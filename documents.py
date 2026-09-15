"""One reader for every one-order document type — Focused Build and ChaRM alike.

Requirements, work packages, work items, defects, defect corrections, test
requests, requests for change, normal/urgent changes, incidents, problems: they
are all CRM one-order documents, and everything below works the same way for each.

WHERE EACH PIECE COMES FROM (all verified live, 16-Sep-2026)
-------------------------------------------------------------
header, current status  WORKSPACESET (OData) — any ProcessType
partners                WORKSPACESET(...)/BTPARTNERSet — function, BP number, name
related documents       WORKSPACESET(...)/BT_RELATEDTRANSSet — requirement<->WP, WP<->WI,
                        defect<->correction, RFC<->change; with relation and status
work items              WORKSPACESET(...)/BTSCOPESet
transports              WORKSPACESET(...)/BT_TRANSPORTREQSet (changes, corrections)
test cases              WORKSPACESET(...)/BTTESTCASESet (defects)
available actions       WORKSPACESET(...)/PPF_ACTIONSet
status HISTORY          CRM_JCDS via RFC — who moved it, when, to what. OData has no
                        history at all; this is the only source.
document flow           CRMD_BRELVONAE via RFC — OBJKEY is CHAR, so the related GUIDs
                        come back in full and resolve to ids and types
text notes              STXH via RFC — id, size, author, dates (no content)
people                  BUT000 via RFC for BP names; BAPI_USER_GET_DETAIL for SAP users

What does NOT work and why: CRMD_LINK -> CRMD_PARTNER through tables (RAW GUIDs
truncate on output), BTTEXTSET (inert), DETAILSET/BT_ITPPMSet navigations (404).
"""
from __future__ import annotations

import re

import config
import rfc
from client import SolmanError, client_for
from workspaces import _redash, _ptype, get_workspace

_HDR = ("ObjectId", "Guid", "ProcessType", "ProcessTypeTxt", "Description", "Status",
        "Concatstatuser", "PriorityTxt", "BrancheId", "PersonRespList", "ServiceTeamList",
        "CreatedBy", "ChangedBy", "CreatedAt", "ChangedAt", "WsUrl")

_GUID_RE = re.compile(r"^[0-9A-Fa-f]{32}$")


def _nodash(g: str) -> str:
    return (g or "").replace("-", "").upper()


def _resolve(ref: str, process_type: str = "") -> dict:
    """Header for an ObjectId or GUID. Process type is needed for an ObjectId
    lookup only when the caller does not know it; then the document table tells us."""
    ref = ref.strip()
    if _GUID_RE.match(ref.replace("-", "")) and not process_type:
        hit = rfc.read_table("CRMD_ORDERADM_H", ["OBJECT_ID", "PROCESS_TYPE"],
                             where=f"GUID = '{_nodash(ref)}'", rowcount=1)
        if not hit:
            raise SolmanError(f"no document with GUID {ref}")
        process_type = hit[0]["PROCESS_TYPE"]
    if not process_type:
        hit = rfc.read_table("CRMD_ORDERADM_H", ["PROCESS_TYPE"],
                             where=f"OBJECT_ID = '{rfc._sql(ref)}'", rowcount=2)
        if not hit:
            raise SolmanError(f"no document with id {ref}")
        if len(hit) > 1:
            raise SolmanError(f"id {ref} exists in more than one type: "
                              f"{sorted(h['PROCESS_TYPE'] for h in hit)} — pass process_type")
        process_type = hit[0]["PROCESS_TYPE"]
    guid = _guid_for(ref, process_type)
    hdr = get_workspace(guid, process_type) if guid else get_workspace(ref, process_type)
    if not hdr:
        raise SolmanError(f"{process_type} {ref}: header not readable through WORKSPACESET")
    return hdr


def _guid_for(ref: str, process_type: str) -> str | None:
    """ObjectId -> GUID by whatever route is reliable for the type.

    WORKSPACESET's ObjectId filter is applied AFTER the gateway has cut the first
    page, so it only finds recently changed documents (measured: 2000007740 found,
    2000007100 and every ZMMJ not). Requirements have a real filter on
    REQUIREMENTSet. Everything else goes through _guid_via_tables.
    """
    if _GUID_RE.match(ref.replace("-", "")):
        return _nodash(ref)
    if process_type == config.REQUIREMENT_PROCESS_TYPE:
        rows = client_for(config.SVC_BIZ_REQ).results(
            "REQUIREMENTSet", {"$filter": f"RequirementId eq '{rfc._sql(ref)}'"})
        if rows and rows[0].get("RequirementGuid"):
            return _nodash(rows[0]["RequirementGuid"])
    g = _guid_via_tables(ref, process_type)          # reliable for every type and age
    if g:
        return g
    hit = get_workspace(ref, process_type)           # last resort: recent documents only
    return _nodash(hit["Guid"]) if hit and hit.get("Guid") else None


def _guid_via_tables(ref: str, process_type: str) -> str | None:
    """ObjectId -> full GUID straight from the document table. Works for every
    type and every age, because read_table returns RAW GUIDs whole (ET_DATA)."""
    where = f"OBJECT_ID = '{rfc._sql(ref)}'" + (f" AND PROCESS_TYPE = '{rfc._sql(process_type)}'" if process_type else "")
    hit = rfc.read_table("CRMD_ORDERADM_H", ["GUID"], where=where, rowcount=1)
    g = (hit[0].get("GUID") or "").strip().upper() if hit else ""
    return g if _GUID_RE.match(g) else None


def _nav(guid: str, process_type: str, nav: str) -> list[dict]:
    key = f"WORKSPACESET(Guid=guid'{_redash(guid)}',ProcessType='{process_type}')/{nav}"
    try:
        return client_for(config.SVC_GENERIC).results(key)
    except SolmanError as ex:
        if "404" in str(ex):
            return []
        raise


def _clean(row: dict, drop_guids: bool = True) -> dict:
    return {k: v for k, v in row.items()
            if k != "__metadata" and v not in (None, "", 0, False)
            and not (drop_guids and k.endswith("Guid"))}


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------
def status_history(guid: str, process_type: str) -> list[dict]:
    """Every status change with who and when, oldest first. OData cannot do this."""
    g = _nodash(guid)
    rows = rfc.read_table("CRM_JCDS", ["STAT", "INACT", "UDATE", "UTIME", "USNAM", "CHIND"],
                          where=f"OBJNR = '{g}'", rowcount=400)
    codes = sorted({r["STAT"] for r in rows if r["STAT"].startswith("E")})
    names = status_texts(process_type, codes)
    out = []
    for r in sorted(rows, key=lambda x: (x["UDATE"], x["UTIME"])):
        if r["INACT"]:              # the row that deactivated a status; the activation row says it all
            continue
        out.append({"status": r["STAT"],
                    "name": names.get(r["STAT"]) or _system_status(r["STAT"]),
                    "by": r["USNAM"], "date": r["UDATE"], "time": r["UTIME"]})
    return out


def status_texts(process_type: str, codes: list[str]) -> dict[str, str]:
    """E-status code -> name for a type's status schema (S1IT -> S1ITHEAD, ZMMJ -> ZMMJHEAD)."""
    if not codes:
        return {}
    where = " OR ".join(f"ESTAT = '{c}'" for c in codes)
    rows = rfc.read_table("TJ30T", ["STSMA", "ESTAT", "TXT30"],
                          where=f"SPRAS = 'E' AND ( {where} )", rowcount=400)
    schema = f"{process_type}HEAD"
    by_code: dict[str, str] = {}
    for r in rows:
        if r["STSMA"] == schema:
            by_code[r["ESTAT"]] = r["TXT30"]
    for r in rows:                  # fall back to any schema for codes the exact one lacks
        by_code.setdefault(r["ESTAT"], r["TXT30"])
    return by_code


_SYS = {"I1002": "Open", "I1003": "In Process", "I1004": "Released", "I1005": "Completed"}


def _system_status(code: str) -> str:
    return _SYS.get(code, code)


# --------------------------------------------------------------------------
# people
# --------------------------------------------------------------------------
def bp_names(partner_nos: list[str]) -> dict[str, str]:
    nos = sorted({n.strip() for n in partner_nos if n and n.strip()})
    if not nos:
        return {}
    out: dict[str, str] = {}
    for i in range(0, len(nos), 8):   # keep the WHERE under a few OPTIONS rows
        where = " OR ".join(f"PARTNER = '{n.zfill(10)}'" for n in nos[i:i + 8])
        for r in rfc.read_table("BUT000", ["PARTNER", "NAME_FIRST", "NAME_LAST", "NAME_ORG1", "TYPE"],
                                where=where, rowcount=50):
            name = (f"{r['NAME_FIRST']} {r['NAME_LAST']}".strip() if r["TYPE"] == "1"
                    else r["NAME_ORG1"] or f"{r['NAME_FIRST']} {r['NAME_LAST']}".strip())
            out[r["PARTNER"].lstrip("0")] = name
    return out


def partners(guid: str, process_type: str) -> list[dict]:
    rows = _nav(guid, process_type, "BTPARTNERSet")
    names = bp_names([r.get("PartnerNo") or r.get("RefPartnerNo") or "" for r in rows])
    out = []
    for r in rows:
        no = (r.get("PartnerNo") or r.get("RefPartnerNo") or "").lstrip("0")
        out.append({"function": r.get("PartnerFct") or r.get("RefPartnerFct"),
                    "function_name": r.get("PartnPftDescr") or "",
                    "partner_no": no, "name": names.get(no, ""),
                    "main": bool(r.get("Mainpartner"))})
    return out


# --------------------------------------------------------------------------
# relationships
# --------------------------------------------------------------------------
def related(guid: str, process_type: str) -> list[dict]:
    """Linked documents with the relation direction and their status — requirement
    <-> work package, work package <-> work item, defect <-> correction, RFC <-> change."""
    return [{"id": r.get("ObjectId"), "type": r.get("WsType"), "relation": r.get("Relation"),
             "status": r.get("StatusId"), "description": r.get("Description"),
             "created_by": r.get("CreatedBy")}
            for r in _nav(guid, process_type, "BT_RELATEDTRANSSet")]


def document_flow(guid: str) -> list[dict]:
    """Document-flow links from the relationship table, resolved to ids and types.
    Both directions. OBJKEY is CHAR, so the GUIDs arrive whole and resolve."""
    g = _nodash(guid)
    links: list[tuple[str, str]] = []
    for side, other in (("A", "B"), ("B", "A")):
        rows = rfc.read_table("CRMD_BRELVONAE", ["BRELTYP", f"OBJKEY_{other}_SEL"],
                              where=f"OBJGUID_{side}_SEL = '{g}'", rowcount=200)
        for r in rows:
            links.append(("successor" if side == "A" else "predecessor", r[f"OBJKEY_{other}_SEL"]))
    out = []
    for direction, key in links:
        key = key.strip().upper()
        if not _GUID_RE.match(key):
            continue
        hit = rfc.read_table("CRMD_ORDERADM_H", ["OBJECT_ID", "PROCESS_TYPE", "DESCRIPTION"],
                             where=f"GUID = '{key}'", rowcount=1)
        if hit:
            out.append({"direction": direction, "id": hit[0]["OBJECT_ID"],
                        "process_type": hit[0]["PROCESS_TYPE"],
                        "type_name": rfc.type_names().get(hit[0]["PROCESS_TYPE"], ""),
                        "description": hit[0]["DESCRIPTION"], "guid": key})
    return out


# --------------------------------------------------------------------------
# the one call
# --------------------------------------------------------------------------
def describe(ref: str, process_type: str = "", include_history: bool = True,
             include_flow: bool = True) -> dict:
    """Everything about one document of any type, by ObjectId or GUID.

        describe("2000007715")                 # a work package
        describe("8000005958")                 # a defect
        describe("1000044703", "S1BR")         # a requirement (type known -> one call fewer)
        describe("DBBD843BAFA21FD1AC8845E443B68DF0")   # by GUID, type looked up
    """
    hdr = _resolve(ref, process_type)
    pt = hdr["ProcessType"]
    g = hdr["Guid"]
    out = {
        "id": hdr.get("ObjectId"), "guid": _nodash(g), "process_type": pt,
        "type_name": hdr.get("ProcessTypeTxt") or rfc.type_names().get(pt, ""),
        "description": hdr.get("Description"),
        "status": {"id": hdr.get("Status"), "name": hdr.get("Concatstatuser")},
        "priority": hdr.get("PriorityTxt"),
        "created_by": hdr.get("CreatedBy"), "changed_by": hdr.get("ChangedBy"),
        "branch_id": hdr.get("BrancheId"),
        "url": (config.BASE_URL + hdr["WsUrl"]) if hdr.get("WsUrl") else None,
    }
    out["partners"] = partners(g, pt)
    out["related"] = related(g, pt)
    items = _nav(g, pt, "BTSCOPESet")
    if items:
        out["work_items"] = [{"type": i.get("WpType"), "description": i.get("WpDescription"),
                              "status": i.get("WpStatus"), "wricef": i.get("Wricef"),
                              "component": i.get("ConfigItem"), "system": i.get("WpSystem"),
                              "scope": i.get("WpScope")} for i in items]
    tr = _nav(g, pt, "BT_TRANSPORTREQSet")
    if tr:
        out["transports"] = [_clean(t) for t in tr]
    tc = _nav(g, pt, "BTTESTCASESet")
    if tc:
        out["test_cases"] = [_clean(t) for t in tc]
    acts = _nav(g, pt, "PPF_ACTIONSet")
    out["available_actions"] = [{"id": a.get("ActionId"), "name": a.get("ActionDesc")} for a in acts]
    try:
        out["texts"] = rfc.texts(g)
    except Exception as ex:  # noqa: BLE001 - texts are secondary
        out["texts_error"] = str(ex)[:160]
    if include_history:
        try:
            out["status_history"] = status_history(g, pt)
        except Exception as ex:  # noqa: BLE001
            out["status_history_error"] = str(ex)[:160]
    if include_flow:
        try:
            out["document_flow"] = document_flow(g)
        except Exception as ex:  # noqa: BLE001
            out["document_flow_error"] = str(ex)[:160]
    return out


def find(process_type: str = "", text: str = "", max_rows: int = 500) -> list[dict]:
    """Complete enumeration by type and title substring — defects, changes, RFCs,
    incidents, anything. See rfc.documents for why OData cannot do this."""
    return rfc.documents(process_type=process_type, description_like=text, max_rows=max_rows)


def actions(ref: str, process_type: str = "") -> list[dict]:
    """Lifecycle actions available right now on any document."""
    hdr = _resolve(ref, process_type)
    return [{"id": a.get("ActionId"), "name": a.get("ActionDesc")}
            for a in _nav(hdr["Guid"], hdr["ProcessType"], "PPF_ACTIONSet")]

"""RFC-over-SOAP transport for SolMan — the read path OData cannot give you.

WHY THIS EXISTS
---------------
The SALM OData gateway on a Focused Build box cannot enumerate. Measured on the
Gore system: ``$filter`` is silently ignored on several fields, ``$skip`` repeats
the first page, ``$top`` caps at 100, and there is no service catalog. So
"give me every work package on this release" is unanswerable through OData, and
every audit built on it has a blind spot shaped exactly like the thing it is
looking for.

``/sap/bc/soap/rfc`` is usually open on these systems and accepts the SAME session
cookie the OData client already holds. A bare GET answering **415** (rather than
403/404) is the tell that it is live and authenticated. Through it,
``RFC_READ_TABLE`` reads ``CRMD_ORDERADM_H`` directly, which lists every one-order
document — Focused Build (S1BR/S1IT/S1MJ/S1CG/S1DM/S1MT) *and* ChaRM
(ZMCR/ZMMJ/ZMSG/ZMHF) — by process type, complete.

SCOPE AND SAFETY
----------------
Read-only by construction. Only function modules on ``_ALLOWED`` may be called and
every one of them is a reader; there is no generic "call any FM" here, because
this transport bypasses the OData layer's own guards. Writes still go through
client.py so they are journalled and subject to the read-only kill switch.

Authorisation is separate from availability: ``RFC_READ_TABLE`` works on the Gore
system while ``READ_TEXT`` returns "RFC Authority Error" — ``S_RFC`` is scoped per
function group. ``probe()`` reports what this user can actually call.
"""
from __future__ import annotations

import re
from xml.sax.saxutils import escape as _xml_escape

import config
from client import SessionExpired, SolmanError, client_for

ENDPOINT = "/sap/bc/soap/rfc"
_NS = ('xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
       'xmlns:urn="urn:sap-com:document:sap:rfc:functions"')

# Every FM callable here. All readers. Keep it that way: this transport has no
# journal and no read-only guard of its own, so a mutating FM must not be added.
_ALLOWED = frozenset({
    "RFC_PING", "STFC_CONNECTION", "RFC_SYSTEM_INFO",
    "RFC_READ_TABLE", "RFC_GET_TABLE_ENTRIES",
    "RFC_GET_FUNCTION_INTERFACE", "RFC_FUNCTION_SEARCH",
    "DDIF_FIELDINFO_GET",
    "BAPI_USER_GET_DETAIL",
})
# Proven NOT callable for this user (S_RFC): READ_TEXT, SAVE_TEXT, CRM_ORDER_READ,
# RPY_TABLE_READ, BBP_RFC_READ_TABLE, BAPI_HELPVALUES_GET, TH_USER_LIST,
# SUSR_USER_AUTH_FOR_OBJ_GET, SO_USER_READ_API1, SICF_SERVICE_TREE.

# OPTIONS-TEXT in RFC_READ_TABLE is CHAR(72). A longer WHERE has to be split
# across rows, and split at a token boundary or the kernel parses a half literal.
_OPTION_WIDTH = 72

# RAW columns and RFC_READ_TABLE. The classic DATA/WA return renders a RAW(16)
# GUID as its first 16 hex digits — a prefix shared across the whole system — so
# every GUID-keyed join was impossible and a LIKE on the prefix matched other
# documents' rows. read_table() requests the ET_DATA return path instead, which
# renders the full 32 digits. Keep the notes below as the reason, not as a rule:
# with ET_DATA, GUID joins between CRMD_ORDERADM_H, CRMD_LINK, CRM_JEST, STXH and
# CRMD_BRELVONAE all work. If a future kernel drops ET_DATA the symptom is 16-char
# GUIDs again; _parse_rows falls back to WA and the joins start failing.
RAW_TRUNCATION = ("legacy WA output truncates RAW columns to half their hex length; "
                  "read_table uses ET_DATA which does not")
_TRUNCATION_WARNINGS = {
    "CRMD_ORDERADM_H": "GUID is RAW(16); full through ET_DATA (" + RAW_TRUNCATION + ").",
    "CRMD_LINK": "GUID_HI/GUID_SET are RAW(16); full through ET_DATA. OData navigations "
                 "(BTPARTNERSet, BTDATESSet…) remain the simpler route for partner/date sets.",
    "STXL": ("text lines live in the compressed CLUSTD cluster and cannot be read this way; "
             "STXH gives you TDID/TDTXTLINES/TDFUSER, which is enough to audit presence."),
}


class RfcUnavailable(RuntimeError):
    """The SOAP RFC endpoint is not usable on this system/session."""


class RfcAuthority(RuntimeError):
    """The FM exists but S_RFC does not cover its function group."""


def _envelope(fm: str, inner: str) -> bytes:
    return ('<?xml version="1.0" encoding="utf-8"?>'
            f'<soapenv:Envelope {_NS}><soapenv:Header/><soapenv:Body>'
            f'<urn:{fm}>{inner}</urn:{fm}>'
            '</soapenv:Body></soapenv:Envelope>').encode("utf-8")


def _call(fm: str, inner: str, _recovered: bool = False) -> str:
    """POST one RFC envelope. Shares the OData client's session and its self-healing:
    an expired session is re-minted once and the call retried, the same way
    client.get() does it, so a long table-driven read survives the cookie clock."""
    try:
        return _call_once(fm, inner)
    except SessionExpired:
        if _recovered:
            raise
        import hardening
        if not hardening.auto_refresh_enabled():
            raise
        outcome = hardening.refresh_session()
        if not outcome.get("refreshed"):
            raise SessionExpired("session expired and the automatic refresh did not succeed: "
                                 f"{outcome.get('reason')}")
        client_for(config.SVC_GENERIC)._reload_cookies()
        return _call(fm, inner, _recovered=True)


def _call_once(fm: str, inner: str) -> str:
    if fm not in _ALLOWED:
        raise ValueError(
            f"{fm} is not on the RFC allowlist {sorted(_ALLOWED)}. This transport is "
            "read-only on purpose — route writes through the OData client so they are "
            "journalled and respect SOLMAN_READONLY."
        )
    http = client_for(config.SVC_GENERIC)._http
    r = http.post(ENDPOINT, content=_envelope(fm, inner),
                  headers={"Content-Type": "text/xml; charset=utf-8",
                           "SOAPAction": f'"urn:sap-com:document:sap:rfc:functions:{fm}"'})
    if "text/html" in r.headers.get("content-type", "").lower():
        # HTML from the SOAP endpoint is either the SAML login page (session gone)
        # or an ICF error page (the call itself failed). Telling them apart matters:
        # the first is recoverable by a refresh, the second is not and a refresh
        # would be a wasted browser launch. Measured 16-Sep: a CRMD_PARTNER read
        # came back as an HTML error page while the session was perfectly valid.
        low = r.text[:4000].lower()
        if any(k in low for k in ("logon", "log on", "saml", "login", "sign in", "authentication")):
            raise SessionExpired("SolMan session expired (login page from /sap/bc/soap/rfc). "
                                 "Run: python refresh_session.py")
        title = re.search(r"<title>(.*?)</title>", r.text, re.S | re.I)
        raise SolmanError(f"{fm}: the RFC endpoint returned an HTML error page "
                          f"(HTTP {r.status_code}: {title.group(1).strip()[:120] if title else r.text[:160]!r})")
    if r.status_code == 415:
        raise RfcUnavailable("the endpoint rejected the SOAP envelope (415)")
    if r.status_code in (401, 403):
        raise RfcUnavailable(f"HTTP {r.status_code} from {ENDPOINT} — the endpoint or this "
                             "user's RFC authority is closed")
    body = r.text
    fault = re.search(r"<faultstring[^>]*>(.*?)</faultstring>", body, re.S)
    if fault:
        msg = re.sub(r"\s+", " ", fault.group(1)).strip()
        if "authority" in msg.lower():
            raise RfcAuthority(f"{fm}: {msg} — S_RFC does not cover this function group")
        raise SolmanError(f"{fm}: {msg}")
    if r.status_code >= 400:
        raise SolmanError(f"{fm} -> HTTP {r.status_code}: {body[:300]}")
    return body


def _split_where(where: str) -> list[str]:
    """Break a WHERE clause into <=72-char rows at token boundaries."""
    if not where:
        return []
    rows, current = [], ""
    for token in where.split(" "):
        if len(token) > _OPTION_WIDTH:
            raise ValueError(f"WHERE token longer than {_OPTION_WIDTH} chars cannot be sent: "
                             f"{token[:40]}...")
        candidate = token if not current else f"{current} {token}"
        if len(candidate) <= _OPTION_WIDTH:
            current = candidate
        else:
            rows.append(current)
            current = token
    if current:
        rows.append(current)
    return rows


def ping() -> bool:
    """True if the endpoint answers RFC_PING with this session."""
    _call("RFC_PING", "")
    return True


def probe() -> dict:
    """What this transport can actually do here. Safe to call anywhere."""
    out: dict = {"endpoint": config.BASE_URL + ENDPOINT, "allowlist": sorted(_ALLOWED)}
    try:
        out["ping"] = ping()
    except Exception as ex:  # noqa: BLE001
        out["ping"] = False
        out["reason"] = f"{type(ex).__name__}: {ex}"
        return out
    try:
        read_table("T000", ["MANDT"], rowcount=1)
        out["read_table"] = True
    except Exception as ex:  # noqa: BLE001
        out["read_table"] = False
        out["read_table_reason"] = f"{type(ex).__name__}: {ex}"
    return out


def read_table(table: str, fields: list[str] | None = None, where: str = "",
               rowcount: int = 100, skip: int = 0,
               delimiter: str = "|") -> list[dict]:
    """Read a table through RFC_READ_TABLE. Returns a list of field->value dicts.

    fields   explicit column list. Strongly recommended: RFC_READ_TABLE builds a
             fixed 512-byte row and a wide table raises DATA_BUFFER_EXCEEDED.
             Omitting it asks for every column and will fail on most CRM tables.
    where    plain ABAP-style SQL, e.g. "PROCESS_TYPE = 'S1IT' AND DESCRIPTION
             LIKE '%GMR6%'". Split across OPTIONS rows automatically.
    rowcount 0 means "no limit" to the kernel; keep a real ceiling instead.

    Values are returned as the kernel renders them — space-padded and, for RAW
    columns, possibly truncated. See _TRUNCATION_WARNINGS.
    """
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_/]{0,29}", table):
        raise ValueError(f"implausible table name: {table!r}")
    fields = [f.strip().upper() for f in (fields or []) if f.strip()]
    field_xml = "".join(f"<item><FIELDNAME>{_xml_escape(f)}</FIELDNAME></item>" for f in fields)
    option_xml = "".join(f"<item><TEXT>{_xml_escape(row)}</TEXT></item>"
                         for row in _split_where(where))
    # USE_ET_DATA_4_RETURN is the whole reason RAW columns work here. The classic
    # DATA/WA output renders a RAW(16) GUID as 16 characters — a prefix shared across
    # the system — which made every GUID-keyed join impossible. The ET_DATA return
    # path (SAP note 2246160) renders the full 32 hex digits and has a wider line.
    # Measured 16-Sep-2026: WA -> 'DBBD843BAFA21FD1', ET_DATA -> the full GUID.
    inner = (f"<DELIMITER>{_xml_escape(delimiter)}</DELIMITER>"
             f"<NO_DATA></NO_DATA>"
             f"<QUERY_TABLE>{_xml_escape(table.upper())}</QUERY_TABLE>"
             f"<ROWCOUNT>{int(rowcount)}</ROWCOUNT>"
             f"<ROWSKIPS>{int(skip)}</ROWSKIPS>"
             f"<USE_ET_DATA_4_RETURN>X</USE_ET_DATA_4_RETURN>"
             f"<DATA></DATA><ET_DATA></ET_DATA>"
             f"<FIELDS>{field_xml}</FIELDS><OPTIONS>{option_xml}</OPTIONS>")
    body = _call("RFC_READ_TABLE", inner)
    return _parse_rows(body, fields, delimiter)


def _parse_rows(body: str, fields: list[str], delimiter: str = "|") -> list[dict]:
    """Rows out of an RFC_READ_TABLE response: ET_DATA lines preferred, WA fallback.
    Column names come from the echoed FIELDS table (authoritative order), else the
    caller's list."""
    names = [n.strip() for n in re.findall(r"<FIELDNAME>(.*?)</FIELDNAME>", body, re.S)] or list(fields)
    raw_rows = re.findall(r"<LINE>(.*?)</LINE>", body, re.S) or re.findall(r"<WA>(.*?)</WA>", body, re.S)
    rows = []
    for raw in raw_rows:
        parts = [p.strip() for p in _unescape(raw).split(delimiter)]
        rows.append(dict(zip(names, parts)) if names else {"LINE": raw})
    return rows


def _unescape(s: str) -> str:
    return (s.replace("&lt;", "<").replace("&gt;", ">")
             .replace("&quot;", '"').replace("&#39;", "'")
             .replace("&#38;", "&").replace("&amp;", "&"))


def read_table_all(table: str, fields: list[str], where: str = "",
                   page: int = 500, max_rows: int = 20000) -> list[dict]:
    """Page a table with ROWSKIPS, stopping on a short page or at max_rows.

    Unlike the OData gateway's ``$skip`` (which repeats page one), ROWSKIPS is
    honoured — but the page is still verified to advance, so a future regression
    surfaces as a short read rather than silent duplication.
    """
    out: list[dict] = []
    seen_first: set[str] = set()
    skip = 0
    while len(out) < max_rows:
        batch = read_table(table, fields, where, rowcount=page, skip=skip)
        if not batch:
            break
        marker = "|".join(batch[0].get(f, "") for f in fields)
        if marker in seen_first:
            break                      # paging is not advancing — stop, do not duplicate
        seen_first.add(marker)
        out.extend(batch)
        if len(batch) < page:
            break
        skip += page
    return out[:max_rows]


def truncation_note(table: str) -> str | None:
    return _TRUNCATION_WARNINGS.get(table.upper())


# --------------------------------------------------------------------------
# Cross-object inventory: Focused Build AND ChaRM, complete
# --------------------------------------------------------------------------
_H = "CRMD_ORDERADM_H"

# Type names are READ FROM THE SYSTEM, not hardcoded. An earlier draft of this
# module carried a static map and got several labels wrong (ZMSG is "Gore Standard
# Change", not a service message; S1CG is "Work Item (GC)"). The system knows;
# ask it once and cache.
_TYPE_NAMES: dict[str, str] | None = None

# Only used to label a type when the system lookup itself fails.
_FALLBACK_NAMES = {
    "S1BR": "Requirement", "S1IT": "Work Package", "S1MJ": "Work Item (NC)",
    "S1CG": "Work Item (GC)", "S1DM": "Defect", "S1MT": "Master Work Package",
}


def type_names() -> dict[str, str]:
    """process_type -> name, read once from the system and cached."""
    global _TYPE_NAMES
    if _TYPE_NAMES is None:
        try:
            _TYPE_NAMES = {p["process_type"]: p["name"] for p in process_types()}
        except Exception:  # noqa: BLE001
            _TYPE_NAMES = dict(_FALLBACK_NAMES)
    return _TYPE_NAMES


def focused_build_types() -> list[dict]:
    """The Focused Build (S1*) and ChaRM/Gore (ZM*) one-order types on this system.

    This is the answer to "what object types can I work with" — requirements,
    work packages, work items, defects, defect corrections, test requests, tasks,
    releases, scope changes, plus the ChaRM side: requests for change, normal and
    urgent changes, incidents, problems, service requests.
    """
    return [{"process_type": k, "name": v}
            for k, v in sorted(type_names().items())
            if k.startswith(("S1", "ZM"))]


def process_types() -> list[dict]:
    """Every one-order process type on the system, from CRMC_PROC_TYPE_T.

    Wider than OData's CRMDOCTYPESet, which returns only the ten Focused Build
    types and no ChaRM ones. Note the description column is P_DESCRIPTION, not
    DESCRIPTION — describe_table() is how to check that kind of thing before
    guessing.
    """
    rows = read_table("CRMC_PROC_TYPE_T", ["PROCESS_TYPE", "P_DESCRIPTION"],
                      where="LANGU = 'E'", rowcount=400)
    return sorted(({"process_type": r.get("PROCESS_TYPE"), "name": r.get("P_DESCRIPTION")}
                   for r in rows if r.get("PROCESS_TYPE")),
                  key=lambda x: x["process_type"])


def documents(process_type: str = "", description_like: str = "",
              object_id_like: str = "", max_rows: int = 5000) -> list[dict]:
    """Enumerate one-order documents — the read the OData gateway cannot do.

    Any Focused Build or ChaRM type: requirements, work packages, work items,
    defects, RFCs, changes. Filters are ANDed; all are optional, but an unfiltered
    call on a live system is large, so max_rows applies.

        documents(process_type="S1IT", description_like="GMR6")
        documents(process_type="S1DM")                      # every defect
        documents(object_id_like="20000077")                 # a number range
    """
    clauses = []
    if process_type:
        clauses.append(f"PROCESS_TYPE = '{_sql(process_type.upper())}'")
    if description_like:
        clauses.append(f"DESCRIPTION LIKE '%{_sql(description_like)}%'")
    if object_id_like:
        clauses.append(f"OBJECT_ID LIKE '{_sql(object_id_like)}%'")
    rows = read_table_all(_H, ["OBJECT_ID", "PROCESS_TYPE", "DESCRIPTION",
                               "CREATED_AT", "CREATED_BY"],
                          where=" AND ".join(clauses), max_rows=max_rows)
    return [{"id": r.get("OBJECT_ID"), "process_type": r.get("PROCESS_TYPE"),
             "type_name": type_names().get(r.get("PROCESS_TYPE", ""), ""),
             "description": r.get("DESCRIPTION"),
             "created_at": r.get("CREATED_AT"), "created_by": r.get("CREATED_BY")}
            for r in rows]


def inventory(description_like: str = "", max_rows: int = 20000) -> dict:
    """Count documents per process type, optionally scoped to a release/title.

    The one call that answers "what exists for this release, across every object
    type" — which is the question a release audit has to start from.
    """
    where = f"DESCRIPTION LIKE '%{_sql(description_like)}%'" if description_like else ""
    rows = read_table_all(_H, ["PROCESS_TYPE", "OBJECT_ID"], where=where, max_rows=max_rows)
    counts: dict[str, int] = {}
    for r in rows:
        pt = r.get("PROCESS_TYPE", "")
        counts[pt] = counts.get(pt, 0) + 1
    return {"filter": description_like or "(all)", "total": len(rows),
            "by_type": {k: {"count": v, "name": type_names().get(k, "")}
                        for k, v in sorted(counts.items(), key=lambda x: -x[1])},
            "truncated": len(rows) >= max_rows}


def describe_table(table: str) -> list[dict]:
    """Field list for any table, from DD03L — so a caller can pick columns safely."""
    rows = read_table("DD03L", ["FIELDNAME", "POSITION", "DATATYPE", "LENG", "KEYFLAG"],
                      where=f"TABNAME = '{_sql(table.upper())}'", rowcount=400)
    fields = [{"field": r.get("FIELDNAME"), "position": r.get("POSITION"),
               "type": r.get("DATATYPE"), "length": r.get("LENG"),
               "key": r.get("KEYFLAG") == "X"} for r in rows
              if r.get("FIELDNAME") and not r.get("FIELDNAME", "").startswith(".")]
    return sorted(fields, key=lambda f: int(f["position"] or 0))


def texts(document_guid: str, max_rows: int = 200) -> list[dict]:
    """Every text note on a one-order document, from STXH.

    Needs the FULL 32-character GUID (take it from OData — see
    _TRUNCATION_WARNINGS). Content is not readable (READ_TEXT is authority-
    blocked and STXL is compressed), but id, length, author and dates are, which
    is enough to audit whether a required note is present and who wrote it.
    """
    g = document_guid.replace("-", "").upper()
    if len(g) != 32:
        raise ValueError("need the full 32-character document GUID; a truncated "
                         "CRMD_ORDERADM_H GUID prefix matches other documents")
    rows = read_table("STXH", ["TDID", "TDTXTLINES", "TDFUSER", "TDFDATE",
                               "TDLUSER", "TDLDATE", "TDSPRAS"],
                      where=f"TDOBJECT = 'CRM_ORDERH' AND TDNAME LIKE '{g}%'",
                      rowcount=max_rows)
    return [{"text_id": r.get("TDID"), "lines": int(r.get("TDTXTLINES") or 0),
             "created_by": r.get("TDFUSER"), "created_on": r.get("TDFDATE"),
             "changed_by": r.get("TDLUSER"), "changed_on": r.get("TDLDATE")}
            for r in rows]


def text_types(object_type: str = "CRM_ORDERH") -> list[dict]:
    """Text-id catalogue (TTXIT) — what CR05, S112 and friends actually mean."""
    rows = read_table("TTXIT", ["TDID", "TDTEXT"],
                      where=f"TDOBJECT = '{_sql(object_type)}' AND TDSPRAS = 'E'",
                      rowcount=300)
    return [{"text_id": r.get("TDID"), "name": r.get("TDTEXT")} for r in rows if r.get("TDID")]


def _sql(value: str) -> str:
    """Escape a value for an ABAP SQL literal inside OPTIONS (doubles quotes).

    Also rejects the characters that would let a caller close the literal and
    append a clause, since OPTIONS is raw SQL, not a parameterised query.
    """
    v = str(value)
    if any(ch in v for ch in ("\n", "\r", "\t")):
        raise ValueError("newlines are not allowed in an RFC_READ_TABLE literal")
    return v.replace("'", "''")


# --------------------------------------------------------------------------
# System, people and function discovery (all proven callable 16-Sep-2026)
# --------------------------------------------------------------------------
def system_info() -> dict:
    """SID, host, DB, release — RFC_SYSTEM_INFO. Cheap liveness + identity check."""
    body = _call("RFC_SYSTEM_INFO", "")
    out = {}
    for tag in ("RFCSYSID", "RFCHOST", "RFCDBSYS", "RFCDBHOST", "RFCSAPRL", "RFCKERNRL", "RFCMACH"):
        m = re.search(rf"<{tag}>(.*?)</{tag}>", body)
        if m:
            out[tag.replace("RFC", "").lower()] = m.group(1)
    return out


def user_detail(user_id: str) -> dict:
    """Resolve an SAP user id (TDFUSER, USNAM, CreatedBy…) to a person — BAPI_USER_GET_DETAIL."""
    uid = re.sub(r"[^A-Za-z0-9_\-]", "", user_id)[:12].upper()
    body = _call("BAPI_USER_GET_DETAIL", f"<USERNAME>{_xml_escape(uid)}</USERNAME>")
    out = {"user": uid}
    for tag, key in (("FIRSTNAME", "first_name"), ("LASTNAME", "last_name"),
                     ("FULLNAME", "full_name"), ("E_MAIL", "email"),
                     ("DEPARTMENT", "department"), ("FUNCTION", "function"),
                     ("TEL1_NUMBR", "phone")):
        m = re.search(rf"<{tag}>(.*?)</{tag}>", body)
        if m and m.group(1).strip():
            out[key] = _unescape(m.group(1).strip())
    if re.search(r"<TYPE>E</TYPE>", body):
        msg = re.search(r"<MESSAGE>(.*?)</MESSAGE>", body)
        out["error"] = msg.group(1) if msg else "user not found"
    return out


def function_search(pattern: str, group: str = "*", max_rows: int = 200) -> list[dict]:
    """Find RFC-enabled function modules by name pattern — RFC_FUNCTION_SEARCH.
    Discovery only; whether this user may CALL one is a separate S_RFC question,
    answered by trying it (probe pattern) — see the not-callable list in _ALLOWED."""
    pat = re.sub(r"[^A-Za-z0-9_*/]", "", pattern)[:30].upper()
    # SOAP-RFC returns a TABLES parameter only if the request declares it, even
    # empty — omit <FUNCTIONS/> and the search "succeeds" with nothing in it.
    body = _call("RFC_FUNCTION_SEARCH",
                 f"<FUNCNAME>{_xml_escape(pat)}</FUNCNAME><GROUPNAME>{_xml_escape(group)}</GROUPNAME>"
                 f"<LANGUAGE>E</LANGUAGE><FUNCTIONS></FUNCTIONS>")
    out = []
    for item in re.findall(r"<item>(.*?)</item>", body, re.S):
        row = {}
        for tag, key in (("FUNCNAME", "function"), ("GROUPNAME", "group"), ("STEXT", "text")):
            m = re.search(rf"<{tag}>(.*?)</{tag}>", item)
            if m:
                row[key] = _unescape(m.group(1))
        if row:
            out.append(row)
    return out[:max_rows]


def function_interface(function_module: str) -> dict:
    """Parameters of a function module — RFC_GET_FUNCTION_INTERFACE."""
    fm = re.sub(r"[^A-Za-z0-9_/]", "", function_module)[:30].upper()
    body = _call("RFC_GET_FUNCTION_INTERFACE",
                 f"<FUNCNAME>{_xml_escape(fm)}</FUNCNAME><LANGUAGE>E</LANGUAGE><PARAMS></PARAMS>")
    params = []
    for item in re.findall(r"<item>(.*?)</item>", body, re.S):
        row = {}
        for tag, key in (("PARAMCLASS", "class"), ("PARAMETER", "name"), ("TABNAME", "type"),
                         ("FIELDNAME", "field"), ("EXID", "abap_type"), ("OPTIONAL", "optional"),
                         ("DEFAULT", "default"), ("PARAMTEXT", "text")):
            m = re.search(rf"<{tag}>(.*?)</{tag}>", item)
            if m and m.group(1).strip():
                row[key] = _unescape(m.group(1).strip())
        if row.get("name"):
            params.append(row)
    kinds = {"I": "import", "E": "export", "T": "table", "C": "changing", "X": "exception"}
    for p in params:
        p["class"] = kinds.get(p.get("class", ""), p.get("class"))
    return {"function": fm, "parameters": params}


def resolve_guids(guids: list[str]) -> list[dict]:
    """Full 32-char GUIDs -> id / type / description, from the document table."""
    out = []
    for g in guids:
        g = g.replace("-", "").upper()
        if not re.fullmatch(r"[0-9A-F]{32}", g):
            continue
        hit = read_table("CRMD_ORDERADM_H", ["OBJECT_ID", "PROCESS_TYPE", "DESCRIPTION"],
                         where=f"GUID = '{g}'", rowcount=1)
        if hit:
            out.append({"guid": g, "id": hit[0]["OBJECT_ID"], "process_type": hit[0]["PROCESS_TYPE"],
                        "type_name": type_names().get(hit[0]["PROCESS_TYPE"], ""),
                        "description": hit[0]["DESCRIPTION"]})
    return out

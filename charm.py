"""ChaRM / Request-for-Change creation and the work-package text notes.

Both payloads come from the Focused Build generic workspace app's own source
(/sap/bc/ui5_ui5/salm/generic/Component-preload.js), not from guesswork:

Request for Change
    oModel.createEntry("/WS_REQUEST_CHANGESet", {properties: {
        Title, Description, Requester, Priority, Category, ChangManager,
        SoldToParty, ActualRelease  (+ customer fields, e.g. ZZFLD00000B)}})
    with customer fields resolved for ("S1CR", "WS_REQUEST_CHANGE"). TypeId is a
    key of the entity; the app creates Focused Build RFCs (S1CR). Whether the same
    entity accepts a ChaRM type (ZMCR) is a question for the backend — pass it and
    read the created document's ProcessType back.

Text notes
    read : WORKSPACESET(Guid,ProcessType)/BTTEXTSet?$filter=ConfigId eq <n>
           (ConfigId is numeric and MANDATORY — without it the set is empty, which is
           why BTTEXTSET looked inert for two days)
    types: TEXT_TYPEF4SET?$filter=ConfigId eq <n>
    write: MERGE BTTEXTSET(WsGuid='<32>',TextType='<type name>',TextTypeId='<id>',
           TextDate='') {"TextValue": ...} INSIDE A $BATCH CHANGESET — the app's model
           runs with useBatch; a direct MERGE answers 501 "UPDATE_ENTITY not implemented".
    The Work Package app runs with configId 2, which offers S115 Description, S114 Memo,
    S105 Comment and the role comments S108/S109/S112/S113.
"""
from __future__ import annotations

import config
from client import SolmanError, client_for, odata_literal
from workspaces import _redash, get_workspace, list_actions

WP_CONFIG_ID = 2   # the generic app's configId for work packages (URL ?configId=2)


# --------------------------------------------------------------------------
# text notes on any workspace document
# --------------------------------------------------------------------------
def text_types(config_id: int = WP_CONFIG_ID) -> list[dict]:
    rows = client_for(config.SVC_GENERIC).results("TEXT_TYPEF4SET",
                                                  {"$filter": f"ConfigId eq {int(config_id)}"})
    return [{"id": r.get("TypeId"), "name": r.get("TypeName"),
             "changeable": r.get("Changeable")} for r in rows if r.get("TypeId")]


def read_texts(guid: str, process_type: str, config_id: int = WP_CONFIG_ID) -> list[dict]:
    """Every text note on the document WITH content — the read BTTEXTSET refused
    until the ConfigId filter was found in the app source."""
    key = f"WORKSPACESET(Guid=guid'{_redash(guid)}',ProcessType='{process_type}')/BTTEXTSet"
    rows = client_for(config.SVC_GENERIC).results(key, {"$filter": f"ConfigId eq {int(config_id)}"})
    return [{"text_id": r.get("TextTypeId"), "type": r.get("TextType"),
             "value": r.get("TextValue"), "author": r.get("TextSender"),
             "date": r.get("TextDate"), "language": r.get("TextLanguage")} for r in rows]


def write_text(guid: str, text_type_id: str, value: str, process_type: str = "S1IT",
               config_id: int = WP_CONFIG_ID) -> dict:
    """Add a text note (e.g. CR05 'Change Request - Internal') to a document.

    This is how the CCB questionnaire gets onto a work package without Fiori.
    Each note is its own record (TDNAME = guid + timestamp), so this always ADDS;
    nothing is overwritten. Journalled and kill-switchable like every write.
    """
    types = {t["id"]: t["name"] for t in text_types(config_id)}
    if text_type_id not in types:
        raise SolmanError(f"text type {text_type_id!r} is not offered for configId {config_id}; "
                          f"available: {sorted(types)}")
    g = guid.replace("-", "").upper()
    key = (f"BTTEXTSET(WsGuid='{g}',TextType='{odata_literal(types[text_type_id])}',"
           f"TextTypeId='{odata_literal(text_type_id)}',TextDate='')")
    before = len(read_texts(g, process_type, config_id))
    # a direct MERGE answers 501 (no UPDATE_ENTITY); the app saves through a $batch
    # changeset, which the gateway routes to CHANGESET_PROCESS — proven 16-Sep-2026
    client_for(config.SVC_GENERIC).batch_merge(key, {"TextValue": value})
    after = read_texts(g, process_type, config_id)
    mine = [t for t in after if t["text_id"] == text_type_id and (t["value"] or "").strip()[:60] == value.strip()[:60]]
    return {"written": len(after) > before or bool(mine), "text_id": text_type_id,
            "type": types[text_type_id], "notes_before": before, "notes_after": len(after),
            "match": mine[-1] if mine else None}


# --------------------------------------------------------------------------
# Request for Change
# --------------------------------------------------------------------------
def create_request_for_change(title: str, description: str, requester_bp: str,
                              priority: str = "2", category: str = "",
                              change_manager_bp: str = "", sold_to_party_bp: str = "",
                              actual_release: str = "", type_id: str = "S1CR",
                              external_reference: str = "") -> dict:
    """Create a Request for Change through WS_REQUEST_CHANGESet — the generic app's own
    create path. Returns the new document's id, guid, process type and status, read back.

    type_id is passed through but the backend ignores it (the entity is hard-wired to
    the Focused Build RFC type S1CR — the response echoes TypeId "").

    STATUS ON THE GORE SYSTEM (16-Sep-2026): the payload is exactly the app's, the
    changeset POST returns 201, and the sap-message says S1CR is "blocked for further
    business transactions" — disabled in customizing. Gore raises ZMCR requests for
    change in the CRM WebClient; no OData create path for ZMCR was found. This function
    therefore raises with that message rather than returning an empty success.
    """
    body = {"TypeId": type_id, "Title": title[:40], "Description": description,
            "Requester": requester_bp, "Priority": str(priority), "Category": category,
            "ChangManager": change_manager_bp, "SoldToParty": sold_to_party_bp,
            "ActualRelease": actual_release, "ZZFLD00000B": external_reference}
    created = client_for(config.SVC_GENERIC).batch_create("WS_REQUEST_CHANGESet", body)
    guid = (created.get("Guid") or "").replace("-", "").upper()
    if not guid:
        # The service answers 201 with an empty entity and explains itself only in the
        # sap-message header. On the Gore system that message is "Business transaction
        # type Request for Change is blocked for further business transactions": the
        # Focused Build RFC type (S1CR) is switched off in customizing in favour of the
        # ChaRM ZMCR, which is created in the CRM WebClient and has no OData create path.
        raise SolmanError("no Request for Change was created: "
                          + (created.get("__sap_message") or "the service returned an empty entity"))
    out = {"id": created.get("Id"), "guid": guid, "requested_type": type_id,
           "response": {k: v for k, v in created.items() if k != "__metadata"}}
    if guid:
        hdr = get_workspace(guid, created.get("TypeId") or type_id)
        out.update({"process_type": hdr.get("ProcessType"), "status": hdr.get("Concatstatuser"),
                    "description": hdr.get("Description")})
        try:
            out["actions"] = list_actions(guid, hdr.get("ProcessType") or type_id)
        except Exception as ex:  # noqa: BLE001
            out["actions_error"] = str(ex)[:120]
    return out

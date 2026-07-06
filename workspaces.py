"""Generic Focused Build workspace (CRM one-order) operations via CRM_GENERIC_SRV.

Covers ALL object types uniformly — Requirement (S1BR), Work Package (S1IT), Work Item
(S1MJ/S1CG), Defect (S1DM), Defect Correction (S1TM), Request for Change (S1CR), Urgent
Change (S1HF), Risk (S1RK), Master Work Package (S1MT): search, read header, and list /
execute lifecycle PPF actions. Type-specific create + rich edit live in the dedicated
modules (requirements.py, workpackages.py). Everything here is grounded in the
WORKSPACESET+ProcessType and CRM_GENERIC PPF_ACTION patterns verified live.
"""
from __future__ import annotations

import config
from client import SolmanError, client_for, odata_literal

# Focused Build transaction types (CRMDOCTYPESet). list_process_types() reads them live.
PROCESS_TYPES: dict[str, str] = {
    "requirement": "S1BR", "work_package": "S1IT", "master_work_package": "S1MT",
    "work_item_nc": "S1MJ", "work_item_gc": "S1CG", "defect": "S1DM",
    "defect_correction": "S1TM", "request_for_change": "S1CR", "urgent_change": "S1HF",
    "risk": "S1RK",
}
_HEADER = ("ObjectId", "Guid", "ProcessType", "ProcessTypeTxt", "Description", "Status",
           "Concatstat", "Concatstatuser", "PriorityTxt", "BrancheId", "PersonRespList",
           "ServiceTeamList", "CreatedBy")


def _ptype(process_type: str) -> str:
    """Accept a friendly key ('work_package') or a raw code ('S1IT'); return the code."""
    if not process_type:
        raise ValueError(f"process_type required. friendly keys: {list(PROCESS_TYPES)}")
    return PROCESS_TYPES.get(process_type.lower(), process_type.upper())


def _nodash(guid: str) -> str:
    return guid.replace("-", "").upper()


def _redash(guid: str) -> str:
    g = _nodash(guid)
    return f"{g[:8]}-{g[8:12]}-{g[12:16]}-{g[16:20]}-{g[20:32]}"


def _is_guid(s: str) -> bool:
    return len(s.replace("-", "")) == 32 and all(ch in "0123456789ABCDEFabcdef" for ch in s.replace("-", ""))


def search_workspaces(process_type: str, query: str = "", top: int = 25) -> list[dict]:
    """Search any object type by title substring. process_type: friendly key or code."""
    pt = _ptype(process_type)
    flt = f"ProcessType eq '{odata_literal(pt)}'"
    if query:
        flt += f" and substringof('{odata_literal(query)}',Description)"
    rows = client_for(config.SVC_GENERIC).results("WORKSPACESET", {"$filter": flt, "$top": str(top)})
    return [{"id": r.get("ObjectId"), "guid": r.get("Guid"), "type": r.get("ProcessType"),
             "type_text": r.get("ProcessTypeTxt"), "title": r.get("Description"),
             "status": r.get("Status"), "status_text": r.get("Concatstatuser")} for r in rows]


def _resolve_guid(id_or_guid: str, process_type_code: str) -> str:
    """Return the plain (no-dash) GUID for an ObjectId or a GUID in either form."""
    if _is_guid(id_or_guid):
        return _nodash(id_or_guid)
    flt = f"ProcessType eq '{odata_literal(process_type_code)}' and ObjectId eq '{odata_literal(id_or_guid)}'"
    rows = client_for(config.SVC_GENERIC).results("WORKSPACESET", {"$filter": flt, "$top": "1"})
    if not rows:
        raise SolmanError(f"No {process_type_code} workspace with ObjectId '{id_or_guid}'.")
    return _nodash(rows[0].get("Guid", ""))


def get_workspace(id_or_guid: str, process_type: str) -> dict:
    """Read a workspace header (any type) by ObjectId or GUID."""
    pt = _ptype(process_type)
    if _is_guid(id_or_guid):
        flt = f"ProcessType eq '{odata_literal(pt)}' and Guid eq guid'{_redash(id_or_guid)}'"
    else:
        flt = f"ProcessType eq '{odata_literal(pt)}' and ObjectId eq '{odata_literal(id_or_guid)}'"
    rows = client_for(config.SVC_GENERIC).results("WORKSPACESET", {"$filter": flt, "$top": "1"})
    if not rows:
        return {}
    r = rows[0]
    return {k: r.get(k) for k in _HEADER if r.get(k) not in (None, "")}


def list_actions(id_or_guid: str, process_type: str) -> list[dict]:
    """List the lifecycle PPF actions available for any workspace object."""
    pt = _ptype(process_type)
    guid = _resolve_guid(id_or_guid, pt)
    key = f"WORKSPACESET(Guid=guid'{_redash(guid)}',ProcessType='{pt}')/PPF_ACTIONSet"
    rows = client_for(config.SVC_GENERIC).results(key)
    return [{"action_id": r.get("ActionId"), "description": r.get("ActionDesc"),
             "digital_signature": bool(r.get("DigitalSignature"))} for r in rows]


def execute_action(id_or_guid: str, action_id: str, process_type: str) -> dict:
    """Execute a lifecycle PPF action (from list_actions) on any workspace object."""
    pt = _ptype(process_type)
    guid = _resolve_guid(id_or_guid, pt)
    client_for(config.SVC_GENERIC).function(
        "PPF_ACTION", {"ActionDesc": "", "ActionId": action_id, "WsGuid": guid})
    after = get_workspace(guid, pt)
    return {"guid": guid, "action": action_id,
            "status": after.get("Status"), "status_text": after.get("Concatstatuser")}


def list_process_types() -> list[dict]:
    """List the Focused Build transaction types configured on this system (CRMDOCTYPESet)."""
    rows = client_for(config.SVC_GENERIC).results("CRMDOCTYPESet", {"$top": "50"})
    return [{"code": r.get("Id"), "name": r.get("Name")} for r in rows]

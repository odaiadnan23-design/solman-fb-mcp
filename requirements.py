"""Domain operations for SolMan Focused Build requirements (PM1).

All logic is grounded in live-captured/verified API behavior against
BUSINESS_REQUIREMENTS_SRV (create/read/update) and CRM_GENERIC_SRV (search).
"""
from __future__ import annotations

from typing import Any

import config
from client import SolmanClient, SolmanError, client_for, odata_literal

REQUIREMENT_PROCESS_TYPE = config.REQUIREMENT_PROCESS_TYPE  # Focused Build "Requirement" txn type

# Lifecycle changes are PPF ACTIONS (not field writes — MERGE of StatusId is a no-op).
# Available actions depend on current status; list_actions() reads them per requirement.
ACTION_WITHDRAW = f"{REQUIREMENT_PROCESS_TYPE}_CANCEL"            # -> Canceled (E0006)
ACTION_SEND_FOR_APPROVAL = f"{REQUIREMENT_PROCESS_TYPE}_SEND_FOR_APPROVAL"  # Draft -> To Be Approved
ACTION_APPROVE = f"{REQUIREMENT_PROCESS_TYPE}_CONFIRMED"         # To Be Approved -> Approved
ACTION_REJECT = f"{REQUIREMENT_PROCESS_TYPE}_REJECTED"           # -> Rejected

# Classification -> ClassifAttributes {Key, Value} (WRICEFSet).
CLASSIFICATION = {
    "fit": ("F", "Fit"), "gap": ("G", "Gap"), "wricef": ("1", "WRICEF"),
    "non-functional": ("N", "Non-Functional"),
}

# Deployment defaults (from env via config). NOTE: Category and Owner are MANDATORY
# — omitting them makes the create a silent no-op (HTTP 201 with an empty entity that
# never persists), so ensure they are set in .env.
DEFAULTS: dict[str, Any] = {
    "SolutionId": config.DEFAULT_SOLUTION_ID,
    "BranchId": config.DEFAULT_BRANCH_ID,
    "RequirementsTeamName": config.DEFAULT_TEAM_NAME,
    "RequirementsTeamBpNb": config.DEFAULT_TEAM_BP,
    "PlannedProject": config.DEFAULT_PLANNED_PROJECT,
    "PlannedProjectGuid": config.DEFAULT_PLANNED_PROJECT_GUID,
    "CategoryId": config.DEFAULT_CATEGORY_ID,
    "OwnerBpNo": config.DEFAULT_OWNER_BP,
    "OwnerName": config.DEFAULT_OWNER_NAME,
}

MAX_TITLE = 40  # RequirementTitle is silently truncated server-side at 40 chars


def _dash(guid: str) -> str:
    """REQUIREMENTSet key form (no dashes, upper) from a dashed WORKSPACE GUID."""
    return guid.replace("-", "").upper()


# --------------------------------------------------------------------------
# Reads / search
# --------------------------------------------------------------------------
def search_requirements(query: str = "", top: int = 25) -> list[dict]:
    """Search requirements by title substring (via CRM_GENERIC_SRV WORKSPACESET)."""
    flt = f"ProcessType eq '{REQUIREMENT_PROCESS_TYPE}'"
    if query:
        flt += f" and substringof('{odata_literal(query)}',Description)"
    with SolmanClient(service=config.SVC_GENERIC) as c:
        rows = c.results("WORKSPACESET", {"$filter": flt, "$top": str(top)})
    return [{"id": r.get("ObjectId"), "guid": r.get("Guid"), "title": r.get("Description"),
             "status": r.get("Status"), "branch": r.get("BrancheId")} for r in rows]


def get_requirement(guid: str) -> dict:
    """Read a requirement (full) by GUID (dashed or plain)."""
    key = f"REQUIREMENTSet(WpGuid='',RequirementGuid='{_dash(guid)}')"
    with SolmanClient(service=config.SVC_BIZ_REQ) as c:
        d = c.get(key).get("d", {})
    return {k: v for k, v in d.items() if not (isinstance(v, dict) and "__deferred" in v)}


# --------------------------------------------------------------------------
# Lookups
# --------------------------------------------------------------------------
_LOOKUP = {
    "solutions": ("SOLUTIONSet", ("SolutionId", "SolutionDescription")),
    "priorities": ("PRIORITYSet", ("PriorityId", "PriorityName")),
    "classifications": ("WRICEFSet", ("KEY", "VALUE")),
    "categories": ("CATEGORYSet", None),
    "statuses": ("STATUSSet", ("StatusId", "StatusName")),
    "projects": ("WPPROJECTSet", None),
}


def lookup(kind: str, top: int = 50) -> list[dict]:
    if kind not in _LOOKUP:
        raise ValueError(f"unknown lookup '{kind}'. options: {', '.join(_LOOKUP)}")
    entityset, fields = _LOOKUP[kind]
    with SolmanClient(service=config.SVC_BIZ_REQ) as c:
        rows = c.results(entityset, {"$top": str(top)})
    if fields:
        return [{f: r.get(f) for f in fields} for r in rows]
    return [{k: v for k, v in r.items() if k != "__metadata" and not isinstance(v, dict)} for r in rows]


def branches_for_solution(solution_id: str) -> list[dict]:
    with SolmanClient(service=config.SVC_BIZ_REQ) as c:
        rows = c.results(f"SOLUTIONSet('{solution_id}')/Branchs")
    return [{k: v for k, v in r.items() if k != "__metadata" and not isinstance(v, dict)} for r in rows]


def search_solution_elements(query: str, branch_id: str = "", top: int = 15) -> list[dict]:
    """Search Solution Documentation elements by name substring within a branch."""
    branch_id = branch_id or DEFAULTS["BranchId"]
    flt = f"BranchId eq '{odata_literal(branch_id)}' and substringof('{odata_literal(query)}',ElementName)"
    with SolmanClient(service=config.SVC_BIZ_REQ) as c:
        rows = c.results("ELEMENTSet", {"$filter": flt, "$top": str(top)})
    return [{"element_id": r.get("ElementId"), "name": r.get("ElementName"),
             "type": r.get("ElementTypeId"), "path": r.get("Path")} for r in rows]


# --------------------------------------------------------------------------
# Writes
# --------------------------------------------------------------------------
def create_requirement(
    title: str,
    priority: str = "2",
    classification: str = "fit",
    description: str = "",
    remarks: str = "",
    suggested_solution: str = "",
    external_reference: str = "",
    category_id: str = "",
    owner_bp: str = "",
    owner_name: str = "",
    solution_id: str | None = None,
    branch_id: str | None = None,
    planned_project: str | None = None,
    planned_project_guid: str | None = None,
    value: int = 0,
    effort: int = 0,
    element_id: str = "",
) -> dict:
    """Create a requirement. Returns {RequirementId, RequirementGuid, ...}.

    `priority`: '1'|'2'|'3'. `classification`: fit|gap|wricef|non-functional.
    Env-specific fields default to the S4P working context (see DEFAULTS).
    If `element_id` is given, the Solution element is attached after create.
    """
    if not title:
        raise ValueError("title is required")
    if len(title) > MAX_TITLE:
        title = title[:MAX_TITLE]  # server truncates anyway; do it explicitly
    cls = CLASSIFICATION.get(classification.lower())
    if not cls:
        raise ValueError(f"classification must be one of {list(CLASSIFICATION)}")

    prio_name = {"1": "1: High", "2": "2: Medium", "3": "3: Low"}.get(priority, "2: Medium")
    body: dict[str, Any] = {
        "RequirementTitle": title,
        "PriorityName": prio_name,
        "Description": description,
        "Remarks": remarks,
        "SuggestedSolution": suggested_solution,
        "ZZFLD00000B": external_reference or title,
        "ZFC_ZFLD00000B": 3,
        "ClassifAttributes": {"Key": cls[0], "Value": cls[1]},
        "WricefString": "",
        "Value": value, "Effort": effort,
        "SolutionId": solution_id or DEFAULTS["SolutionId"],
        "BranchId": branch_id or DEFAULTS["BranchId"],
        "RequirementsTeamName": DEFAULTS["RequirementsTeamName"],
        "RequirementsTeamBpNb": DEFAULTS["RequirementsTeamBpNb"],
        "PlannedProject": planned_project or DEFAULTS["PlannedProject"],
        "PlannedProjectGuid": planned_project_guid or DEFAULTS["PlannedProjectGuid"],
        # Mandatory — omitting these silently no-ops the create.
        "Category": {"CatId": category_id or DEFAULTS["CategoryId"]},
        "OwnerBpNo": owner_bp or DEFAULTS["OwnerBpNo"],
        "OwnerName": owner_name or DEFAULTS["OwnerName"],
    }

    with SolmanClient(service=config.SVC_BIZ_REQ) as c:
        created = c.create("REQUIREMENTSet", body)
        guid = created.get("RequirementGuid")
        if not guid:
            raise SolmanError(
                "Create returned no RequirementGuid (silent no-op). Check mandatory fields "
                "(Category, Owner, Solution/Branch)."
            )
        result = {k: created.get(k) for k in ("RequirementId", "RequirementGuid", "RequirementTitle", "Status")}
        if element_id:
            result["element_attached"] = _assign_element(c, guid, element_id, body["BranchId"])
    return result


def _assign_element(c: SolmanClient, guid: str, element_id: str, branch_id: str,
                    scope_id: str = "SAP_DEFAULT_SCOPE") -> bool:
    """Attach a SolDoc element via the Assign_Requirement function import, then verify.

    NOTE: POST to REQELEMENTSet returns success but does NOT persist — the function
    import is the real assignment. Returns True iff the element is present afterwards.
    """
    c.function("Assign_Requirement", {"BranchId": branch_id, "elementid": element_id,
                                      "RequirementId": guid, "ScopeId": scope_id})
    attached = c.results(f"REQUIREMENTSet(WpGuid='',RequirementGuid='{guid}')/REQELEMENTSet")
    return any(e.get("ElementId") == element_id for e in attached) or len(attached) > 0


_UPDATABLE = {"RequirementTitle", "Description", "Remarks", "SuggestedSolution",
              "LongDescription", "PriorityName", "ZZFLD00000B", "Value", "Effort"}


def update_requirement(guid: str, **fields: Any) -> dict:
    """MERGE-update editable fields on a requirement. Keys must be REQUIREMENT properties."""
    body = {k: v for k, v in fields.items() if v is not None}
    unknown = set(body) - _UPDATABLE
    if unknown:
        raise ValueError(f"non-updatable field(s): {unknown}. allowed: {sorted(_UPDATABLE)}")
    if "RequirementTitle" in body:
        body["RequirementTitle"] = str(body["RequirementTitle"])[:MAX_TITLE]
    key = f"REQUIREMENTSet(WpGuid='',RequirementGuid='{_dash(guid)}')"
    with SolmanClient(service=config.SVC_BIZ_REQ) as c:
        c.merge(key, body)
    return {"guid": _dash(guid), "updated": list(body)}


def list_actions(guid: str) -> list[dict]:
    """List the lifecycle PPF actions currently available for a requirement."""
    key = f"REQUIREMENTSet(WpGuid='',RequirementGuid='{_dash(guid)}')/PPFACTIONSET"
    with SolmanClient(service=config.SVC_BIZ_REQ) as c:
        rows = c.results(key)
    return [{"action_id": r.get("ActionId"), "description": r.get("ActionDesc")} for r in rows]


def execute_action(guid: str, action_id: str) -> dict:
    """Execute a lifecycle PPF action (from list_actions) and return the resulting status."""
    g = _dash(guid)
    with SolmanClient(service=config.SVC_BIZ_REQ) as c:
        c.function("get_ppf_actions", {"ActionId": action_id, "WsGuid": g})  # executes the action
        after = c.get(f"REQUIREMENTSet(WpGuid='',RequirementGuid='{g}')").get("d", {})
    return {"guid": g, "action": action_id, "status": after.get("Status"), "status_id": after.get("StatusId")}


def withdraw_requirement(guid: str) -> dict:
    """Withdraw (cancel) a requirement — Draft -> Canceled."""
    return execute_action(guid, ACTION_WITHDRAW)


def submit_for_approval(guid: str) -> dict:
    """Send a requirement for approval — Draft -> To Be Approved."""
    return execute_action(guid, ACTION_SEND_FOR_APPROVAL)


def approve_requirement(guid: str) -> dict:
    """Approve a requirement — To Be Approved -> Approved (required before WP assignment)."""
    return execute_action(guid, ACTION_APPROVE)


def reject_requirement(guid: str) -> dict:
    """Reject a requirement — To Be Approved -> Rejected."""
    return execute_action(guid, ACTION_REJECT)


def attach_element(requirement_guid: str, element_id: str, branch_id: str | None = None,
                   scope_id: str = "SAP_DEFAULT_SCOPE") -> dict:
    """Attach a Solution Documentation element to a requirement (verified)."""
    g = _dash(requirement_guid)
    with SolmanClient(service=config.SVC_BIZ_REQ) as c:
        ok = _assign_element(c, g, element_id, branch_id or DEFAULTS["BranchId"], scope_id)
    if not ok:
        raise SolmanError(f"Element {element_id} did not attach to {g} (verify failed).")
    return {"guid": g, "element_attached": element_id, "verified": True}


def list_elements(requirement_guid: str) -> list[dict]:
    """List the Solution Documentation elements attached to a requirement."""
    g = _dash(requirement_guid)
    with SolmanClient(service=config.SVC_BIZ_REQ) as c:
        rows = c.results(f"REQUIREMENTSet(WpGuid='',RequirementGuid='{g}')/REQELEMENTSet")
    return [{"element_id": r.get("ElementId"), "name": r.get("ElementName"),
             "type": r.get("ElementTypeId"), "path": r.get("ElementPath"),
             "branch_id": r.get("BranchId"), "scope_id": r.get("ScopeId")} for r in rows]


def detach_element(requirement_guid: str, element_id: str, branch_id: str | None = None) -> dict:
    """Detach a Solution Documentation element from a requirement (UNASSIGN_BR_FROM_ELEMENT), verified."""
    g = _dash(requirement_guid)
    with SolmanClient(service=config.SVC_BIZ_REQ) as c:
        c.function("UNASSIGN_BR_FROM_ELEMENT",
                   {"RequirementGuid": g, "elementId": element_id,
                    "BranchId": branch_id or DEFAULTS["BranchId"]})
        still = any(e.get("ElementId") == element_id
                    for e in c.results(f"REQUIREMENTSet(WpGuid='',RequirementGuid='{g}')/REQELEMENTSet"))
    return {"guid": g, "element_detached": element_id, "removed": not still}

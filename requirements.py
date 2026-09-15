"""Domain operations for SolMan Focused Build requirements.

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


def flp_url(guid: str) -> str:
    """FLP deep link to open a requirement in the Requirements Management app."""
    g = _dash(guid)
    return (f"{config.BASE_URL}/sap/bc/ui2/flp?sap-client={config.SAP_CLIENT}"
            f"&sap-language=EN#Action-requirementApp&/object/{g},NULL")


def get_requirement(guid: str) -> dict:
    """Read a requirement (full) by GUID (dashed or plain)."""
    key = f"REQUIREMENTSet(WpGuid='',RequirementGuid='{_dash(guid)}')"
    with SolmanClient(service=config.SVC_BIZ_REQ) as c:
        d = c.get(key).get("d", {})
    out = {k: v for k, v in d.items() if not (isinstance(v, dict) and "__deferred" in v)}
    out["url"] = flp_url(guid)
    return out


# QUERY-SEMANTICS WARNING (verified live): this gateway SILENTLY IGNORES most
# $filter fields, or-chains keep only the LAST predicate, $orderby 500s, and on
# a BranchId-filtered REQUIREMENTSet even $skip lies (every page returns the
# same window). The only trustworthy enumeration is CRM_GENERIC WORKSPACESET,
# which honors ProcessType + substringof(Description) and returns NEWEST FIRST.
def list_requirements(solution: str = "", branch_id: str = "", status: str = "",
                      priority: str = "", owner: str = "", project: str = "",
                      query: str = "", top: int = 25, scan: int = 200) -> list[dict]:
    """List/filter requirements, newest first, with FLP deep links.

    `query` (title substring) is applied server-side; `status` ('Approved' or
    'E0003') and branch are filtered client-side from the search rows; `owner`
    / `project` / `priority` require hydrating each candidate (extra read per
    row, capped by `scan`). solution accepts a name/id ("PRD").
    """
    if solution and not branch_id:
        import solutions as _sol
        branch_id = _sol.resolve_context(solution)["branch_id"]

    status_id = ""
    if status:
        statuses = lookup("statuses")
        by_id = {s["StatusId"]: s["StatusId"] for s in statuses}
        by_name = {(s["StatusName"] or "").lower(): s["StatusId"] for s in statuses}
        status_id = by_id.get(status.upper()) or by_name.get(status.lower())
        if not status_id:
            raise ValueError(f"unknown status {status!r}; use an id (E0003) or name (Approved)")

    flt = f"ProcessType eq '{REQUIREMENT_PROCESS_TYPE}'"
    if query:
        flt += f" and substringof('{odata_literal(query)}',Description)"
    rows = client_for(config.SVC_GENERIC).results(
        "WORKSPACESET", {"$filter": flt, "$top": str(scan)})

    need_hydrate = bool(owner or project or priority)
    ol, pl = owner.lower(), project.lower()
    out = []
    for r in rows:
        if status_id and r.get("Status") != status_id:
            continue
        if branch_id and r.get("BrancheId") and r.get("BrancheId") != branch_id:
            continue
        item = {"id": r.get("ObjectId"), "guid": _dash(r.get("Guid", "")),
                "title": r.get("Description"), "status_id": r.get("Status"),
                "url": flp_url(r.get("Guid", ""))}
        if need_hydrate:
            full = get_requirement(item["guid"])
            if ol and ol not in (full.get("OwnerName") or "").lower():
                continue
            if pl and pl not in (full.get("PlannedProject") or "").lower():
                continue
            if priority and full.get("PriorityId") != priority:
                continue
            item.update({"status": full.get("Status"), "owner": full.get("OwnerName"),
                         "project": full.get("PlannedProject"),
                         "priority": full.get("PriorityName")})
        out.append(item)
        if len(out) >= top:
            break
    return out


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


def search_solution_elements(query: str, branch_id: str = "", top: int = 15,
                             solution: str = "") -> list[dict]:
    """Search Solution Documentation elements by name substring within a branch.

    `solution` accepts a solution name/id ("PRD") — its Design branch is used.
    """
    if solution and not branch_id:
        import solutions as _sol
        branch_id = _sol.resolve_context(solution)["branch_id"]
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
    scope_id: str = "SAP_DEFAULT_SCOPE",
    solution: str = "",
) -> dict:
    """Create a requirement. Returns {RequirementId, RequirementGuid, ...}.

    `priority`: '1'|'2'|'3'. `classification`: fit|gap|wricef|non-functional.
    `solution` accepts a solution NAME or id ("PRD", "QAS", ...) — the branch is
    resolved automatically (Design) and `scope_id` may then be a scope NAME too
    ("Release 5"). Without `solution`, env defaults apply (see DEFAULTS).
    If `element_id` is given, the Solution element is attached after create under
    the resolved scope — pass the target release/wave scope so the link lands
    there, not in the `SAP_DEFAULT_SCOPE` catch-all.
    """
    if not title:
        raise ValueError("title is required")
    if len(title) > MAX_TITLE:
        title = title[:MAX_TITLE]  # server truncates anyway; do it explicitly
    cls = CLASSIFICATION.get(classification.lower())
    if not cls:
        raise ValueError(f"classification must be one of {list(CLASSIFICATION)}")

    non_default_solution = False
    if solution or (scope_id and scope_id != "SAP_DEFAULT_SCOPE"):
        import solutions as _sol  # local import; no cycle (solutions never imports us)
        ctx = _sol.resolve_context(solution, branch_id or "", scope_id or "")
        solution_id = solution_id or ctx["solution_id"]
        branch_id = branch_id or ctx["branch_id"]
        scope_id = ctx["scope_id"]
        non_default_solution = bool(solution_id) and solution_id != DEFAULTS["SolutionId"]

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
        # Team/PlannedProject env defaults are solution-specific: never let them
        # leak into a DIFFERENT solution's requirement — blank unless passed.
        "RequirementsTeamName": "" if non_default_solution else DEFAULTS["RequirementsTeamName"],
        "RequirementsTeamBpNb": "" if non_default_solution else DEFAULTS["RequirementsTeamBpNb"],
        "PlannedProject": planned_project or ("" if non_default_solution else DEFAULTS["PlannedProject"]),
        "PlannedProjectGuid": planned_project_guid or ("" if non_default_solution else DEFAULTS["PlannedProjectGuid"]),
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
            result["element_attached"] = _assign_element(c, guid, element_id, body["BranchId"], scope_id)
    return result


def create_requirements_batch(items: list[dict], solution: str = "",
                              scope_id: str = "SAP_DEFAULT_SCOPE",
                              planned_project: str = "", planned_project_guid: str = "",
                              classification: str = "fit", priority: str = "2",
                              category_id: str = "", owner_bp: str = "",
                              owner_name: str = "") -> dict:
    """Create several requirements in one call (continues on per-row errors).

    Each item: {title, description?, element_id?, external_reference?,
    classification?, priority?, remarks?, suggested_solution?}. Shared context
    (solution/scope/project/etc.) applies to every row; per-item values win.
    Solution/scope names are resolved ONCE up front.
    """
    if not items:
        raise ValueError("items is empty")
    branch_id = ""
    if solution or (scope_id and scope_id != "SAP_DEFAULT_SCOPE"):
        import solutions as _sol
        ctx = _sol.resolve_context(solution, "", scope_id or "")
        branch_id, scope_id = ctx["branch_id"], ctx["scope_id"]

    results, ok = [], 0
    for it in items:
        title = (it.get("title") or "")[:MAX_TITLE]
        try:
            r = create_requirement(
                title=title,
                priority=it.get("priority", priority),
                classification=it.get("classification", classification),
                description=it.get("description", ""),
                remarks=it.get("remarks", ""),
                suggested_solution=it.get("suggested_solution", ""),
                external_reference=it.get("external_reference", ""),
                category_id=category_id, owner_bp=owner_bp, owner_name=owner_name,
                branch_id=branch_id or None,
                planned_project=planned_project or None,
                planned_project_guid=planned_project_guid or None,
                element_id=it.get("element_id", ""),
                scope_id=scope_id, solution=solution,
            )
            ok += 1
            results.append({"title": title, "id": r.get("RequirementId"),
                            "guid": r.get("RequirementGuid"),
                            "element_attached": r.get("element_attached"),
                            "url": flp_url(r.get("RequirementGuid", ""))})
        except Exception as e:  # noqa: BLE001 — batch must continue on row errors
            results.append({"title": title, "error": f"{type(e).__name__}: {e}"})
    return {"created": ok, "failed": len(items) - ok, "results": results}


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
    # v4: every update goes through the full-entity $batch MERGE the Fiori app uses.
    # The old field-list MERGE blanked SolutionId (hiding element links) and could not
    # touch planned project, team, owner or classification. Same signature, no trap.
    changes = {k: v for k, v in fields.items() if v is not None}
    if not changes:
        return {"guid": _dash(guid), "applied": {}, "all_applied": True}
    return update_requirement_fields(guid, **changes)


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
                   scope_id: str = "SAP_DEFAULT_SCOPE", solution: str = "") -> dict:
    """Attach a Solution Documentation element to a requirement (verified).

    `solution` accepts a name/id ("PRD"); `scope_id` may then be a scope NAME
    ("Release 5") — both are resolved to ids on the right branch. Re-running with
    a new scope UPDATES the existing link in place (no duplicate row).
    """
    if solution or (scope_id and scope_id != "SAP_DEFAULT_SCOPE"):
        import solutions as _sol
        ctx = _sol.resolve_context(solution, branch_id or "", scope_id or "")
        branch_id = branch_id or ctx["branch_id"]
        scope_id = ctx["scope_id"]
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


# --------------------------------------------------------------------------
# Requirement <-> Work Package assignment (BUSINESS_REQUIREMENTS_SRV imports)
# --------------------------------------------------------------------------
def authorized_actions() -> list[dict]:
    """What this user may do in Requirements Management — get_authorized_actions.
    Read-only. On the Gore system all eight came back authorized, including
    UNASSIGN_WP_FROM_REQ and ASSIGN_EXISTING_WP."""
    rows = client_for(config.SVC_BIZ_REQ).function("get_authorized_actions", {})
    d = rows.get("d", rows)
    rows = d.get("results", d) if isinstance(d, dict) else d
    return [{"code": r.get("Code"), "description": r.get("Description"),
             "authorized": bool(r.get("Authorized"))} for r in rows]


def _req_wp_data(requirement_guid: str, work_package_guid: str) -> str:
    """The reqWpData payload the (un)assignment imports take. Verified against
    checkWpUnassignmentFromRequirement: every shape below is accepted without
    error and returns no objection; the JSON object form is what the Fiori app
    sends, so that is the one used."""
    import json as _json
    return _json.dumps({"RequirementGuid": _dash(requirement_guid).replace("-", "").upper(),
                        "WpGuid": _dash(work_package_guid).replace("-", "").upper()})


def check_unassign_work_package(requirement_guid: str, work_package_guid: str) -> dict:
    """Ask whether a requirement may be unassigned from a work package (read-only).
    An empty message list means no objection."""
    c = client_for(config.SVC_BIZ_REQ)
    r = c.function("checkWpUnassignmentFromRequirement",
                   {"reqWpData": _req_wp_data(requirement_guid, work_package_guid)})
    d = r.get("d", r)
    msgs = d.get("results", d) if isinstance(d, dict) else d
    msgs = msgs if isinstance(msgs, list) else [msgs]
    msgs = [m for m in msgs if isinstance(m, dict) and any(v for k, v in m.items() if k != "__metadata")]
    return {"allowed": not msgs, "messages": msgs}


def unassign_work_package(requirement_guid: str, work_package_guid: str,
                          force: bool = False) -> dict:
    """Unassign ONE work package from a requirement — wpUnassignmentFromRequirement.

    This is the API form of the Fiori unassign, with one crucial difference in
    intent: Fiori's unassign clears EVERY work-package link on the requirement and
    resets its status; this import targets one pair. It is a POST-only import
    (calling it with GET returns a misleading 404 — the first attempt did exactly
    that), so it goes through client.function_post: journalled, kill-switchable.

    Runs the check first and refuses if the system objects, unless force=True.
    Verifies afterwards by re-reading the requirement's work packages.

    STATUS: implemented and reachable, but NOT yet exercised against a live pair —
    every candidate pair on 16-Sep-2026 was a link somebody wanted kept. Treat the
    first real call as a test: pick a pair you would re-create if it misbehaved.
    """
    check = check_unassign_work_package(requirement_guid, work_package_guid)
    if not check["allowed"] and not force:
        return {"unassigned": False, "reason": "system objected", **check}
    c = client_for(config.SVC_BIZ_REQ)
    resp = c.function_post("wpUnassignmentFromRequirement",
                           {"reqWpData": _req_wp_data(requirement_guid, work_package_guid)})
    rg = _dash(requirement_guid)
    after = c.results("REQUIREMENTSet", {"$filter": f"RequirementGuid eq guid'{rg}'"})
    still = [x.get("WpId") for x in after if x.get("WpId")]
    wp_id = _work_package_id(work_package_guid)
    return {"unassigned": wp_id not in still if wp_id else None, "work_package": wp_id,
            "remaining_work_packages": still, "response": resp.get("d", resp)}


def assign_existing_work_package(requirement_guid: str, work_package_guid: str) -> dict:
    """Assign an already-existing work package to a requirement — Assign_Existing_Wp.
    Same outcome as workpackages.assign_work_package (which uses Assign_Requirement on
    the other service); kept because this is the import the authorization list names.
    Requirement must be Approved and the work package in Scoping."""
    c = client_for(config.SVC_BIZ_REQ)
    wg = _dash(work_package_guid).replace("-", "").upper()
    resp = c.function("Assign_Existing_Wp", {"WpGuid": wg})
    import workpackages as _wp
    return {"assigned": _wp.link_verified(wg, _dash(requirement_guid)), "response": resp.get("d", resp)}


def _work_package_id(work_package_guid: str) -> str | None:
    import workspaces as _ws
    hdr = _ws.get_workspace(work_package_guid, "S1IT")
    return hdr.get("ObjectId") if hdr else None


# --------------------------------------------------------------------------
# Full-entity update — every header field, the way the Fiori app saves
# --------------------------------------------------------------------------
# The Requirements app (/SALM/OST_BR, com.sap.solman.fb.requirement_mgt_7_2) saves an
# edit as m.update(path, entity) on a UI5 v2 model with default settings: a MERGE
# of the WHOLE entity, inside a $batch changeset, with the REQELEMENTSet navigation
# deleted first. Two things follow that the field-list MERGE in update_requirement
# never did:
#   * a partial MERGE blanks whatever it omits (SolutionId, team, owner, planned
#     project) — the full entity carries them, so nothing is lost;
#   * the changeset route reaches CHANGESET_PROCESS, where PlannedProject / Guid,
#     RequirementsTeam*, Owner*, Category and ClassifAttributes are honoured, whereas
#     a direct MERGE carrying PlannedProject answers 500.
# Proven 16-Sep-2026 on requirement 1000044872.

# Fields the caller may change. Everything else is carried through unchanged.
_FULL_UPDATABLE = {
    "RequirementTitle", "Description", "Remarks", "SuggestedSolution", "LongDescription",
    "PriorityName", "PriorityId", "Value", "Effort", "ZZFLD00000B",
    "PlannedProject", "PlannedProjectGuid",
    "RequirementsTeamName", "RequirementsTeamBpNb",
    "OwnerName", "OwnerBpNo", "BusinessExpertName", "BusinessExpertBpNo",
    "SolutionId", "BranchId", "SoldocScopeId",
    "Category", "ClassifAttributes", "WricefString", "Local",
}
_SERVER_OWNED = ("CreatedAt", "ChangedAt", "CreatedBy", "ChangedBy", "RequirementIdLink",
                 "CrmLink", "WpCrmLink", "SoldocUrl", "Icon", "LineItems", "RequirementItems",
                 "MaxLines", "EnableEdit", "Assignable", "Wpassignment", "TruncPath")


def _full_entity(row: dict) -> dict:
    body = {k: v for k, v in row.items()
            if k != "__metadata" and not (isinstance(v, dict) and "__deferred" in v)}
    for k in ("Category", "ClassifAttributes"):
        if isinstance(body.get(k), dict):
            body[k] = {kk: vv for kk, vv in body[k].items() if kk != "__metadata"}
    for k in _SERVER_OWNED:
        body.pop(k, None)
    return body


def update_requirement_fields(requirement_guid: str, **changes) -> dict:
    """Change any header field of a requirement — including the ones the field-list
    update could not touch: planned project (+ GUID), team, owner, business expert,
    category, classification, solution/branch/scope.

    Reads the current entity, applies `changes`, MERGEs the whole thing in a $batch
    changeset, and reads it back; the result lists each requested field as applied
    or not. Journalled; blocked by SOLMAN_READONLY.

        update_requirement_fields(g, PlannedProject="MYPROJ_1.0_CHG0001", PlannedProjectGuid="…")
        update_requirement_fields(g, RequirementsTeamName="RTR Team", RequirementsTeamBpNb="228")
        update_requirement_fields(g, ClassifAttributes={"AttrName": "/SALM/WRICEF", "Key": "1", "Value": "WRICEF"})
    """
    bad = sorted(set(changes) - _FULL_UPDATABLE)
    if bad:
        raise ValueError(f"not updatable through this route: {bad}; allowed: {sorted(_FULL_UPDATABLE)}")
    if "PlannedProject" in changes and "PlannedProjectGuid" not in changes:
        raise ValueError("PlannedProject needs PlannedProjectGuid alongside — the name alone is "
                         "ignored and the requirement ends up outside its release")
    if "PriorityName" in changes and "PriorityId" not in changes:
        changes["PriorityId"] = str(changes["PriorityName"])[:1]
    # Resolve to a RequirementId first: the collection's RequirementGuid filter is a
    # string compare the gateway ignores, and guid'...' is an invalid token on it.
    ref = requirement_guid.replace("-", "").strip()
    c = client_for(config.SVC_BIZ_REQ)
    if len(ref) == 32:
        import rfc as _rfc
        hit = _rfc.read_table("CRMD_ORDERADM_H", ["OBJECT_ID"], where=f"GUID = '{ref.upper()}'", rowcount=1)
        if not hit:
            raise SolmanError(f"no document with GUID {ref}")
        rid = hit[0]["OBJECT_ID"]
    else:
        rid = ref
    rows = c.results("REQUIREMENTSet", {"$filter": f"RequirementId eq '{odata_literal(rid)}'"})
    if not rows:
        raise SolmanError(f"requirement {requirement_guid} not found")
    current = rows[0]
    g = current["RequirementGuid"]
    if current.get("StatusId") in ("E0005", "E0006"):
        raise SolmanError(f"requirement {rid} is {current.get('Status')} — the document is locked "
                          "(CRM_ORDER/008 'No changes possible'); a MERGE would answer 204 and change nothing")
    body = _full_entity(current)
    body.update(changes)
    key = f"REQUIREMENTSet(WpGuid='',RequirementGuid='{body['RequirementGuid'].replace('-', '')}')"
    c.batch_merge(key, body)
    after = c.results("REQUIREMENTSet", {"$filter": f"RequirementId eq '{current['RequirementId']}'"})[0]

    def _eq(a, b):
        if isinstance(a, dict) and isinstance(b, dict):
            return all(str(b.get(k, "")) == str(v) for k, v in a.items() if k != "__metadata")
        return str(a) == str(b)
    applied = {k: _eq(v, after.get(k)) for k, v in changes.items()}
    return {"id": current["RequirementId"], "guid": g, "applied": applied,
            "all_applied": all(applied.values()),
            "now": {k: after.get(k) for k in changes},
            "solution_kept": bool(after.get("SolutionId")) == bool(current.get("SolutionId"))}

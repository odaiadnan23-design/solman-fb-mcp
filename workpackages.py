"""Work Package operations for SolMan Focused Build (PM1).

Flow (verified against the UI):
  1. requirement must be APPROVED (Draft->To Be Approved->Approved) before a WP links to it
  2. create the WP    -> POST BUSINESS_REQUIREMENTS_SRV/BRWPSet (TypeId="WP"), ProcessType S1IT
  3. link WP -> req   -> Assign_Existing_Wp(WpGuid, RequirementGuid)  [function import]

Note: the BRWPSet create's REQUIREMENTS array does NOT persist the link on its own, and
Assign_Existing_Wp silently no-ops if the requirement isn't Approved — hence the precheck.
"""
from __future__ import annotations

import re
from typing import Any

import config
from client import SolmanClient, SolmanError
from requirements import CLASSIFICATION, _dash, MAX_TITLE, get_requirement

WP_PROCESS_TYPE = "S1IT"
_APPROVED_STATUS = "Approved"


def _guid_from_url(url: str) -> str:
    m = re.search(r"/workspace/([0-9A-Fa-f-]{32,})", url or "")
    return m.group(1).replace("-", "").upper() if m else ""


def _redash(guid: str) -> str:
    """32-char plain GUID -> dashed form for a WORKSPACESET key."""
    g = guid.replace("-", "").upper()
    return f"{g[:8]}-{g[8:12]}-{g[12:16]}-{g[16:20]}-{g[20:32]}"


def link_verified(wp_guid: str, requirement_guid: str) -> bool:
    """True iff the WP's related transactions include the requirement.

    Reads CRM_GENERIC_SRV WORKSPACESET(<wp>,'S1IT')/BT_RELATEDTRANSSet — the WP's linked
    requirement appears there as a row with WsType='Requirement'.
    """
    key = f"WORKSPACESET(Guid=guid'{_redash(wp_guid)}',ProcessType='{WP_PROCESS_TYPE}')/BT_RELATEDTRANSSet"
    want = _dash(requirement_guid)
    with SolmanClient(service=config.SVC_GENERIC) as c:
        return any(_dash(r.get("WsTransGuid", "")) == want for r in c.results(key))


def create_work_package(
    requirement_guid: str,
    title: str,
    assign: bool = True,
    category_id: str = "",
    classification: str = "fit",
    priority: str = "",
    owner_bp: str = "",
    dev_team_bp: str = "",
    long_description: str = "",
    project: str = "",
    project_phase: str = "",
    release: str = "",
    release_component: str = "",
    release_number: str = "",
) -> dict:
    """Create a Work Package and (by default) link it to its requirement.

    The requirement MUST be Approved for the link to take. Release/project targeting
    defaults come from config (SOLMAN_WP_* env). classification: fit|gap|wricef|non-functional.
    """
    if not requirement_guid:
        raise ValueError("requirement_guid is required")
    if not title:
        raise ValueError("title is required")
    cls = CLASSIFICATION.get(classification.lower())
    if not cls:
        raise ValueError(f"classification must be one of {list(CLASSIFICATION)}")

    req_guid = _dash(requirement_guid)
    if assign:  # fail early if the requirement can't accept a WP
        status = get_requirement(req_guid).get("Status")
        if status != _APPROVED_STATUS:
            raise SolmanError(
                f"Requirement is '{status}', must be 'Approved' before a WP can be linked. "
                "Run approve_requirement first (submit_for_approval -> approve)."
            )

    body: dict[str, Any] = {
        "TypeId": "WP",
        "Description": title[:MAX_TITLE],
        "EFFORT_POINT": 0, "VALUE_POINT": 0, "Priority": priority,
        "Category": category_id or config.DEFAULT_CATEGORY_ID,
        "ClassifAttributes": {"Key": cls[0], "Value": cls[1]}, "WricefString": "",
        "RequestedRelease": release or config.WP_RELEASE,
        "ReleaseComponent": release_component or config.WP_RELEASE_COMPONENT,
        "ReleaseNumber": release_number or config.WP_RELEASE_NUMBER,
        "Project": project or config.WP_PROJECT,
        "ProjectPhase": project_phase or config.WP_PROJECT_PHASE,
        "Owner": owner_bp or config.DEFAULT_OWNER_BP,
        "DevTeamBpNo": dev_team_bp or config.WP_DEV_TEAM_BP, "TestTeamBpNo": "",
        "LongDescription": long_description,
        "REQUIREMENTS": [{"RequirementGuid": req_guid, "WpGuid": ""}],
    }
    with SolmanClient(service=config.SVC_BIZ_REQ) as c:
        created = c.create("BRWPSet", body)
        wp_id = created.get("Guid")                 # BRWP quirk: Guid field holds the ObjectId
        wp_guid = _guid_from_url(created.get("Url", ""))
        if not wp_id:
            raise SolmanError("WP create returned no id (silent no-op). Check mandatory fields.")
        result = {"work_package_id": wp_id, "work_package_guid": wp_guid,
                  "title": title[:MAX_TITLE], "requirement_guid": req_guid, "assigned": False}
        if assign and wp_guid:
            c.function("Assign_Existing_Wp", {"WpGuid": wp_guid, "RequirementGuid": req_guid})
            result["assigned"] = link_verified(wp_guid, req_guid)  # self-verify the link persisted
    return result


def assign_work_package(requirement_guid: str, work_package_guid: str) -> dict:
    """Link an existing WP to a requirement. Requirement must be Approved."""
    req_guid = _dash(requirement_guid)
    wp_guid = _dash(work_package_guid)
    with SolmanClient(service=config.SVC_BIZ_REQ) as c:
        status = get_requirement(req_guid).get("Status")
        if status != _APPROVED_STATUS:
            raise SolmanError(
                f"Requirement is '{status}', must be 'Approved' to assign a WP. Approve it first."
            )
        c.function("Assign_Existing_Wp", {"WpGuid": wp_guid, "RequirementGuid": req_guid})
    assigned = link_verified(wp_guid, req_guid)
    if not assigned:
        raise SolmanError(f"Assign call returned but WP {wp_guid} is not linked to {req_guid} (verify failed).")
    return {"requirement_guid": req_guid, "work_package_guid": wp_guid, "assigned": True}


def withdraw_work_package(work_package_guid: str) -> dict:
    """Withdraw a Work Package (PPF action S1ITR_REJECT_SCOPE — 'Reject')."""
    g = _dash(work_package_guid)
    with SolmanClient(service=config.SVC_GENERIC) as c:
        c.function("PPF_ACTION", {"ActionDesc": "", "ActionId": "S1ITR_REJECT_SCOPE", "WsGuid": g})
    return {"work_package_guid": g, "withdrawn": True}

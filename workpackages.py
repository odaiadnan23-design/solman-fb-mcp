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
            result["assigned"] = True
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
    return {"requirement_guid": req_guid, "work_package_guid": wp_guid, "assigned": True}

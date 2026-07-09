"""Work Package operations for SolMan Focused Build.

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
from client import SolmanClient, SolmanError, client_for
from requirements import CLASSIFICATION, _dash, MAX_TITLE, get_requirement

WP_PROCESS_TYPE = "S1IT"
# A requirement can take a WP once Approved or later. Block only pre-approval / terminal states:
# Draft, To Be Approved, Rejected, Canceled, Postponed.
_WP_LINK_BLOCKED = {"E0001", "E0009", "E0002", "E0006", "E0008"}


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
        req = get_requirement(req_guid)
        if req.get("StatusId") in _WP_LINK_BLOCKED:
            raise SolmanError(
                f"Requirement is '{req.get('Status')}' — must be Approved or later to link a WP. "
                "Run submit_for_approval then approve_requirement."
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
            if not result["assigned"]:
                result["warning"] = ("WP created but link to requirement not verified — "
                                     "check the requirement is Approved, then use assign_work_package.")
    return result


def assign_work_package(requirement_guid: str, work_package_guid: str) -> dict:
    """Link an existing WP to a requirement. Requirement must be Approved."""
    req_guid = _dash(requirement_guid)
    wp_guid = _dash(work_package_guid)
    with SolmanClient(service=config.SVC_BIZ_REQ) as c:
        req = get_requirement(req_guid)
        if req.get("StatusId") in _WP_LINK_BLOCKED:
            raise SolmanError(
                f"Requirement is '{req.get('Status')}' — must be Approved or later to assign a WP."
            )
        c.function("Assign_Existing_Wp", {"WpGuid": wp_guid, "RequirementGuid": req_guid})
    assigned = link_verified(wp_guid, req_guid)
    out = {"requirement_guid": req_guid, "work_package_guid": wp_guid, "assigned": assigned}
    if not assigned:
        out["warning"] = "Assign call returned but the link could not be verified."
    return out


_WI_TYPES = {"nc": "S1MJ", "gc": "S1CG", "S1MJ": "S1MJ", "S1CG": "S1CG"}

_WP_SCOPING_ACTION = "S1ITR_HANDOVER_TO_SCOPING"     # "Define Scope"
_WP_PRE_SCOPING_STATUS = "E0001"                     # freshly created WP


def list_scope_components(work_package_guid: str, wp_type: str = "nc",
                          current_systems_only: bool = True) -> list[dict]:
    """Valid technical components (ConfigItem value help) for Work Items under a WP.

    NOTE: the value help returns EMPTY unless filtered by ProcessType and
    SystemSwitch — exactly the filters the UI sends.
    """
    wtype = _WI_TYPES.get(wp_type, wp_type.upper())
    wpg = _dash(work_package_guid)
    sw = "true" if current_systems_only else "false"
    rows = client_for(config.SVC_GENERIC).results(
        f"BTSCOPESET(WpGuid='{wpg}',WpItemGuid='')/SCOPE_COMPONENTSet",
        {"$filter": f"ProcessType eq '{wtype}' and SystemSwitch eq {sw}", "$top": "100"})
    return [{"config_item": r.get("ConfigItem"), "ibase_instance": r.get("IbaseInstance"),
             "system_id": r.get("SystemId"), "description": r.get("CmpDesc")}
            for r in rows]


def create_work_item(work_package_guid: str, description: str, wricef: str = "non-functional",
                     text: str = "", config_item: str = "", wp_type: str = "nc",
                     sprint: str = "", priority: str = "4", ibase_instance: str = "",
                     cmp_desc: str = "", wp_system: str = "", proc_type_desc: str = "",
                     auto_scope: bool = True) -> dict:
    """Create a Work Item (scope item) under a Work Package — verified to persist.

    Three ingredients make the row COMMIT (a plain POST 201s but silently never
    saves — reverse-engineered from the shipped UI):
      1. the WP must be in Scoping or later — freshly-created WPs get the
         "Define Scope" PPF action executed first (when `auto_scope`);
      2. `config_item` must be VALID for this WP/type (see list_scope_components;
         an invalid one is dropped without error). Omitted -> first valid picked;
      3. the fill is a DEEP create to top-level BTSCOPESET including an (empty)
         `BTSCOPE_PARTNERSSet` — the deep-create signature triggers the one-order save.

    wricef: fit|gap|wricef|non-functional. wp_type: nc (S1MJ) | gc (S1CG).
    """
    if not description:
        raise ValueError("description is required")
    cls = CLASSIFICATION.get(wricef.lower())
    if not cls:
        raise ValueError(f"wricef must be one of {list(CLASSIFICATION)}")
    wtype = _WI_TYPES.get(wp_type, wp_type.upper())
    wpg = _dash(work_package_guid)
    c = client_for(config.SVC_GENERIC)
    ws_key = f"WORKSPACESET(Guid=guid'{_redash(wpg)}',ProcessType='{WP_PROCESS_TYPE}')"

    # 1) ensure the WP is in a scope-editable status
    hdr = c.get(ws_key).get("d", {})
    moved = False
    if hdr.get("Status") == _WP_PRE_SCOPING_STATUS:
        if not auto_scope:
            raise SolmanError("WP is freshly created — run the 'Define Scope' action "
                              f"({_WP_SCOPING_ACTION}) first, or pass auto_scope=True.")
        c.function("PPF_ACTION", {"ActionDesc": "", "ActionId": _WP_SCOPING_ACTION, "WsGuid": wpg})
        moved = True

    # 2) a VALID component is mandatory for the save to stick
    if not config_item:
        comps = list_scope_components(wpg, wtype)
        if not comps:
            raise SolmanError("No valid scope components for this WP/type — cannot create a "
                              "Work Item (check the WP's system landscape assignment).")
        pick = comps[0]
        config_item, ibase_instance = pick["config_item"], pick["ibase_instance"]
        wp_system = wp_system or pick["system_id"]
        cmp_desc = cmp_desc or (pick["description"] or "")

    # 3) add empty row -> deep fill incl. partners array (the save trigger)
    added = c.create(f"{ws_key}/BTSCOPESet", {
        "WpGuid": "", "WpType": "", "WpDescription": "", "WpSystem": "", "WpScope": "",
        "WpStatus": "", "WpItemGuid": "", "ProcTypeDesc": "", "Sprint": "", "Wricef": "",
        "IbaseInstance": "", "Text": "", "Changeable": True, "Url": "", "WricefKey": "",
        "ConfigItem": "", "CmpDesc": "", "ValuePoints": 0, "StoryPoints": 0, "PriorityId": priority})
    item_guid = added.get("WpItemGuid")
    if not item_guid:
        raise SolmanError("Work Item add returned no WpItemGuid (could not create the scope row).")

    c.create("BTSCOPESET", {
        "WpGuid": wpg, "WpItemGuid": item_guid, "WpType": wtype,
        "WpDescription": description[:MAX_TITLE], "Wricef": cls[0], "WricefKey": cls[0],
        "Text": text, "PriorityId": priority, "Changeable": True,
        "ValuePoints": 0, "StoryPoints": 0,
        "ConfigItem": config_item, "IbaseInstance": ibase_instance, "CmpDesc": cmp_desc,
        "WpSystem": wp_system, "ProcTypeDesc": proc_type_desc, "Sprint": sprint,
        "WpScope": "", "WpStatus": "", "Url": "", "ZZFLD00000B": "", "ZFC_ZFLD00000B": 0,
        "BTSCOPE_PARTNERSSet": []})

    match = [i for i in list_work_items(wpg) if i.get("item_guid") == item_guid]
    if not match:
        raise SolmanError("Work Item did not persist despite the deep-create flow — "
                          "verify config_item is in list_scope_components for this WP.")
    return {"work_package_guid": wpg, "work_item_guid": item_guid, "type": wtype,
            "description": description[:MAX_TITLE], "status": match[0].get("status"),
            "config_item": config_item, "system": wp_system,
            "wp_moved_to_scoping": moved, "created": True}


def list_work_items(work_package_guid: str) -> list[dict]:
    """List the Work Items (scope items / BTSCOPE) under a Work Package."""
    g = _redash(_dash(work_package_guid))
    key = f"WORKSPACESET(Guid=guid'{g}',ProcessType='{WP_PROCESS_TYPE}')/BTSCOPESet"
    rows = client_for(config.SVC_GENERIC).results(key)
    return [{"item_guid": r.get("WpItemGuid"), "type": r.get("WpType"),
             "description": r.get("WpDescription"), "status": r.get("WpStatus"),
             "wricef": r.get("Wricef"), "config_item": r.get("ConfigItem"),
             "sprint": r.get("Sprint"), "priority_id": r.get("PriorityId"),
             "value_points": r.get("ValuePoints"), "story_points": r.get("StoryPoints"),
             "text": r.get("Text")} for r in rows]


def withdraw_work_package(work_package_guid: str) -> dict:
    """Withdraw a Work Package (PPF action S1ITR_REJECT_SCOPE — 'Reject')."""
    g = _dash(work_package_guid)
    with SolmanClient(service=config.SVC_GENERIC) as c:
        c.function("PPF_ACTION", {"ActionDesc": "", "ActionId": "S1ITR_REJECT_SCOPE", "WsGuid": g})
    return {"work_package_guid": g, "withdrawn": True}

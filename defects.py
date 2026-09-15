"""Defect (S1DM) creation through the Test Suite's own service, TM_TWL_SRV.

WHERE THIS COMES FROM. Focused Build has no "create defect" in the generic
workspace app; defects are raised from a test execution in My Test Executions
(/sap/bc/ui5_ui5/salm/mango_tester). That app posts to
/sap/opu/odata/salm/TM_TWL_SRV/DefectCreationSet with the payload reproduced in
create_defect(), and the backend REQUIRES a test-package context:

    POST DefectCreationSet without TestPackageId -> 400
    "No TestPackageID for creation of defect provided."          (proven 16-Sep-2026)

So a defect is always born against a test package and a test case (the entity's
keys). That is not an API limitation to work around — it is how Gore's test
process ties every defect to the execution that found it.

STATUS: reachable and payload-complete, NOT yet exercised against a real test
package. Creating a defect on someone's live test plan is visible on their
dashboards, so the first real call should be on a package the caller owns and
the defect withdrawn afterwards if it was only a test.
"""
from __future__ import annotations

import config
from client import SolmanError, client_for

SVC_TWL = "/sap/opu/odata/salm/TM_TWL_SRV"


def _c():
    return client_for(SVC_TWL)


def test_packages(query: str = "", top: int = 100) -> list[dict]:
    """Test packages visible to this user (TM_TWL_SRV TestPackageSet), optionally
    filtered client-side on name/title — the set does not filter server-side."""
    rows = _c().results("TestPackageSet", {"$top": str(top)})
    out = [{"test_package_id": r.get("TestPackageId"), "name": r.get("TestPackageName"),
            "title": r.get("TestPackageTitle"), "priority": r.get("TpckPriority"),
            "responsible": r.get("TestPackageResponsible")} for r in rows]
    if query:
        q = query.lower()
        out = [x for x in out if q in (x["name"] or "").lower() or q in (x["title"] or "").lower()]
    return out


def test_cases(test_package_id: str, top: int = 200) -> list[dict]:
    """Test cases in a package — the second key a defect needs."""
    rows = _c().results("TestCaseSet", {"$filter": f"TestPackageId eq '{test_package_id}'",
                                        "$top": str(top)})
    return [{"test_case_id": r.get("TestCaseId"), "test_package_id": r.get("TestPackageId"),
             "title": r.get("TestCaseTitle") or r.get("Title") or r.get("Description"),
             "status": r.get("Status") or r.get("StatusText")} for r in rows]


def defaults(test_package_id: str, test_case_id: str) -> dict:
    """Default processor / reporter / support team the app would pre-fill."""
    d = _c().get(f"DefectCreationDefaultValuesSet(TestPackageId='{test_package_id}',"
                 f"TestCaseId='{test_case_id}')").get("d", {})
    return {k: v for k, v in d.items() if k != "__metadata"}


def value_helps() -> dict:
    """Priorities, defect categories, process types and the CSN component tree root."""
    c = _c()
    out = {}
    out["priorities"] = [{"id": r.get("Fieldname"), "name": r.get("Fieldvalue")}
                         for r in c.results("DefectPriorityValuesSet") if r.get("Fieldname")]
    out["categories"] = [{"id": r.get("DefCatId"), "name": r.get("DefCatDescription")}
                         for r in c.results("DefectCategorySet")]
    out["process_types"] = [{"id": r.get("ProcessType"), "name": r.get("Description")}
                            for r in c.results("DefectProcessTypeValuesSet")]
    out["components_root"] = [{"id": r.get("CompId"), "name": r.get("CompText"),
                               "parent": r.get("ParentId"), "selectable": bool(r.get("Selectable"))}
                              for r in c.results("SapCsnComponentValuesSet", {"$top": "50"})]
    return out


def create_defect(test_package_id: str, test_case_id: str, short_text: str, long_text: str,
                  priority: str = "2", reporter_bp: str = "", processor_bp: str = "",
                  support_team_bp: str = "", system_id: str = "", client: str = "",
                  installation: str = "", csn_component: str = "", category: str = "",
                  crm_category_guid: str = "", process_type: str = "S1DM",
                  exec_id: str = "", exec_run: int = 0, step_id: int = 0,
                  external_reference: str = "") -> dict:
    """Create a defect against a test case, exactly as My Test Executions does.

    Missing reporter/processor/support team are taken from the package's defaults.
    Returns the created defect id/guid as the service reports them.
    """
    if not test_package_id or not test_case_id:
        raise SolmanError("a defect needs TestPackageId and TestCaseId — the backend refuses "
                          "creation without a test-package context")
    if not (reporter_bp and processor_bp and support_team_bp):
        try:
            d = defaults(test_package_id, test_case_id)
            reporter_bp = reporter_bp or d.get("ReporterBP") or ""
            processor_bp = processor_bp or d.get("ProcessorBP") or ""
            support_team_bp = support_team_bp or d.get("MsgSupportTeamBp") or ""
        except Exception:  # noqa: BLE001 - defaults are a convenience
            pass
    body = {
        "TestPackageId": test_package_id, "TestCaseId": test_case_id,
        "ProcType": process_type, "ShortText": short_text[:40], "LongText": long_text,
        "Priority": str(priority), "ReporterBP": reporter_bp, "ProcessorBP": processor_bp,
        "MsgSupportTeamBp": support_team_bp, "Sysid": system_id, "Mandt": client or config.SAP_CLIENT,
        "Instn": installation, "CSNComponent": csn_component, "DefCatgy": category,
        "CrmCat": crm_category_guid, "ExecId": exec_id, "ExecRun": int(exec_run),
        "StepId": int(step_id), "FAttachEvidence": False, "ZZFLD00000B": external_reference,
    }
    created = _c().create("DefectCreationSet", body)
    return {"defect_id": created.get("DefectId"), "guid": created.get("MsgGuid"),
            "response": {k: v for k, v in created.items() if k != "__metadata" and v not in (None, "", 0, False)}}

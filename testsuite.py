"""SAP Test Suite — test cases, steps, xlsx upload/download, plan/package where-used.

Backed by TM_TS_DESIGNER_SRV (test-case content), TM_TS_PARAM_SRV (test-data
parameters) and TM_DASH_SRV (execution analytics). A test case is keyed by
(CaseId GUID, CaseVersion, Language). Grounded in live-captured API behavior and
the shipped tm_ts_des UI5 controller.
"""
from __future__ import annotations

import base64
from typing import Any

import config
from client import SolmanClient, SolmanError, client_for, odata_literal

SVC_TS = "/sap/opu/odata/salm/TM_TS_DESIGNER_SRV"
SVC_TS_PARAM = "/sap/opu/odata/salm/TM_TS_PARAM_SRV"
SVC_TM_DASH = "/sap/opu/odata/salm/TM_DASH_SRV"

DEFAULT_LANG = config.SAP_LANGUAGE or "EN"
_STATUS_SCHEMA_FALLBACK = "TESTCASE"


def _key(case_id: str, version: int = 1, lang: str = "") -> str:
    return (f"TestCaseHeaderSet(CaseId='{odata_literal(case_id)}',"
            f"CaseVersion={version},Language='{lang or DEFAULT_LANG}')")


# --------------------------------------------------------------------------
# Reads / search
# --------------------------------------------------------------------------
def list_test_cases(query: str = "", folder: str = "", top: int = 25) -> list[dict]:
    """List test cases (optionally by name substring and/or folder id).

    Both filters are server-side (substringof on Name is honored and fast here —
    unlike the requirement service). NOTE: rows come back ALPHABETICAL by name.
    """
    params: dict[str, Any] = {"$top": str(top)}
    flt = []
    if query:
        flt.append(f"substringof('{odata_literal(query)}',Name)")
    if folder:
        flt.append(f"Folder eq '{odata_literal(folder)}'")
    if flt:
        params["$filter"] = " and ".join(flt)
    rows = client_for(SVC_TS).results("TestCaseSet", params)
    return [_case_summary(r) for r in rows[:top]]


def _case_summary(r: dict) -> dict:
    return {"case_id": r.get("CaseId"), "version": r.get("CaseVersion"),
            "language": r.get("Language"), "name": r.get("Name"),
            "status": r.get("Status"), "status_label": r.get("StatusLabel"),
            "priority_label": r.get("PriorityLabel"), "folder": r.get("Folder"),
            "solution": r.get("SolDoc"), "responsible": r.get("PersonResponsibleText"),
            "is_library": r.get("LibraryObject"), "guid": r.get("Guid"),
            "changed_by": r.get("ChangedBy"), "changed_at": r.get("ChangedAt")}


def get_test_case(case_id: str, version: int = 1, lang: str = "") -> dict:
    """Read a test case header (description, status, priority, prerequisites, …)."""
    c = client_for(SVC_TS)
    d = c.get(_key(case_id, version, lang)).get("d", {})
    return {k: v for k, v in d.items() if not (isinstance(v, dict) and "__deferred" in v)}


def list_steps(case_id: str, version: int = 1, lang: str = "") -> list[dict]:
    """List a test case's steps (ordered), with description / expected result / instruction."""
    flt = (f"CaseId eq '{odata_literal(case_id)}' and CaseVersion eq {version} "
           f"and Language eq '{lang or DEFAULT_LANG}'")
    rows = client_for(SVC_TS).results("TestCaseStepSet", {"$filter": flt, "$top": "500"})
    rows.sort(key=lambda r: r.get("StepId") or 0)
    return [{"step_id": r.get("StepId"), "parent_id": r.get("ParentId"),
             "has_children": r.get("HasChildren"), "text": r.get("StepText"),
             "description": r.get("Description"), "expected_result": r.get("ExpectedResult"),
             "instruction": r.get("Instruction"), "executable": r.get("ExecutableName"),
             "evidence_required": r.get("Evidence")} for r in rows]


def where_used(case_id: str, version: int = 1, lang: str = "") -> dict:
    """Where a test case is used — the test plans and packages that contain it.

    Uses the header nav (TestCaseHeadertoWuTplnNav); the flat WuTpln/WuTpck sets
    require the same key context and return nothing when queried standalone.
    """
    c = client_for(SVC_TS)
    key = _key(case_id, version, lang)
    plans = c.results(f"{key}/TestCaseHeadertoWuTplnNav")
    return {
        "test_plans": [{"id": p.get("TplnId"), "text": p.get("TplnText"),
                        "release_status": p.get("RelStatValueTxt"),
                        "locations": p.get("LocationCount"), "url": p.get("UrlTpln")}
                       for p in plans],
    }


# --------------------------------------------------------------------------
# Test plans & packages (read — TM_DASH_SRV; management is classic STWB_2)
# --------------------------------------------------------------------------
def list_test_plans(solution: str = "", query: str = "", top: int = 50) -> list[dict]:
    """List test plans for a solution (TM_DASH requires a SolutionId).

    `solution` accepts a name/id ("P1M"); defaults to the configured solution.
    `query` filters TplnId/description client-side.
    """
    sid = _solution_id(solution)
    rows = client_for(SVC_TM_DASH).results(
        "TestPlanSet", {"$filter": f"SolutionId eq '{odata_literal(sid)}'", "$top": str(max(top, 100))})
    ql = query.lower()
    out = []
    for r in rows:
        if ql and ql not in (r.get("TplnId") or "").lower() and ql not in (r.get("TplnDescription") or "").lower():
            continue
        out.append({"plan_id": r.get("TplnId"), "guid": r.get("TplnGuid"),
                    "description": r.get("TplnDescription"),
                    "release_status": r.get("RelStatValueTxt"),
                    "responsible": r.get("ResponsibleName"),
                    "scope": r.get("ScopeName"), "test_class": r.get("TestClassTxt"),
                    "branch": r.get("BranchName")})
        if len(out) >= top:
            break
    return out


def list_test_packages(plan_guid: str, top: int = 100) -> list[dict]:
    """List the test packages under a test plan (by the plan's TplnGuid)."""
    rows = client_for(SVC_TM_DASH).results(
        "TestPackageSet", {"$filter": f"TplnGuid eq '{odata_literal(plan_guid)}'", "$top": str(top)})
    return [{"package_id": r.get("TpckId"), "guid": r.get("TpckGuid"),
             "plan_id": r.get("TplnId"), "priority": r.get("PriorityTxt"),
             "sequence": r.get("TflwId")} for r in rows]


def test_execution_status(solution: str = "", top: int = 100) -> list[dict]:
    """Test-status progress per plan (executed/failed/blocked) from the dashboard service."""
    sid = _solution_id(solution)
    rows = client_for(SVC_TM_DASH).results(
        "TestStatusProgressSet", {"$filter": f"SolutionId eq '{odata_literal(sid)}'", "$top": str(top)})
    return [{k: v for k, v in r.items() if not k.startswith("__") and not isinstance(v, dict)}
            for r in rows]


def _solution_id(solution: str) -> str:
    if not solution:
        return config.DEFAULT_SOLUTION_ID
    import solutions as _sol
    return _sol.resolve_solution(solution)["solution_id"]


# --------------------------------------------------------------------------
# Test data parameters (TM_TS_PARAM_SRV)
# --------------------------------------------------------------------------
def list_test_parameters(case_id: str, version: int = 1, lang: str = "") -> list[dict]:
    """List a test case's test-data parameters (variants) via TM_TS_PARAM_SRV."""
    flt = (f"CaseId eq '{odata_literal(case_id)}' and CaseVersion eq {version} "
           f"and Language eq '{lang or DEFAULT_LANG}'")
    rows = client_for(SVC_TS_PARAM).results("TestDataParameterSet", {"$filter": flt, "$top": "200"})
    return [{k: v for k, v in r.items() if not k.startswith("__") and not isinstance(v, dict)}
            for r in rows]


# --------------------------------------------------------------------------
# Lookups / help
# --------------------------------------------------------------------------
_HELP = {
    "folders": ("CustFolderSet", ("FolderId", "FolderLabel")),
    "status_schemas": ("CustStatusSchemaSet", ("StatusSchema", "StatusSchemaText")),
    "statuses": ("CustStatusSet", None),
    "priorities": ("CustPrioritySet", None),
    "solutions": ("HelpSolutionSet", ("SlanId", "SlanName", "SlanDesc")),
}


def test_lookup(kind: str, top: int = 50) -> list[dict]:
    if kind not in _HELP:
        raise ValueError(f"unknown lookup '{kind}'. options: {', '.join(_HELP)}")
    entityset, fields = _HELP[kind]
    rows = client_for(SVC_TS).results(entityset, {"$top": str(top)})
    if fields:
        return [{f: r.get(f) for f in fields} for r in rows]
    return [{k: v for k, v in r.items() if k != "__metadata" and not isinstance(v, dict)}
            for r in rows]


# --------------------------------------------------------------------------
# Writes
# --------------------------------------------------------------------------
def create_test_case(name: str, folder: str, solution: str = "NONE",
                     status_schema: str = "", is_library: bool = True,
                     lang: str = "") -> dict:
    """Create a test case (POST TestCaseSet, mirroring the designer's create form).

    `folder` is a folder id (see test_lookup('folders')). `solution` is a solution
    id or 'NONE'. `status_schema` defaults to the system's single schema. Returns
    the new CaseId/CaseVersion/Language.
    """
    if not name:
        raise ValueError("name is required")
    if not folder:
        raise ValueError("folder is required (see test_lookup('folders'))")
    if not status_schema:
        schemas = test_lookup("status_schemas")
        status_schema = schemas[0]["StatusSchema"] if schemas else _STATUS_SCHEMA_FALLBACK
    body = {
        "Name": name, "Folder": folder, "StatusSchema": status_schema,
        "LibraryObject": bool(is_library), "SolDoc": solution or "NONE",
        "UpdateLock": False, "LibraryId": "", "LibraryVersion": 0,
        "CaseId": "", "CaseVersion": 0, "SolDocAssigned": False,
        "Language": lang or DEFAULT_LANG,
    }
    created = client_for(SVC_TS).create("TestCaseSet", body)
    cid = created.get("CaseId")
    if not cid:
        raise SolmanError("Test case create returned no CaseId (check folder/status schema).")
    return {"case_id": cid, "version": created.get("CaseVersion"),
            "language": created.get("Language"), "name": created.get("Name"),
            "folder": created.get("Folder"), "status": created.get("Status")}


_HEADER_UPDATABLE = {"Description", "Prerequisites", "ExitCriteria", "Notes",
                     "Priority", "PersonResponsible", "TestingMode", "Duration",
                     "DurationUnit", "VersionLabel"}


def update_test_case(case_id: str, version: int = 1, lang: str = "", **fields: Any) -> dict:
    """MERGE-update editable header fields (Description/Prerequisites/ExitCriteria/
    Notes/Priority/PersonResponsible/TestingMode/Duration…)."""
    bad = set(fields) - _HEADER_UPDATABLE
    if bad:
        raise ValueError(f"non-updatable field(s): {sorted(bad)}. "
                         f"allowed: {sorted(_HEADER_UPDATABLE)}")
    if not fields:
        raise ValueError("no fields to update")
    client_for(SVC_TS).merge(_key(case_id, version, lang), fields)
    return {"case_id": case_id, "version": version, "updated": sorted(fields)}


def _step_row(case_id: str, version: int, lang: str, step_id: int, s: dict) -> dict:
    """Full-field step row — the deep save rejects payloads with missing fields."""
    return {"CaseId": case_id, "CaseVersion": version, "Language": lang,
            "StepId": step_id, "ParentId": int(s.get("parent_id", 0)),
            "Partner": s.get("partner", ""), "Description": s.get("description", ""),
            "Evidence": bool(s.get("evidence", False)),
            "Executable": s.get("executable", ""), "ExecutableName": s.get("executable_name", ""),
            "ExpectedResult": s.get("expected_result", ""),
            "LogCompId": "", "LogCompTxt": "", "Hidden": False,
            "Instruction": s.get("instruction", ""),
            "LibraryId": "", "LibraryVersion": 0, "OccidTmp": "",
            "ObjId": "", "AttachmentUrl": "", "Class": ""}


def set_steps(case_id: str, steps: list[dict], version: int = 1, lang: str = "",
              append: bool = False) -> dict:
    """Write a test case's steps — the way the designer UI saves (verified live).

    Steps are committed by ONE deep POST to TestCaseSet carrying the outer case,
    the full header, and ALL steps nested in TestCaseHeadertoStepsNav (a direct
    POST to TestCaseStepSet 201s but silently never saves). This REPLACES the
    step list unless append=True (existing steps are read and kept in front).

    Each step: {description, expected_result?, instruction?, evidence?, parent_id?}.
    """
    if not steps:
        raise ValueError("steps is empty")
    lang = lang or DEFAULT_LANG
    c = client_for(SVC_TS)

    rows = c.results("TestCaseSet",
                     {"$filter": f"CaseId eq '{odata_literal(case_id)}'", "$top": "1"})
    if not rows:
        raise SolmanError(f"test case {case_id} not found")
    case_row = rows[0]
    h = c.get(_key(case_id, version, lang)).get("d", {})

    existing: list[dict] = []
    if append:
        flt = (f"CaseId eq '{odata_literal(case_id)}' and CaseVersion eq {version} "
               f"and Language eq '{lang}'")
        for r in c.results("TestCaseStepSet", {"$filter": flt, "$top": "500"}):
            existing.append(_step_row(case_id, version, lang, r.get("StepId"), {
                "parent_id": r.get("ParentId"), "partner": r.get("Partner"),
                "description": r.get("Description"), "evidence": r.get("Evidence"),
                "executable": r.get("Executable"), "executable_name": r.get("ExecutableName"),
                "expected_result": r.get("ExpectedResult"), "instruction": r.get("Instruction")}))

    next_id = max((s["StepId"] for s in existing), default=0) + 1
    new_rows = [_step_row(case_id, version, lang, next_id + i, s)
                for i, s in enumerate(steps)]

    header = {"CaseId": case_id, "CaseVersion": version, "Language": lang,
              "TestingMode": h.get("TestingMode") or "1",
              "Description": h.get("Description") or "",
              "Prerequisites": h.get("Prerequisites") or "",
              "ExitCriteria": h.get("ExitCriteria") or "",
              "VersionLabel": h.get("VersionLabel") or "",
              "StatusSchema": h.get("StatusSchema"), "Status": h.get("Status"),
              "Priority": h.get("Priority") or "",
              "PersonResponsible": h.get("PersonResponsible") or "",
              "StepSequence": h.get("StepSequence") or "S",
              "Duration": h.get("Duration") or "", "DurationUnit": h.get("DurationUnit") or "",
              "LibraryId": h.get("LibraryId") or "", "LibraryVersion": h.get("LibraryVersion") or 0,
              "Notes": h.get("Notes") or "",
              "TestCaseHeadertoStepsNav": existing + new_rows,
              "TestCaseHeadertoBranchesNav": []}
    case = {"CaseId": case_id, "CaseVersion": version, "Language": lang,
            "Name": case_row.get("Name"), "Folder": case_row.get("Folder"),
            "SolDoc": case_row.get("SolDoc") or "NONE",
            "SolDocAssigned": bool(case_row.get("SolDocAssigned")),
            "LibraryObject": bool(case_row.get("LibraryObject")), "UpdateLock": True,
            "TestCasetoHeaderNav": [header]}
    c.create("TestCaseSet", case)

    saved = list_steps(case_id, version, lang)
    want = len(existing) + len(new_rows)
    if len(saved) != want:
        raise SolmanError(f"step save verify failed: expected {want} steps, found {len(saved)}")
    return {"case_id": case_id, "version": version, "steps": len(saved),
            "appended": len(new_rows) if append else None, "verified": True}


# --------------------------------------------------------------------------
# xlsx template flow (the "SFT upload" — Download Sample / Download / Upload)
# --------------------------------------------------------------------------
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

# File-header text (as in the standard template) -> internal attribute key.
# Captured from the designer's fieldspecification registry + i18n labels.
_UPLOAD_FIELD_KEYS = {
    "test case name": "CASE.NAME",                    # mandatory
    "folder": "CASE.FOLDER",
    "test case version title": "HEAD.VERSION_LABEL",
    "test case status schema": "HEAD.STATUS_SCHEMA",
    "test case status": "HEAD.STATUS",
    "test case priority": "HEAD.PRIORITY",
    "test case owner": "HEAD.PERSON_RESPONSIBLE",
    "test case notes": "HEAD.NOTES",
    "test case duration (min)": "HEAD.DURATION",
    "soldoc path occ id": "HEAD.SOLDOC_PATH_OCC_ID",  # mandatory when SolDoc-assigned
    "test case description": "HTXT.DESCRIPTION",
    "test case prerequisites": "HTXT.PREREQUISITES",
    "test case exit criteria": "HTXT.EXIT_CRITERIA",
    "step number": "STEP.STEP_INDEX",
    "step description": "STXT.DESCRIPTION",
    "step expected result": "STXT.EXPECTED_RESULT",
    "step test instruction": "STXT.INSTRUCTION",
    "step attachment": "SATT.OBJID",
    "step evidence": "STEP.EVIDENCE",
    "step executable": "STEP.EXECUTABLE",
    "logical component group": "STEP.EXECUTABLE_LCG",
    "step business partner": "STEP.PARTNER",
    "hidden": "STEP.HIDDEN",
}
_COL_LETTERS = [chr(ord("A") + i) for i in range(26)] + ["AA", "AB", "AC", "AD", "AE", "AF"]


def download_template(save_to: str) -> dict:
    """Download the standard test-case upload template (xlsx) to a local file."""
    c = client_for(SVC_TS)
    r = c._http.get(f"{c.service}/TestCaseDownloadSampleSet(FileName='')/$value")
    if r.status_code != 200:
        raise SolmanError(f"template download failed: HTTP {r.status_code}")
    from pathlib import Path
    Path(save_to).write_bytes(r.content)
    return {"saved_to": save_to, "size": len(r.content)}


def download_test_case_xlsx(case_id: str, save_to: str, version: int = 1) -> dict:
    """Download a test case (header + steps) as the upload-format xlsx."""
    c = client_for(SVC_TS)
    rows = c.results("TestCaseDownloadSet",
                     {"$filter": f"CaseId eq '{odata_literal(case_id)}' "
                                 f"and CaseVersion eq {version}", "$top": "1"})
    if not rows:
        raise SolmanError(f"no download cache for test case {case_id} v{version}")
    dcid = rows[0]["DownloadCacheId"]
    r = c._http.get(f"{c.service}/TestCaseDownloadSet(DownloadCacheId='{dcid}')/$value")
    if r.status_code != 200:
        raise SolmanError(f"download failed: HTTP {r.status_code}")
    from pathlib import Path
    Path(save_to).write_bytes(r.content)
    return {"case_id": case_id, "saved_to": save_to, "size": len(r.content)}


def upload_test_cases_xlsx(file_path: str, validate_only: bool = True,
                           first_row: int = 2, last_row: int | None = None,
                           branch: str = "", change_document: str = "",
                           with_executables: bool = False,
                           column_overrides: dict | None = None) -> dict:
    """Upload a filled test-case xlsx (the designer's Upload wizard, headless).

    Two-phase (verified): (1) POST the raw file to TestCaseUploadSet — the server
    parses it and returns an UploadCacheId plus the file's header texts per
    column; (2) UPDATE the cache entry with the column mapping
    (Col<letter> = "<ATTR.KEY>:<index>") + FirstRow/ValidateOnly. Headers matching
    the standard template are auto-mapped; column_overrides ({"C": "CASE.FOLDER"})
    wins over auto-mapping. DEFAULTS TO VALIDATE-ONLY — pass validate_only=False
    to actually create/update the test cases.
    """
    from pathlib import Path
    p = Path(file_path)
    if not p.is_file():
        raise ValueError(f"file not found: {file_path}")
    c = client_for(SVC_TS)

    # phase 1: raw bytes -> cache + parsed headers
    token = c._ensure_csrf()
    r = c._http.post(f"{c.service}/TestCaseUploadSet",
                     headers={"X-CSRF-Token": token, "Accept": "application/json",
                              "Content-Type": _XLSX_MIME},
                     content=p.read_bytes())
    if r.status_code not in (200, 201):
        raise SolmanError(f"upload phase 1 failed: HTTP {r.status_code} {r.text[:200]}")
    d = r.json().get("d", {})
    cache_id = d.get("UploadCacheId")
    if not cache_id:
        raise SolmanError("upload phase 1 returned no UploadCacheId")

    # phase 2: column mapping
    body: dict = {"UploadCacheId": cache_id, "MimeType": _XLSX_MIME, "Seperator": "",
                  "FirstRow": first_row, "LastRow": last_row,
                  "ValidateOnly": bool(validate_only),
                  "WithExecutables": bool(with_executables),
                  "Branch": branch, "ChangeDocument": change_document}
    mapped, unmapped = {}, []
    overrides = {k.upper(): v for k, v in (column_overrides or {}).items()}
    for idx, letter in enumerate(_COL_LETTERS):
        header = (d.get(f"Col{letter}") or "").strip()
        if not header:
            continue
        key = overrides.get(letter) or _UPLOAD_FIELD_KEYS.get(header.lower())
        if key == "HEAD.SOLDOC_PATH_OCC_ID" and not branch:
            # The wizard skips the SolDoc-path column unless "Upload into SolDoc"
            # is active (i.e. a branch is given) — mapping it without one 400s.
            unmapped.append(f"{letter}:{header} (SolDoc column skipped; pass branch= to use)")
            continue
        if key:
            body[f"Col{letter}"] = f"{key}:{idx}"
            mapped[letter] = f"{header} -> {key}"
        else:
            unmapped.append(f"{letter}:{header}")
    if "CASE.NAME" not in str(body):
        raise SolmanError(f"no column maps to CASE.NAME (Test Case Name) — mapped: {mapped}")

    c.merge(f"TestCaseUploadSet(UploadCacheId='{cache_id}')", body)
    return {"upload_cache_id": cache_id, "validate_only": bool(validate_only),
            "mapped_columns": mapped, "ignored_columns": unmapped,
            "note": "validation passed" if validate_only else "upload executed"}


def delete_test_case(case_id: str, version: int = 1, lang: str = "") -> dict:
    """Delete a test case (DELETE on the TestCaseSet key). Use for cleanup."""
    c = client_for(SVC_TS)
    lang = lang or DEFAULT_LANG
    key = (f"TestCaseSet(CaseId='{odata_literal(case_id)}',"
           f"CaseVersion={version},Language='{lang}')")
    token = c._ensure_csrf()
    r = c._http.request("DELETE", f"{c.service}/{key}",
                        headers={"X-CSRF-Token": token, "Accept": "application/json"})
    if r.status_code == 403 and "require" in r.headers.get("x-csrf-token", "").lower():
        c._csrf = None
        r = c._http.request("DELETE", f"{c.service}/{key}",
                            headers={"X-CSRF-Token": c._ensure_csrf(), "Accept": "application/json"})
    if r.status_code not in (200, 202, 204):
        raise SolmanError(f"delete failed: HTTP {r.status_code} {r.text[:200]}")
    return {"case_id": case_id, "deleted": True}

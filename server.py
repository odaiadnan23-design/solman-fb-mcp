"""MCP server for SAP Solution Manager Focused Build — Requirements Management.

Cookie-only, stdio. Never opens a browser: it loads the session cookie minted
out-of-band by refresh_session.py. If the session expires, tools return an
actionable error telling the user to re-run refresh_session.py.

Run:  python server.py     (stdio)
"""
from __future__ import annotations

import json

# mcp 2.x renamed FastMCP -> MCPServer. requirements.txt previously said
# `mcp>=1.27`, unpinned, so pip resolved 2.x and the v1 import broke the build.
# v2 targets the 2.x API and pins the major range.
from mcp.server.mcpserver import MCPServer

import attachments as att
import config
import charm
import defects
import documents as docs
import hardening
import rfc
import structures
import requirements as rq
import scout as sct
import soldoc as sd
import solutions as sol
import sqlcache as sqc
import testsuite as tst
import workpackages as wp
import workspaces as wsp
from client import SessionExpired, SolmanError
from client import preflight as client_preflight
from client import session_status as client_session_status

mcp = MCPServer("solman-fb")


def _branch_for(solution: str, branch_id: str) -> str | None:
    """Resolve an explicit branch id, or a solution name/id, to a branch id."""
    if branch_id:
        return branch_id
    if solution:
        return sol.resolve_context(solution)["branch_id"]
    return None


def _wrap(fn, *args, **kwargs) -> str:
    """Run a domain call, returning JSON text or a clean error string (never a stack trace)."""
    try:
        return json.dumps(fn(*args, **kwargs), indent=2, default=str)
    except SessionExpired as e:
        return f"SESSION EXPIRED: {e}"
    except hardening.ReadOnlyBlocked as e:
        return f"READ-ONLY: {e}"
    except rfc.RfcAuthority as e:
        return f"RFC AUTHORITY: {e}"
    except rfc.RfcUnavailable as e:
        return f"RFC UNAVAILABLE: {e}"
    except (SolmanError, ValueError, sct.ScoutError) as e:
        return f"ERROR: {e}"
    except Exception as e:  # noqa: BLE001 - surface a clean message, not a traceback
        return f"ERROR ({type(e).__name__}): {e}"


@mcp.tool()
def session_status() -> str:
    """Check whether the SolMan session is live. If not, run refresh_session.py to re-authenticate."""
    return _wrap(client_session_status)


@mcp.tool()
def list_process_types() -> str:
    """List the Focused Build object/transaction types on this system (Requirement, Work Package, Defect, …)."""
    return _wrap(wsp.list_process_types)


@mcp.tool()
def search_requirements(query: str = "", top: int = 25) -> str:
    """Search Focused Build requirements by title substring. Returns id, guid, title, status."""
    return _wrap(rq.search_requirements, query, top)


@mcp.tool()
def get_requirement(guid: str) -> str:
    """Read a requirement in full by its GUID (dashed WORKSPACE guid or plain 32-char)."""
    return _wrap(rq.get_requirement, guid)


@mcp.tool()
def list_requirements(solution: str = "", branch_id: str = "", status: str = "",
                      priority: str = "", owner: str = "", project: str = "",
                      query: str = "", top: int = 25, scan: int = 200) -> str:
    """List/filter requirements NEWEST FIRST, with FLP deep links, using trustworthy
    filter semantics (this gateway silently ignores most $filter fields — worked around).

    query = title substring (server-side). status ('Approved' or 'E0003') and solution/
    branch are client-side. owner/project/priority hydrate each candidate row (slower;
    scan caps the candidates considered). solution accepts a name/id ("PRD").
    """
    return _wrap(rq.list_requirements, solution, branch_id, status, priority,
                 owner, project, query, top, scan)


@mcp.tool()
def create_requirements_batch(items: list[dict], solution: str = "",
                              scope_id: str = "SAP_DEFAULT_SCOPE",
                              planned_project: str = "", planned_project_guid: str = "",
                              classification: str = "fit", priority: str = "2") -> str:
    """Create several requirements in one call; continues on per-row errors.

    items: [{title, description?, element_id?, external_reference?, classification?,
    priority?}, ...]. Shared solution/scope (names OK, resolved once) and project apply
    to all rows. Returns per-row id/guid/url or error. ALWAYS pass scope_id when
    attaching elements so links land in the right release scope.
    """
    return _wrap(rq.create_requirements_batch, items, solution, scope_id,
                 planned_project, planned_project_guid, classification, priority)


@mcp.tool()
def set_element_scope(requirement_guid: str, element_id: str, scope: str,
                      solution: str = "", branch_id: str = "") -> str:
    """Re-file an ALREADY-ATTACHED element link under a different scope (updates in place).

    scope accepts a name ("Release 5") or id; solution a name/id. Use when a link
    landed in SAP_DEFAULT_SCOPE by mistake."""
    return _wrap(rq.attach_element, requirement_guid, element_id, branch_id or None,
                 scope, solution)


@mcp.tool()
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
    solution_id: str = "",
    branch_id: str = "",
    planned_project: str = "",
    planned_project_guid: str = "",
    value: int = 0,
    effort: int = 0,
    element_id: str = "",
    scope_id: str = "SAP_DEFAULT_SCOPE",
    solution: str = "",
) -> str:
    """Create a Focused Build requirement (ProcessType S1BR).

    priority: '1' High | '2' Medium | '3' Low. classification: fit|gap|wricef|non-functional.
    Title is truncated to 40 chars. external_reference maps to the ZZFLD00000B custom field.
    solution accepts a NAME or id ("PRD", "QAS") — its Design branch is resolved
    automatically, and scope_id may then be a scope NAME ("Release 5"). In a non-default
    solution the env team/project defaults are NOT applied (pass planned_project explicitly).
    If element_id is provided, the Solution element is attached after creation under the
    resolved scope — always pass the target release/wave scope so the link is filed there
    rather than in the SAP_DEFAULT_SCOPE catch-all.
    """
    return _wrap(
        rq.create_requirement, title, priority, classification, description, remarks,
        suggested_solution, external_reference, category_id, owner_bp, owner_name,
        solution_id or None, branch_id or None, planned_project or None,
        planned_project_guid or None, value, effort, element_id, scope_id, solution,
    )


@mcp.tool()
def update_requirement(
    guid: str,
    title: str = "",
    description: str = "",
    remarks: str = "",
    suggested_solution: str = "",
    long_description: str = "",
    priority_name: str = "",
    external_reference: str = "",
) -> str:
    """Update editable fields on a requirement (MERGE). Only non-empty args are changed."""
    fields = {
        "RequirementTitle": title or None,
        "Description": description or None,
        "Remarks": remarks or None,
        "SuggestedSolution": suggested_solution or None,
        "LongDescription": long_description or None,
        "PriorityName": priority_name or None,
        "ZZFLD00000B": external_reference or None,
    }
    return _wrap(rq.update_requirement, guid, **fields)


@mcp.tool()
def list_requirement_actions(guid: str) -> str:
    """List the lifecycle actions currently available for a requirement (e.g. Withdraw, Send for Approval)."""
    return _wrap(rq.list_actions, guid)


@mcp.tool()
def withdraw_requirement(guid: str) -> str:
    """Withdraw (cancel) a requirement. Returns the resulting status. Lifecycle changes are actions, not field edits."""
    return _wrap(rq.withdraw_requirement, guid)


@mcp.tool()
def submit_requirement_for_approval(guid: str) -> str:
    """Send a requirement for approval (Draft -> To Be Approved). Returns the resulting status."""
    return _wrap(rq.submit_for_approval, guid)


@mcp.tool()
def approve_requirement(guid: str) -> str:
    """Approve a requirement (To Be Approved -> Approved). Required before a Work Package can be linked."""
    return _wrap(rq.approve_requirement, guid)


@mcp.tool()
def reject_requirement(guid: str) -> str:
    """Reject a requirement (To Be Approved -> Rejected)."""
    return _wrap(rq.reject_requirement, guid)


@mcp.tool()
def execute_requirement_action(guid: str, action_id: str) -> str:
    """Execute a specific lifecycle action by id (from list_requirement_actions). Returns resulting status."""
    return _wrap(rq.execute_action, guid, action_id)


@mcp.tool()
def search_solution_elements(query: str, branch_id: str = "", top: int = 15,
                             solution: str = "") -> str:
    """Search Solution Documentation elements by name substring (for attaching to a requirement).

    solution accepts a name/id ("PRD") — searches that solution's Design branch.
    """
    return _wrap(rq.search_solution_elements, query, branch_id, top, solution)


@mcp.tool()
def attach_element(requirement_guid: str, element_id: str, branch_id: str = "",
                   scope_id: str = "SAP_DEFAULT_SCOPE", solution: str = "") -> str:
    """Attach a Solution Documentation element (from search_solution_elements) to a requirement.

    solution accepts a name/id; scope_id may then be a scope NAME ("Release 5").
    Re-running with a different scope updates the existing link in place (re-scope).
    """
    return _wrap(rq.attach_element, requirement_guid, element_id, branch_id or None,
                 scope_id, solution)


@mcp.tool()
def list_requirement_elements(requirement_guid: str) -> str:
    """List the Solution Documentation elements attached to a requirement."""
    return _wrap(rq.list_elements, requirement_guid)


@mcp.tool()
def detach_element(requirement_guid: str, element_id: str, branch_id: str = "") -> str:
    """Detach a Solution Documentation element from a requirement (verified)."""
    return _wrap(rq.detach_element, requirement_guid, element_id, branch_id or None)


@mcp.tool()
def list_lookup(kind: str = "priorities", top: int = 50) -> str:
    """List reference values. kind: solutions|priorities|classifications|categories|statuses|projects."""
    return _wrap(rq.lookup, kind, top)


@mcp.tool()
def session_keepalive() -> str:
    """Touch the SolMan session so its server-side idle timer resets.

    Addresses the "Future: a periodic keepalive ping" note in the README. The
    client already auto-reloads the cookie when refresh_session.py rewrites it
    (it watches the file mtime), so the remaining friction is purely the
    server-side idle timeout. One cheap authenticated GET resets it.

    Call this on a timer from the client, or before a long batch, to avoid
    hitting SESSION EXPIRED mid-run. It cannot create a session -- if the
    session is already gone this reports that, and refresh_session.py is still
    the out-of-band way back in.
    """
    return _wrap(client_session_status)


@mcp.tool()
def sql_cache_status() -> str:
    """Health and STALENESS of the companion application's SQL cache.

    Ask before trusting a read: the cache is maintained by that other application,
    not by this server, so it can lag. Reports row counts and last sync time.
    """
    return _wrap(sqc.status)


@mcp.tool()
def sql_defect_search(text: str, top: int = 50) -> str:
    """Search cached defects by substring — with filtering that actually works.

    The SALM gateway silently DROPS most $filter predicates and miscounts
    $count; this queries SQL Server directly, so predicates and counts are real.
    READ-ONLY: non-SELECT statements are refused before reaching the driver.
    """
    return _wrap(sqc.defect_search, text, top)


@mcp.tool()
def sql_defect_summary() -> str:
    """Defect counts per project from the SQL cache (honest counts)."""
    return _wrap(sqc.defect_summary)


@mcp.tool()
def sql_defect_by_value_stream() -> str:
    """Defect counts by value stream and status — a query the gateway cannot answer."""
    return _wrap(sqc.defect_by_value_stream)


@mcp.tool()
def sql_table_inventory() -> str:
    """What the SQL cache holds, with row counts, to decide what is worth querying."""
    return _wrap(sqc.table_inventory)


@mcp.tool()
def list_projects() -> str:
    """List every planned project / release on the system.

    Where several releases run concurrently there is no default project. Sites
    commonly publish parallel release families (one per landscape), each split
    further by value stream.
    """
    return _wrap(sol.list_projects)


@mcp.tool()
def resolve_project(project: str) -> str:
    """Resolve a release NAME (or id, or unique substring) to its id and GUID.

    Matching: exact id > exact name > unique substring. Ambiguity RAISES and
    lists the candidates rather than picking one -- with concurrent releases,
    silently choosing would file work against the wrong release.
    """
    return _wrap(sol.resolve_project, project)


@mcp.tool()
def list_branches(solution_id: str) -> str:
    """List branches for a given solution id (from list_lookup 'solutions')."""
    return _wrap(rq.branches_for_solution, solution_id)


@mcp.tool()
def resolve_context(solution: str = "", branch: str = "", scope: str = "") -> str:
    """Resolve solution/branch/scope NAMES (or ids) to ids in one call.

    Examples: solution="PRD" -> its Design branch; solution="QAS", scope="Release 7"
    -> the branch + that release scope's id. Ambiguous names error with candidates.
    Use this before create/attach/browse when working outside the default solution.
    """
    return _wrap(sol.resolve_context, solution, branch, scope)


@mcp.tool()
def solution_overview(solution: str) -> str:
    """One-call orientation for a solution (by name or id): its branches, each with all scopes."""
    return _wrap(sol.solution_overview, solution)


# --- Solution Documentation hierarchy (dedicated tree service) -------------
@mcp.tool()
def soldoc_context(branch_id: str = "", solution: str = "") -> str:
    """SolDoc root context for a branch (solution/branch names). solution accepts a name/id ("PRD")."""
    return _wrap(lambda: sd.context(_branch_for(solution, branch_id)))


@mcp.tool()
def soldoc_list_scopes(branch_id: str = "", solution: str = "") -> str:
    """List SolDoc scopes (views) for a branch — 'Show All', team/release scopes. solution accepts a name/id."""
    return _wrap(lambda: sd.list_scopes(_branch_for(solution, branch_id)))


@mcp.tool()
def soldoc_browse(parent_element_id: str = "", branch_id: str = "",
                  scope: str = "SAP_DEFAULT_SCOPE", solution: str = "") -> str:
    """Browse the Solution Documentation tree. Empty parent = top-level nodes; else the node's children.

    Each node has element_id, name, type (PROC/PROCSTEP/FOLDER/…), has_children, selectable, path.
    `solution` accepts a name/id ("PRD"); `scope` accepts a scope id or NAME. Drill down by passing
    a node's element_id (where has_children is true). element_id is what attach_element consumes.
    """
    return _wrap(lambda: sd.browse(parent_element_id, _branch_for(solution, branch_id), scope))


@mcp.tool()
def soldoc_get_element(element_id: str, branch_id: str = "", solution: str = "") -> str:
    """Read a single SolDoc tree node (incl. full path) by element id. solution accepts a name/id."""
    return _wrap(lambda: sd.get_element(element_id, _branch_for(solution, branch_id)))


@mcp.tool()
def assign_structures(crm_guid: str, element_ids: list[str], branch_id: str = "",
                      solution: str = "", solution_name: str = "") -> str:
    """Assign SolDoc elements (structures) to a Work Package or Work Item, verified.

    element_ids from soldoc_browse/search_solution_elements. For REQUIREMENTS use
    attach_element instead. solution accepts a name/id ("PRD")."""
    return _wrap(lambda: sd.assign_structures(crm_guid, element_ids,
                                              _branch_for(solution, branch_id), solution_name))


@mcp.tool()
def list_structures(crm_guid: str, branch_id: str = "", solution: str = "") -> str:
    """List the SolDoc structures assigned to a Work Package / Work Item."""
    return _wrap(lambda: sd.list_structures(crm_guid, _branch_for(solution, branch_id)))


# --- Attachments (files + URL links; works on requirements, WPs, WIs) -------
@mcp.tool()
def list_attachments(guid: str, branch_id: str = "", solution: str = "") -> str:
    """List attachments (files + URL links) on a requirement/Work Package/Work Item by guid."""
    return _wrap(att.list_attachments, guid, branch_id, solution)


@mcp.tool()
def upload_attachment(guid: str, file_path: str = "", filename: str = "",
                      content_b64: str = "", mime_type: str = "",
                      branch_id: str = "", solution: str = "") -> str:
    """Attach a file to a requirement/WP/WI. Pass file_path (local file), OR filename +
    content_b64 for in-memory content. Upload is verified against the attachment list."""
    return _wrap(att.upload_attachment, guid, file_path, filename, content_b64,
                 mime_type, branch_id, solution)


@mcp.tool()
def attach_url(guid: str, url: str, title: str = "",
               branch_id: str = "", solution: str = "") -> str:
    """Attach a URL link to a requirement/WP/WI (appears as '<title>.URL' in its attachments)."""
    return _wrap(att.attach_url, guid, url, title, branch_id, solution)


@mcp.tool()
def download_attachment(guid: str, doc_id: str, save_to: str = "",
                        branch_id: str = "", solution: str = "") -> str:
    """Download an attachment (doc_id from list_attachments). Saves to save_to, else returns base64."""
    return _wrap(att.download_attachment, guid, doc_id, save_to, branch_id, solution)


@mcp.tool()
def delete_attachment(guid: str, doc_id: str, branch_id: str = "", solution: str = "") -> str:
    """Delete an attachment (file or URL link) from a requirement/WP/WI, verified."""
    return _wrap(att.delete_attachment, guid, doc_id, branch_id, solution)


# --- Work Packages ---------------------------------------------------------
@mcp.tool()
def create_work_package(
    requirement_guid: str,
    title: str,
    assign: bool = True,
    classification: str = "fit",
    priority: str = "",
    category_id: str = "",
    long_description: str = "",
) -> str:
    """Create a Work Package (ProcessType S1IT) and link it to its requirement.

    The requirement MUST be Approved first (use approve_requirement) — otherwise the link
    silently fails and this raises. Release/project targeting comes from SOLMAN_WP_* config.
    Returns {work_package_id, work_package_guid, assigned}.
    """
    return _wrap(wp.create_work_package, requirement_guid, title, assign, category_id,
                 classification, priority, "", "", long_description)


@mcp.tool()
def assign_work_package(requirement_guid: str, work_package_guid: str) -> str:
    """Link an existing Work Package to a requirement (requirement must be Approved). Self-verifies the link."""
    return _wrap(wp.assign_work_package, requirement_guid, work_package_guid)


@mcp.tool()
def withdraw_work_package(work_package_guid: str) -> str:
    """Withdraw a Work Package (rejects its scope)."""
    return _wrap(wp.withdraw_work_package, work_package_guid)


@mcp.tool()
def create_work_item(work_package_guid: str, description: str, wricef: str = "non-functional",
                     text: str = "", config_item: str = "", wp_type: str = "nc",
                     sprint: str = "", priority: str = "4", auto_scope: bool = True) -> str:
    """Create a Work Item (scope item) under a Work Package — persists (verified live).

    Handles the three commit requirements automatically: moves a fresh WP into Scoping
    (auto_scope), picks a valid technical component if config_item is omitted (see
    list_scope_components), and uses the deep-create that triggers the one-order save.
    wricef: fit|gap|wricef|non-functional. wp_type: nc (Normal Change S1MJ) | gc (General
    Change S1CG). Self-verified against list_work_items.
    """
    return _wrap(wp.create_work_item, work_package_guid, description, wricef, text,
                 config_item, wp_type, sprint, priority, auto_scope=auto_scope)


@mcp.tool()
def list_scope_components(work_package_guid: str, wp_type: str = "nc",
                          current_systems_only: bool = True) -> str:
    """List the valid technical components (ConfigItem value help) for Work Items under a WP.

    Empty result usually means the WP lacks a system landscape assignment. Pass a
    config_item from here to create_work_item (or let it auto-pick the first)."""
    return _wrap(wp.list_scope_components, work_package_guid, wp_type, current_systems_only)


@mcp.tool()
def list_work_items(work_package_guid: str) -> str:
    """List the Work Items (scope items) under a Work Package."""
    return _wrap(wp.list_work_items, work_package_guid)


# --- Generic workspace layer (any Focused Build object type) ---------------
@mcp.tool()
def search_workspaces(process_type: str, query: str = "", top: int = 25) -> str:
    """Search any Focused Build object type by title. process_type: friendly key (requirement,
    work_package, work_item_nc, defect, request_for_change, risk, master_work_package, …) or a
    code (S1BR/S1IT/…). Returns id, guid, type, title, status. See list_process_types."""
    return _wrap(wsp.search_workspaces, process_type, query, top)


@mcp.tool()
def get_workspace(id_or_guid: str, process_type: str) -> str:
    """Read a workspace header (any object type) by ObjectId or GUID + process_type."""
    return _wrap(wsp.get_workspace, id_or_guid, process_type)


@mcp.tool()
def list_workspace_actions(id_or_guid: str, process_type: str) -> str:
    """List the lifecycle actions available for any object (Defect/RfC/Risk/WP/…) by id + process_type."""
    return _wrap(wsp.list_actions, id_or_guid, process_type)


@mcp.tool()
def execute_workspace_action(id_or_guid: str, action_id: str, process_type: str) -> str:
    """Execute a lifecycle action (from list_workspace_actions) on any object. Returns resulting status."""
    return _wrap(wsp.execute_action, id_or_guid, action_id, process_type)


# --- Test Suite: test cases, steps, xlsx, plans/packages -------------------
@mcp.tool()
def list_test_cases(query: str = "", folder: str = "", top: int = 25) -> str:
    """List test cases by name substring and/or folder id (see test_lookup('folders'))."""
    return _wrap(tst.list_test_cases, query, folder, top)


@mcp.tool()
def get_test_case(case_id: str, version: int = 1, lang: str = "") -> str:
    """Read a test case header (description, status, priority, prerequisites, exit criteria)."""
    return _wrap(tst.get_test_case, case_id, version, lang)


@mcp.tool()
def list_test_case_steps(case_id: str, version: int = 1, lang: str = "") -> str:
    """List a test case's steps (ordered) with description/expected-result/instruction."""
    return _wrap(tst.list_steps, case_id, version, lang)


@mcp.tool()
def create_test_case(name: str, folder: str, solution: str = "NONE",
                     status_schema: str = "", is_library: bool = True, lang: str = "") -> str:
    """Create a test case. folder = a folder id (test_lookup('folders')); solution = id or 'NONE'."""
    return _wrap(tst.create_test_case, name, folder, solution, status_schema, is_library, lang)


@mcp.tool()
def update_test_case(case_id: str, version: int = 1, lang: str = "",
                     description: str = "", prerequisites: str = "", exit_criteria: str = "",
                     notes: str = "", priority: str = "", person_responsible: str = "") -> str:
    """MERGE-update a test case header. Only non-empty fields are sent."""
    fields = {k: v for k, v in {
        "Description": description, "Prerequisites": prerequisites,
        "ExitCriteria": exit_criteria, "Notes": notes, "Priority": priority,
        "PersonResponsible": person_responsible}.items() if v}
    return _wrap(tst.update_test_case, case_id, version, lang, **fields)


@mcp.tool()
def set_test_case_steps(case_id: str, steps: list[dict], version: int = 1,
                        lang: str = "", append: bool = False) -> str:
    """Write a test case's steps (deep-save, the only way that persists — a direct step
    POST silently no-ops). REPLACES the list unless append=True. Each step:
    {description, expected_result?, instruction?, evidence?, parent_id?}."""
    return _wrap(tst.set_steps, case_id, steps, version, lang, append)


@mcp.tool()
def delete_test_case(case_id: str, version: int = 1, lang: str = "") -> str:
    """Delete a test case (by key). Irreversible — use for throwaways/cleanup."""
    return _wrap(tst.delete_test_case, case_id, version, lang)


@mcp.tool()
def test_case_where_used(case_id: str, version: int = 1, lang: str = "") -> str:
    """List the test plans that contain a test case (with FLP/WebDynpro links)."""
    return _wrap(tst.where_used, case_id, version, lang)


@mcp.tool()
def download_test_case_template(save_to: str) -> str:
    """Download the standard test-case upload template (xlsx) to a local path."""
    return _wrap(tst.download_template, save_to)


@mcp.tool()
def download_test_case_xlsx(case_id: str, save_to: str, version: int = 1) -> str:
    """Download a test case (header + steps) as the upload-format xlsx."""
    return _wrap(tst.download_test_case_xlsx, case_id, save_to, version)


@mcp.tool()
def upload_test_cases_xlsx(file_path: str, validate_only: bool = True, first_row: int = 2,
                           branch: str = "", with_executables: bool = False) -> str:
    """Upload a filled test-case xlsx (auto-maps standard template headers -> attributes).
    DEFAULTS TO VALIDATE-ONLY; pass validate_only=False to actually create/update. Pass
    branch to enable the SolDoc-path column ('Upload into SolDoc')."""
    return _wrap(lambda: tst.upload_test_cases_xlsx(
        file_path, validate_only=validate_only, first_row=first_row,
        branch=branch, with_executables=with_executables))


@mcp.tool()
def list_test_plans(solution: str = "", query: str = "", top: int = 50) -> str:
    """List test plans for a solution (name/id, e.g. 'PRD'); query filters id/description."""
    return _wrap(tst.list_test_plans, solution, query, top)


@mcp.tool()
def list_test_packages(plan_guid: str, top: int = 100) -> str:
    """List the test packages under a test plan (plan_guid from list_test_plans)."""
    return _wrap(tst.list_test_packages, plan_guid, top)


@mcp.tool()
def test_execution_status(solution: str = "", top: int = 100) -> str:
    """Test-status progress per plan (executed/failed/blocked) for a solution."""
    return _wrap(tst.test_execution_status, solution, top)


@mcp.tool()
def list_test_parameters(case_id: str, version: int = 1, lang: str = "") -> str:
    """List a test case's test-data parameters (variants)."""
    return _wrap(tst.list_test_parameters, case_id, version, lang)


@mcp.tool()
def test_lookup(kind: str = "folders", top: int = 50) -> str:
    """Test-suite reference values. kind: folders|status_schemas|statuses|priorities|solutions."""
    return _wrap(tst.test_lookup, kind, top)


# --------------------------------------------------------------------------
# Customizing Scout -- cross-system configuration comparison
#
# These reach the managed systems directly through vsp's ADT data preview, not
# through SolMan. They are read-only, and Scout refuses any system not named in
# SCOUT_SYSTEMS, so production is unreachable unless someone deliberately types
# it into that allowlist.
# --------------------------------------------------------------------------
@mcp.tool()
def scout_systems() -> str:
    """Systems Customizing Scout may read, and whether each has a cached SSO session.

    A cached session is NOT proof it still works -- only a real read is. Start
    here to see what can be compared.
    """
    return _wrap(sct.systems)


@mcp.tool()
def scout_table_catalog(area: str = "") -> str:
    """Customizing tables Scout knows about, by area (FI, CO, AA, TAX, BANK, CROSS).

    A convenience list, not a limit: scout_compare accepts any table name.
    """
    return _wrap(sct.table_catalog, area)


@mcp.tool()
def scout_read(table: str, system: str, fields: str = "", where: str = "",
               top: int = 200, order: str = "") -> str:
    """Read one configuration table from one system (read-only).

    fields/where are passed to the ADT data preview: WHERE is capped near 255
    chars and ORDER BY ... DESC is rejected by the API -- both are reported
    rather than silently truncated.
    """
    return _wrap(sct.read, table, system, fields, where, top, order)


@mcp.tool()
def scout_compare(table: str, left: str, right: str, key: str = "",
                  fields: str = "", where: str = "", top: int = 500,
                  ignore: str = "") -> str:
    """Compare a configuration table between two systems -- the Customizing Scout diff.

    Rows are matched on the table's real primary key (read from DD03L; MANDT is
    dropped), then reported as identical, differing (with the field-level
    deltas), or present on one side only. If the row cap is hit the result says
    so and is explicitly PARTIAL -- do not read absence of differences past the
    cap as agreement.
    """
    return _wrap(sct.compare, table, left, right, key, fields, where, top, ignore)


@mcp.tool()
def scout_compare_area(area: str, left: str, right: str, top: int = 500) -> str:
    """Sweep every table in an area and report which ones differ between two systems.

    Use this to find WHERE two systems drifted before spending calls on WHAT
    drifted; then drill in with scout_compare. Tables that cannot be read are
    reported per table and do not stop the sweep. This makes two reads per
    table, so it is slow -- expect tens of seconds per table.
    """
    return _wrap(sct.compare_area, area, left, right, top)


# ==========================================================================
# Connection health and safety
# ==========================================================================
@mcp.tool()
def preflight() -> str:
    """Full connection health: TLS mode, cookie age and permissions, session validity,
    read-only state, whether write targets are configured, and whether the RFC read
    transport is available. Returns a `problems` list and `ok`.

    Run this FIRST in any session that will write, and before any long enumeration —
    it catches the failures that otherwise show up as a silently wrong answer
    (expired cookie mid-run, unverified TLS, an unset planned project that would file
    work outside the release)."""
    return _wrap(client_preflight)


@mcp.tool()
def harden_connection() -> str:
    """Restrict the session cookie file and state directory to the current user only.
    Run once per machine, or whenever preflight reports a broad ACL."""
    return _wrap(hardening.harden_cookie)


@mcp.tool()
def recent_writes(limit: int = 20) -> str:
    """The last N writes this server made (local journal): what, where, when, status.

    Every CREATE, MERGE and action-executing function import is recorded. Use it to
    answer "what did I just change?" without re-reading the system, and to reconstruct
    a bulk run that went wrong."""
    return _wrap(hardening.recent_writes, limit)


# ==========================================================================
# Cross-object discovery (RFC read transport)
# ==========================================================================
@mcp.tool()
def list_object_types() -> str:
    """Every Focused Build and ChaRM one-order type on this system, with its real name.

    Wider than the OData document-type list, which returns only ten Focused Build
    types: this includes work items, defects, defect corrections, test requests,
    tasks, releases, scope changes, and the ChaRM side (requests for change, normal
    and urgent changes, incidents, problems, service requests)."""
    return _wrap(rfc.focused_build_types)


@mcp.tool()
def find_documents(process_type: str = "", description_like: str = "",
                   object_id_like: str = "", max_rows: int = 2000) -> str:
    """Enumerate one-order documents of ANY type — the read the OData gateway cannot do.

    The gateway ignores $filter on key fields, repeats page one on $skip and caps
    $top at 100, so "every work package on this release" is unanswerable through it.
    This reads CRMD_ORDERADM_H directly and is complete.

        find_documents(process_type="S1IT", description_like="GMR6")
        find_documents(process_type="S1DM")            # every defect
        find_documents(object_id_like="20000077")      # a number range

    Use it to build an audit population, and to cross-check any population derived
    from requirement assignments — those can only find packages that HAVE a
    requirement, which is the wrong blind spot for an audit."""
    return _wrap(rfc.documents, process_type, description_like, object_id_like, max_rows)


@mcp.tool()
def release_inventory(description_like: str = "") -> str:
    """Count documents per object type for a release (matched on title substring).

    The one call that answers "what exists for this release, across requirements,
    work packages, work items, defects and changes" — where a release audit should
    start."""
    return _wrap(rfc.inventory, description_like)


@mcp.tool()
def document_texts(document_guid: str) -> str:
    """Every text note on a document: text id, line count, author, dates.

    Needs the FULL 32-character GUID from OData. Note which ids matter at Gore:
    S112 is the Release Manager's comment — that is how Change Control leaves
    feedback on your object, so read it before asking what is wrong. Text CONTENT
    is not readable (READ_TEXT is authority-blocked and STXL is compressed), but
    presence, size and authorship are, which is enough to audit whether a required
    note such as the CCB questionnaire is there."""
    return _wrap(rfc.texts, document_guid)


@mcp.tool()
def list_text_types(object_type: str = "CRM_ORDERH") -> str:
    """The text-id catalogue — what CR05, CR01, S112 and the rest actually mean."""
    return _wrap(rfc.text_types, object_type)


@mcp.tool()
def describe_table(table: str) -> str:
    """Field list for any table: name, position, type, length, key flag.

    Use it before read_table rather than guessing column names — a wrong column
    name comes back as a confusing TABLE_WITHOUT_DATA, not a helpful error."""
    return _wrap(rfc.describe_table, table)


@mcp.tool()
def read_table(table: str, fields: list[str], where: str = "",
               rowcount: int = 100, skip: int = 0) -> str:
    """Read any table this user is authorised for, through the RFC transport (read-only).

    Give explicit `fields`: the call builds a fixed 512-byte row and a wide table
    raises DATA_BUFFER_EXCEEDED. `where` is ABAP SQL, e.g.
    "PROCESS_TYPE = 'S1IT' AND DESCRIPTION LIKE '%GMR6%'".

    Caveat: RAW columns render truncated — a CRMD_ORDERADM_H GUID comes back as a
    16-character prefix shared by many documents, so join on OBJECT_ID and take
    full GUIDs from OData."""
    return _wrap(rfc.read_table, table, fields, where, rowcount, skip)


@mcp.tool()
def rfc_probe() -> str:
    """Whether the RFC read transport works here, and what it can call.

    Availability and authorisation are separate: the endpoint can be open while
    S_RFC withholds a given function group."""
    return _wrap(rfc.probe)



# ==========================================================================
# Any document, any type — Focused Build and ChaRM
# ==========================================================================
@mcp.tool()
def describe_document(ref: str, process_type: str = "", include_history: bool = True,
                      include_flow: bool = True) -> str:
    """Everything about ONE document of ANY type, by ObjectId or GUID: header and status,
    partners with names, related documents (requirement<->WP, WP<->work item,
    defect<->correction, RFC<->change), work items, transports, test cases, available
    lifecycle actions, text notes (id/size/author), full STATUS HISTORY (who moved it,
    when — OData has none of this) and resolved document flow.

    Works identically for requirements, work packages, work items, defects, defect
    corrections, test requests, and ChaRM requests for change, normal/urgent changes,
    incidents and problems. Pass process_type when you know it to save a lookup.
    Turn off history/flow for speed on bulk reads."""
    return _wrap(docs.describe, ref, process_type, include_history, include_flow)


@mcp.tool()
def document_status_history(ref: str, process_type: str = "") -> str:
    """Who moved a document through which statuses and when, oldest first.
    The only source for this — the OData layer holds current status only."""
    def _run():
        hdr = docs._resolve(ref, process_type)
        return docs.status_history(hdr["Guid"], hdr["ProcessType"])
    return _wrap(_run)


@mcp.tool()
def document_actions(ref: str, process_type: str = "") -> str:
    """Lifecycle actions available right now on any document (feed one to
    execute_workspace_action)."""
    return _wrap(docs.actions, ref, process_type)


@mcp.tool()
def resolve_guids(guids: list[str]) -> str:
    """Full 32-character document GUIDs -> id, type and description."""
    return _wrap(rfc.resolve_guids, guids)


# ==========================================================================
# Requirement <-> work package assignment
# ==========================================================================
@mcp.tool()
def requirement_authorized_actions() -> str:
    """What this user may do in Requirements Management (create, assign/unassign
    structure, create/assign/unassign work package…). Read-only."""
    return _wrap(rq.authorized_actions)


@mcp.tool()
def check_unassign_work_package(requirement_guid: str, work_package_guid: str) -> str:
    """Ask the system whether ONE work package may be unassigned from a requirement.
    Read-only; an empty message list means no objection."""
    return _wrap(rq.check_unassign_work_package, requirement_guid, work_package_guid)


@mcp.tool()
def unassign_work_package(requirement_guid: str, work_package_guid: str,
                          force: bool = False) -> str:
    """Unassign ONE work package from a requirement (wpUnassignmentFromRequirement, POST).

    Unlike the Fiori unassign, which clears EVERY work-package link on the requirement
    and resets its status, this targets a single pair. Runs the check first and refuses
    on objection unless force=True; verifies by re-reading the requirement afterwards.
    Journalled; blocked by SOLMAN_READONLY.

    NOT YET EXERCISED LIVE — implemented and reachable, but every candidate pair on
    16-Sep-2026 was a link someone wanted kept. Treat the first real call as a test on
    a pair you could re-create."""
    return _wrap(rq.unassign_work_package, requirement_guid, work_package_guid, force)


@mcp.tool()
def assign_existing_work_package(requirement_guid: str, work_package_guid: str) -> str:
    """Assign an existing work package to a requirement (Assign_Existing_Wp). Requirement
    must be Approved and the work package in Scoping. Verified via the WP's related
    transactions afterwards."""
    return _wrap(rq.assign_existing_work_package, requirement_guid, work_package_guid)


# ==========================================================================
# System, people, function discovery
# ==========================================================================
@mcp.tool()
def system_info() -> str:
    """SID, host, database and release of the connected system (RFC_SYSTEM_INFO)."""
    return _wrap(rfc.system_info)


@mcp.tool()
def user_detail(user_id: str) -> str:
    """Resolve an SAP user id (as seen in CreatedBy, text authors, status history) to a
    person: name, email, department (BAPI_USER_GET_DETAIL)."""
    return _wrap(rfc.user_detail, user_id)


@mcp.tool()
def rfc_function_search(pattern: str, group: str = "*") -> str:
    """Find RFC-enabled function modules by name pattern, e.g. "*READ_TABLE*".
    Discovery only — whether you may CALL one is a separate S_RFC question; the
    transport's allowlist holds the ones proven callable here."""
    return _wrap(rfc.function_search, pattern, group)


@mcp.tool()
def rfc_function_interface(function_module: str) -> str:
    """Import/export/table parameters of a function module (RFC_GET_FUNCTION_INTERFACE)."""
    return _wrap(rfc.function_interface, function_module)



# ==========================================================================
# Structure assignment (the WP-side of Solution Documentation)
# ==========================================================================
@mcp.tool()
def list_structure_assignments(crm_guid: str, branch_id: str = "") -> str:
    """Every Solution Documentation structure assigned to a work package, work item or
    requirement (DROP_DOC_SRV, as the Documentation app reads it). This is the
    assignment that survives a requirement re-scope and feeds the work item's scope
    documents — compare it against the requirement's elements."""
    return _wrap(structures.list_assignments, crm_guid, branch_id)


@mcp.tool()
def wricef_structures_in_scope(crm_guid: str, branch_id: str = "") -> str:
    """The WRICEF structures (IDD/EDD/FDD/RDD…) assigned to a document — catches a FIT
    package still carrying an interface one level below the requirement."""
    return _wrap(structures.wricef_in_scope, crm_guid, branch_id)


@mcp.tool()
def unassign_structure(crm_guid: str, structure_id: str = "", name_prefix: str = "",
                       branch_id: str = "") -> str:
    """Remove ONE structure from a work package's assignment — the API form of the
    Documentation app's "Unassign selected structures" (PUT RelevantStructureSet with the
    app's unassign action). Give structure_id, or name_prefix such as "IDD0816".

    Works while the package is in Scoping; past that the backend answers "Customizing
    settings prevent assignment changes" (proven: 2000007713 in Scoping -> removed;
    2000007715 in To Be Developed -> refused). Verified by re-read; journalled."""
    def _run():
        if name_prefix:
            return structures.unassign_by_name(crm_guid, name_prefix, branch_id)
        if not structure_id:
            raise ValueError("give structure_id or name_prefix")
        return structures.unassign(crm_guid, structure_id, branch_id)
    return _wrap(_run)


@mcp.tool()
def assign_structure(crm_guid: str, structure_id: str, structure_type: str = "",
                     branch_id: str = "") -> str:
    """Assign a structure to a document via DiagramAssignStructures (POST import).
    Implemented from the app source; not yet exercised live."""
    return _wrap(structures.assign, crm_guid, structure_id, structure_type, branch_id)


# ==========================================================================
# Text notes and the work item component
# ==========================================================================
@mcp.tool()
def list_text_note_types(config_id: int = 2) -> str:
    """Text note types the Work Package app offers (configId 2: S115 Description, S114 Memo,
    S105 Comment, role comments S108/S109/S112/S113)."""
    return _wrap(charm.text_types, config_id)


@mcp.tool()
def read_text_notes(guid: str, process_type: str = "S1IT", config_id: int = 2) -> str:
    """Text notes on a document WITH CONTENT. BTTEXTSet is empty without the numeric
    ConfigId filter the Fiori app always sends — that is the whole trick."""
    return _wrap(charm.read_texts, guid, process_type, config_id)


@mcp.tool()
def write_text_note(guid: str, text_type_id: str, value: str, process_type: str = "S1IT",
                    config_id: int = 2) -> str:
    """Write a text note (e.g. S114 Memo, S115 Description) on a work package — how the CCB
    questionnaire gets onto a package without Fiori. Goes through a $batch changeset
    because a direct MERGE answers 501. Verified by re-read; journalled."""
    return _wrap(charm.write_text, guid, text_type_id, value, process_type, config_id)


@mcp.tool()
def set_work_item_component(work_package_guid: str, work_item_guid: str, config_item: str,
                            ibase_instance: str, system_id: str, client: str = "",
                            cmp_desc: str = "") -> str:
    """Set the technical component (Productive System) on an existing work item. The
    backend keeps it only when WpSystem is SID:CLIENT and ConfigItem/IbaseInstance/CmpDesc
    are sent together — which is why API-created items were blank. Proven on 27 items."""
    return _wrap(wp.set_work_item_component, work_package_guid, work_item_guid, config_item,
                 ibase_instance, system_id, client, cmp_desc)


# ==========================================================================
# Creating documents: requests for change, defects
# ==========================================================================
@mcp.tool()
def create_request_for_change(title: str, description: str, requester_bp: str,
                              priority: str = "2", category: str = "", change_manager_bp: str = "",
                              sold_to_party_bp: str = "", actual_release: str = "",
                              type_id: str = "S1CR", external_reference: str = "") -> str:
    """Create a Request for Change through WS_REQUEST_CHANGESet, the generic app's own path.
    type_id S1CR is what the app creates; a ChaRM type (ZMCR) is passed through for the
    backend to accept or refuse — read the created document's ProcessType back."""
    return _wrap(charm.create_request_for_change, title, description, requester_bp, priority,
                 category, change_manager_bp, sold_to_party_bp, actual_release, type_id,
                 external_reference)


@mcp.tool()
def list_test_packages_for_defects(query: str = "", top: int = 100) -> str:
    """Test packages a defect can be raised against (TM_TWL_SRV)."""
    return _wrap(defects.test_packages, query, top)


@mcp.tool()
def list_test_cases_in_package(test_package_id: str, top: int = 200) -> str:
    """Test cases in a package — the second key a defect needs."""
    return _wrap(defects.test_cases, test_package_id, top)


@mcp.tool()
def defect_value_helps() -> str:
    """Priorities, defect categories, process types and CSN component roots for create_defect."""
    return _wrap(defects.value_helps)


@mcp.tool()
def create_defect(test_package_id: str, test_case_id: str, short_text: str, long_text: str,
                  priority: str = "2", reporter_bp: str = "", processor_bp: str = "",
                  support_team_bp: str = "", system_id: str = "", client: str = "",
                  installation: str = "", csn_component: str = "", category: str = "",
                  external_reference: str = "") -> str:
    """Create a defect (S1DM) against a test case, exactly as My Test Executions does
    (TM_TWL_SRV DefectCreationSet). The backend refuses creation without a test-package
    context — that is Gore's process, not an API gap. Missing reporter/processor/team come
    from the package defaults. Not yet exercised on a live test package."""
    return _wrap(defects.create_defect, test_package_id, test_case_id, short_text, long_text,
                 priority, reporter_bp, processor_bp, support_team_bp, system_id, client,
                 installation, csn_component, category, "", "S1DM", "", 0, 0, external_reference)


if __name__ == "__main__":
    config.require_host()   # fail fast with a clear message if .env isn't configured
    mcp.run()


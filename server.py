"""MCP server for SAP Solution Manager Focused Build — Requirements Management.

Cookie-only, stdio. Never opens a browser: it loads the session cookie minted
out-of-band by refresh_session.py. If the session expires, tools return an
actionable error telling the user to re-run refresh_session.py.

Run:  python server.py     (stdio)
"""
from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

import attachments as att
import config
import requirements as rq
import soldoc as sd
import solutions as sol
import workpackages as wp
import workspaces as wsp
from client import SessionExpired, SolmanError, session_status

mcp = FastMCP("solman-fb")


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
    except (SolmanError, ValueError) as e:
        return f"ERROR: {e}"
    except Exception as e:  # noqa: BLE001 - surface a clean message, not a traceback
        return f"ERROR ({type(e).__name__}): {e}"


@mcp.tool()
def session_status() -> str:
    """Check whether the SolMan session is live. If not, run refresh_session.py to re-authenticate."""
    return _wrap(session_status)


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
    solution accepts a NAME or id ("P1M", "S4P") — its Design branch is resolved
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

    solution accepts a name/id ("P1M") — searches that solution's Design branch.
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
def list_branches(solution_id: str) -> str:
    """List branches for a given solution id (from list_lookup 'solutions')."""
    return _wrap(rq.branches_for_solution, solution_id)


@mcp.tool()
def resolve_context(solution: str = "", branch: str = "", scope: str = "") -> str:
    """Resolve solution/branch/scope NAMES (or ids) to ids in one call.

    Examples: solution="P1M" -> its Design branch; solution="S4P", scope="Release 7"
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
    """SolDoc root context for a branch (solution/branch names). solution accepts a name/id ("P1M")."""
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
    `solution` accepts a name/id ("P1M"); `scope` accepts a scope id or NAME. Drill down by passing
    a node's element_id (where has_children is true). element_id is what attach_element consumes.
    """
    return _wrap(lambda: sd.browse(parent_element_id, _branch_for(solution, branch_id), scope))


@mcp.tool()
def soldoc_get_element(element_id: str, branch_id: str = "", solution: str = "") -> str:
    """Read a single SolDoc tree node (incl. full path) by element id. solution accepts a name/id."""
    return _wrap(lambda: sd.get_element(element_id, _branch_for(solution, branch_id)))


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


if __name__ == "__main__":
    config.require_host()   # fail fast with a clear message if .env isn't configured
    mcp.run()

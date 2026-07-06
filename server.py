"""MCP server for SAP Solution Manager Focused Build — Requirements Management (PM1).

Cookie-only, stdio. Never opens a browser: it loads the session cookie minted
out-of-band by pm1_refresh.py. If the session expires, tools return an
actionable error telling the user to re-run pm1_refresh.py.

Run:  python server.py     (stdio)
"""
from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

import config
import requirements as rq
import soldoc as sd
import workpackages as wp
import workspaces as wsp
from client import SessionExpired, SolmanError, session_status

mcp = FastMCP("solman-fb")


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
    """Check whether the PM1 session is live. If not, run pm1_refresh.py to re-authenticate."""
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
) -> str:
    """Create a Focused Build requirement (ProcessType S1BR).

    priority: '1' High | '2' Medium | '3' Low. classification: fit|gap|wricef|non-functional.
    Title is truncated to 40 chars. external_reference maps to the ZZFLD00000B custom field.
    Env fields default to the S4P context if blank; set planned_project + planned_project_guid
    together to file under a specific project. If element_id is provided, the Solution element
    is attached after creation.
    """
    return _wrap(
        rq.create_requirement, title, priority, classification, description, remarks,
        suggested_solution, external_reference, category_id, owner_bp, owner_name,
        solution_id or None, branch_id or None, planned_project or None,
        planned_project_guid or None, value, effort, element_id,
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
def search_solution_elements(query: str, branch_id: str = "", top: int = 15) -> str:
    """Search Solution Documentation elements by name substring (for attaching to a requirement)."""
    return _wrap(rq.search_solution_elements, query, branch_id, top)


@mcp.tool()
def attach_element(requirement_guid: str, element_id: str, branch_id: str = "",
                   scope_id: str = "SAP_DEFAULT_SCOPE") -> str:
    """Attach a Solution Documentation element (from search_solution_elements) to a requirement."""
    return _wrap(rq.attach_element, requirement_guid, element_id, branch_id or None, scope_id)


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


# --- Solution Documentation hierarchy (dedicated tree service) -------------
@mcp.tool()
def soldoc_context(branch_id: str = "") -> str:
    """SolDoc root context for a branch (solution/branch names). Defaults to S4P Design branch."""
    return _wrap(sd.context, branch_id or None)


@mcp.tool()
def soldoc_list_scopes(branch_id: str = "") -> str:
    """List SolDoc scopes (views) for a branch — e.g. 'Show All', team/release scopes used for WP/WI scoping."""
    return _wrap(sd.list_scopes, branch_id or None)


@mcp.tool()
def soldoc_browse(parent_element_id: str = "", branch_id: str = "", scope: str = "SAP_DEFAULT_SCOPE") -> str:
    """Browse the Solution Documentation tree. Empty parent = top-level nodes; else the node's children.

    Each node has element_id, name, type (PROC/PROCSTEP/FOLDER/…), has_children, selectable, path.
    `scope` accepts a scope id or name (from soldoc_list_scopes). Drill down by passing a node's
    element_id (where has_children is true). element_id is the same id used by attach_element.
    """
    return _wrap(sd.browse, parent_element_id, branch_id or None, scope)


@mcp.tool()
def soldoc_get_element(element_id: str, branch_id: str = "") -> str:
    """Read a single SolDoc tree node (incl. full path) by element id."""
    return _wrap(sd.get_element, element_id, branch_id or None)


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

"""Solution Documentation hierarchy navigation.

Uses the dedicated **soldoc_node_selection_srv** service — a real tree API with
parent/child links (ParentElementId, HasChilds, Level), unlike the flat capped
ELEMENTSet search. This is the structure that requirement elements attach to and
that drives Work Package / Work Item scoping.

Tree model:
  CrmObjectSet(CrmId='',BranchId=<branch>)               -> root context (+ scopes)
    /elementsTree?$filter=ScopeId eq '<scope>'           -> top-level nodes
    /elementsTree?$filter=ParentElementId eq '<id>' and ScopeId eq '<scope>'  -> children
Node types: E2EROOT / LIBROOT / FOLDER / PROC / PROCSTEP / *LOGCOMP (executables/interfaces).
"""
from __future__ import annotations

import config
from client import SolmanClient, odata_literal

def _branch(b: str | None) -> str:
    return b or config.DEFAULT_BRANCH_ID


def _crm_key(branch: str) -> str:
    return f"CrmObjectSet(CrmId='',BranchId='{branch}')"


def _node(x: dict) -> dict:
    return {
        "element_id": x.get("ElementId"),
        "name": x.get("ElementName"),
        "type": x.get("ElementTypeId"),
        "type_name": x.get("ElementTypeName"),
        "level": x.get("Level"),
        "has_children": bool(x.get("HasChilds")),
        "selectable": x.get("Selectable") in ("X", True, "true"),
        "parent_id": x.get("ParentElementId"),
        "path": x.get("Path"),
    }


def context(branch: str | None = None) -> dict:
    """Root context for a branch: solution/branch names."""
    b = _branch(branch)
    with SolmanClient(service=config.SVC_SOLDOC) as c:
        d = c.get(_crm_key(b)).get("d", {})
    return {"branch_id": b, "branch_name": d.get("BranchName"), "solution_name": d.get("SolutionName")}


def list_scopes(branch: str | None = None) -> list[dict]:
    """List the scopes (views) available for a branch — e.g. 'Show All', team/release scopes."""
    b = _branch(branch)
    with SolmanClient(service=config.SVC_SOLDOC) as c:
        rows = c.results(f"{_crm_key(b)}/scopes", {"$top": "200"})
    return [{"scope_id": r.get("ScopeId"), "name": r.get("ScopeName")} for r in rows]


def resolve_scope(name_or_id: str, branch: str | None = None) -> str:
    """Accept a scope id or a (case-insensitive) scope name; return the scope id."""
    if not name_or_id or name_or_id == config.DEFAULT_SCOPE:
        return config.DEFAULT_SCOPE
    for s in list_scopes(branch):
        if s["scope_id"] == name_or_id or (s["name"] or "").lower() == name_or_id.lower():
            return s["scope_id"]
    return name_or_id  # assume it's already an id


def browse(parent_element_id: str = "", branch: str | None = None,
           scope: str = config.DEFAULT_SCOPE, top: int = 200) -> list[dict]:
    """List tree nodes: top-level when parent_element_id is empty, else the node's children."""
    b = _branch(branch)
    scope_id = resolve_scope(scope, b)
    flt = f"ScopeId eq '{odata_literal(scope_id)}'"
    if parent_element_id:
        flt += f" and ParentElementId eq '{odata_literal(parent_element_id)}'"
    with SolmanClient(service=config.SVC_SOLDOC) as c:
        rows = c.results(f"{_crm_key(b)}/elementsTree", {"$filter": flt, "$top": str(top)})
    return [_node(x) for x in rows]


def get_element(element_id: str, branch: str | None = None) -> dict:
    """Resolve a single element (incl. full Path) by id.

    The tree service exposes no single-key GET, so this uses the searchable
    BUSINESS_REQUIREMENTS_SRV/ELEMENTSet (same ElementId space).
    """
    b = _branch(branch)
    with SolmanClient(service=config.SVC_BIZ_REQ) as c:
        rows = c.results("ELEMENTSet", {"$filter": f"BranchId eq '{odata_literal(b)}' and ElementId eq '{odata_literal(element_id)}'", "$top": "1"})
    return _node(rows[0]) if rows else {}

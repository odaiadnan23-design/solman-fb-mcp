"""Solution / branch / scope resolution by NAME or id — the multi-solution layer.

A SolMan system hosts several solutions (each with branches, each branch with
scopes). Tools accept friendly names ("P1M", "Design", "Release 5") and this
module resolves them to ids, with caching and loud ambiguity errors.

Matching rules (case-insensitive):
  1. exact id match wins
  2. exact name match wins
  3. unique substring match wins
  4. several substring matches -> ValueError listing the candidates
"""
from __future__ import annotations

import time
from typing import Any

import config
import soldoc
from client import SolmanClient, client_for

_TTL = 600.0  # seconds; solution/branch/scope lists change rarely
_cache: dict[str, tuple[float, Any]] = {}


def _cached(key: str, loader) -> Any:
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    val = loader()
    _cache[key] = (time.time(), val)
    return val


def clear_cache() -> None:
    _cache.clear()


def _match(query: str, rows: list[dict], id_key: str, name_key: str, kind: str) -> dict:
    """Resolve query against rows by id, exact name, then unique substring."""
    q = (query or "").strip()
    ql = q.lower()
    for r in rows:
        if r.get(id_key) == q:
            return r
    exact = [r for r in rows if (r.get(name_key) or "").lower() == ql]
    if len(exact) == 1:
        return exact[0]
    subs = [r for r in rows if ql in (r.get(name_key) or "").lower()]
    if len(subs) == 1:
        return subs[0]
    if len(subs) > 1:
        names = ", ".join(repr(r.get(name_key)) for r in subs[:8])
        raise ValueError(f"{kind} {q!r} is ambiguous - candidates: {names}")
    names = ", ".join(repr(r.get(name_key)) for r in rows[:12])
    raise ValueError(f"no {kind} matches {q!r}. Available: {names}")


# --------------------------------------------------------------------------
# Solutions & branches
# --------------------------------------------------------------------------
def list_solutions() -> list[dict]:
    def load() -> list[dict]:
        rows = client_for(config.SVC_BIZ_REQ).results("SOLUTIONSet", {"$top": "50"})
        return [{"solution_id": r.get("SolutionId"), "name": r.get("SolutionDescription")}
                for r in rows]
    return _cached("solutions", load)


def list_branches(solution_id: str) -> list[dict]:
    def load() -> list[dict]:
        rows = client_for(config.SVC_BIZ_REQ).results(f"SOLUTIONSet('{solution_id}')/Branchs")
        return [{"branch_id": r.get("BranchId"), "name": r.get("BranchName"),
                 "type": r.get("BranchType")} for r in rows]
    return _cached(f"branches:{solution_id}", load)


def resolve_solution(name_or_id: str) -> dict:
    """'P1M' / 'S4P' / full name / id -> {'solution_id', 'name'}."""
    return _match(name_or_id, list_solutions(), "solution_id", "name", "solution")


def resolve_context(solution: str = "", branch: str = "", scope: str = "") -> dict:
    """Resolve any mix of solution/branch/scope names or ids to a full context.

    Defaults: no solution -> the configured default branch (env); no branch ->
    the solution's Design branch (or its only branch); no scope -> default scope.
    """
    if solution:
        sol = resolve_solution(solution)
        branches = list_branches(sol["solution_id"])
        if branch:
            br = _match(branch, branches, "branch_id", "name", "branch")
        else:
            design = [b for b in branches if b.get("type") == "DESG" or
                      (b.get("name") or "").lower() == "design"]
            br = design[0] if design else branches[0]
        ctx = {"solution_id": sol["solution_id"], "solution_name": sol["name"],
               "branch_id": br["branch_id"], "branch_name": br["name"]}
    elif branch:
        # branch given without solution: try id across all solutions
        for s in list_solutions():
            for b in list_branches(s["solution_id"]):
                if b["branch_id"] == branch:
                    ctx = {"solution_id": s["solution_id"], "solution_name": s["name"],
                           "branch_id": b["branch_id"], "branch_name": b["name"]}
                    break
            else:
                continue
            break
        else:
            raise ValueError(f"branch id {branch!r} not found in any solution "
                             "(pass solution= to resolve a branch by name)")
    else:
        ctx = {"solution_id": config.DEFAULT_SOLUTION_ID, "solution_name": "",
               "branch_id": config.DEFAULT_BRANCH_ID, "branch_name": ""}
        if not ctx["branch_id"]:
            raise ValueError("no solution given and no SOLMAN_BRANCH_ID default configured")

    if scope and scope != config.DEFAULT_SCOPE:
        scopes = _cached(f"scopes:{ctx['branch_id']}",
                         lambda: soldoc.list_scopes(ctx["branch_id"]))
        sc = _match(scope, scopes, "scope_id", "name", "scope")
        ctx["scope_id"], ctx["scope_name"] = sc["scope_id"], sc["name"]
    else:
        ctx["scope_id"], ctx["scope_name"] = config.DEFAULT_SCOPE, "Show All"
    return ctx


def solution_overview(solution: str) -> dict:
    """One-call orientation: a solution's branches and each branch's scopes."""
    sol = resolve_solution(solution)
    branches = []
    for b in list_branches(sol["solution_id"]):
        scopes = _cached(f"scopes:{b['branch_id']}",
                         lambda bid=b["branch_id"]: soldoc.list_scopes(bid))
        branches.append({**b, "scopes": scopes})
    return {**sol, "branches": branches}

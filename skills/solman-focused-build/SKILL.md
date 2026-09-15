---
name: solman-focused-build
description: Create and modify SAP Solution Manager Focused Build objects — requirements (S1BR), work packages (S1IT) and work items (S1MJ/S1CG) — through the solman-fb MCP server without hitting the write traps that silently succeed. Use when creating or editing a requirement, attaching Solution Documentation elements, setting element scope, creating or targeting a work package, creating a work item, ticking work-item scope documents, or moving any of these through their lifecycle. Also use when a Focused Build write "succeeded" but the value did not persist.
---

# Focused Build writes that silently do not persist

The `salm` OData services behind Focused Build accept many writes they do not apply, and
return `200`/`204`. **Never trust a write response.** Every rule below was measured against
a live SolMan 7.2 system, not inferred from documentation.

Read `references/write-traps.md` for the full catalogue before any bulk run. The rules you
must know up front are here.

## The order of operations that works

Getting these out of order is the most common cause of lost work.

1. **Create** the requirement with every field you can set at creation — including
   `planned_project` **and** `planned_project_guid` together.
2. **All text edits** (`update_requirement`: title, description, remarks) — do these now.
3. **Approve** it: `submit_requirement_for_approval` → `approve_requirement`.
4. **Attach elements** (`attach_element`), then verify each one.
5. **Create the work package** with `project` / `release` / `release_component` /
   `release_number` passed at creation.
6. **Create the work item**, then tick its scope documents.

Why the order matters: step 2 after step 4 destroys step 4 (see below), and steps 5 and 6
have no update path at all — a wrong value there means withdraw and recreate.

## Five traps that will cost you a day

**1. `update_requirement` wipes `SolutionId`.** It MERGEs `REQUIREMENTSet`, and the backend
blanks `SolutionId` for any field not in the payload. Attached elements resolve *through*
the solution, so `list_requirement_elements` then returns `[]` and the links look deleted.
They are not — re-run `attach_element` for every element with explicit `solution` and
`scope_id` to restore them. **Do all text edits before attaching anything.**

**2. `attach_element` reports success for the wrong element.** Its verification ends with
`... or len(attached) > 0`, so it returns `"verified": true` whenever the requirement has
*any* element. Always follow with `list_requirement_elements` and confirm the specific
`element_id` and its `scope_id`.

**3. `create_requirement` ignores `planned_project` unless `planned_project_guid` is passed
too.** No error — the requirement is simply created with a blank planned project, outside
the release, invisible to every project-scoped view and KPI report. There is **no OData fix
afterwards**: `PlannedProject` is not updatable and a direct MERGE returns `500`. It has to
be corrected in the Fiori Requirement app, which resets the status. Pass both, then read
`PlannedProject` back.

**4. Work package project and release are create-only.** `BRWPSet` and `WORKPACKAGESet`
MERGE return `501`; `WORKSPACESET` MERGE returns `204` and no-ops. The fields also read
blank on correctly configured work packages, so a blank read proves nothing. The reliable
test that a work package is targeted is `list_scope_components` — a targeted one returns
components, an untargeted one returns `[]`. Call the Python function
`workpackages.create_work_package(...)` directly if the MCP tool schema does not expose
`project` / `release` / `release_component` / `release_number`.

**5. Re-scoping an element link in place does not work.** Re-running `attach_element` with a
different `scope_id` keeps the original scope and returns the usual false positive. To change
scope: `detach_element`, then `attach_element` with the new one. Pass `branch_id` explicitly;
leave `solution` empty when you want `SAP_DEFAULT_SCOPE`, or `resolve_context` may re-map it.

## Element scope — derive it, never guess

An element link's scope decides whether the requirement shows up in the release. Do not ask
the user and do not pick from a list of plausible names. **Read it off a sibling requirement
already attached to the same work package** (`list_requirement_elements` on the sibling shows
its `scope_id`), and use that.

The pattern that falls out: a requirement on a **release** planned project takes the matching
release scope — matched by the same change number as the project. A requirement on an
**urgent-enhancement** project, which has no matching release scope, takes
`SAP_DEFAULT_SCOPE`. Never a monthly `*_Enhancement <month>` scope.

## Work items

**Change type:** default to `wp_type="nc"` (Normal Change, S1MJ). A General Change (S1CG)
**cannot carry a transport**, so "it's only configuration" is not a reason to choose GC —
customizing that needs a transport request still needs a Normal Change. Only use `gc` for
work that moves no transport at all. Do not infer change type from FIT-versus-WRICEF
classification.

**Scope documents.** A work package cannot progress past Scoping until its work item's scope
documents are ticked. There is no tool for it; the only route is a deep create on
`BTSCOPESET` carrying the whole `SCOPE_DOCSet` array — the recipe is in
`references/work-items.md`.

It **adds and ticks only**. Re-posting with `Checked: false` or `Deleted: true` does nothing.
What CAN be removed is the structure behind a scope document: `unassign_structure` prunes
the work package's Solution Documentation assignment (DROP_DOC `RelevantStructureSet` PUT
with the Documentation app's unassign action) **while the package is in Scoping**; past that
the backend answers "Customizing settings prevent assignment changes". So still **never tick
everything offered** — filter the payload to the documents that belong on that work item —
and prune stale structures before moving a package on.

**Technical component.** The backend keeps `config_item` (Productive System) only when
`WpSystem` arrives as `SID:CLIENT` (`P1M:100`) together with `ConfigItem`, `IbaseInstance`
and `CmpDesc`; a bare SID makes it drop all four silently and store `:`. `create_work_item`
now appends the client; `set_work_item_component` repairs an existing item through the same
deep-create. Proven on 27 items.

## Lifecycle gates

These decide what can still be changed, and they close behind you:

- **Requirement → work package assignment only works while the work package is in Scoping**
  (`E0016`). It fails against In Development, To Be Developed and Rejected.
- **A Rejected work package keeps its requirement links.** Rejecting one therefore *strands*
  its requirements rather than freeing them — and since assignment needs Scoping, they can
  only move to a new work package that is still in Scoping.
- **Cancelling an Approved requirement requires Postpone first** — `S1BR_POSTPONE`, after
  which `S1BR_RESTORE` and `S1BR_CANCEL` appear.
- **Unassigning a requirement in Fiori clears *all* its work package links** and resets the
  status. Recovery is re-approve plus reassign every link. The API alternative is
  `unassign_work_package` (a single pair, via the POST-only import
  `wpUnassignmentFromRequirement` — calling it with GET was what produced the earlier 404s);
  `check_unassign_work_package` is its read-only pre-check. Not yet exercised live.

`list_requirement_actions` / `list_workspace_actions` show what is currently possible; use
them rather than assuming an action exists.

## Reading state correctly

- **A requirement's work packages:** read the `REQUIREMENTSet` **collection** filtered by
  `RequirementId` — it returns one row per assigned work package with `WpId`, `WpGuid` and
  `WpDescription`. The single-entity GET never populates them, even for requirements in
  Realization with work packages. Filtering by `RequirementGuid` is silently ignored.
- **A work package header:** filter `WORKSPACESET` by `Guid eq guid'<dashed>'`. An
  `ObjectId eq` filter works for most but not all — `get_workspace` returning `{}` does not
  mean the object is missing.
- Before any long run, check `session_status` and refresh if needed
  (`python refresh_session.py --timeout 240`). A 20-minute enumeration that expires half way
  wastes the whole read.

## Before you start a bulk run

Bulk creation amplifies every trap above. Make the script **resumable** — write each created
ID to a JSON state file and skip what is already done — and **verify each object after
creating it** rather than at the end. Losing the mapping between a 60-row source sheet and
what was actually created is far more expensive than the extra reads.

For the organisation-specific layer — title conventions, which solution and planned project,
required custom fields, and the local rules on what may share a requirement or work package —
see the companion conventions skill for your programme. This skill is deliberately free of
site-specific identifiers.

# Focused Build write traps — the full catalogue

Every entry was measured against a live SolMan 7.2 Focused Build system. "Silent" means the
call returned a success status and changed nothing.

## Requirements (`BUSINESS_REQUIREMENTS_SRV`)

| What | Behaviour | Work-around |
|---|---|---|
| `update_requirement` (MERGE on `REQUIREMENTSet`) | Blanks `SolutionId`, which breaks `REQELEMENTSet` resolution — attached elements vanish from `list_requirement_elements` | Do all text edits before attaching elements. To recover, re-`attach_element` with explicit `solution` + `scope_id` |
| `attach_element` response | `"verified": true` whenever the requirement has **any** element (`... or len(attached) > 0`) | Always re-read with `list_requirement_elements` and match the `element_id` |
| `attach_element` with a new `scope_id` on an existing link | Keeps the original scope, reports success | `detach_element` then `attach_element` |
| `create_requirement(planned_project=...)` without `planned_project_guid` | Planned project comes out **blank**, no error | Always pass both; read `PlannedProject` back |
| MERGE carrying `PlannedProject` / `PlannedProjectGuid` | `500` | Fiori Requirement app only |
| Updatable fields | Only `RequirementTitle`, `Description`, `Remarks`, `SuggestedSolution`, `LongDescription`, `PriorityName`, the external-reference custom field, `Value`, `Effort` | Everything else is create-only or Fiori-only |
| Team name / BP in a non-default solution | Come back blank; not exposed by `update_requirement` | Set in the Fiori Requirement app |
| Work package link before Approved | Silently no-ops | Approve first: `S1BR_SEND_FOR_APPROVAL` → `S1BR_CONFIRMED` |

## Work packages (`BRWPSet`, `WORKPACKAGESet`, `WORKSPACESET`)

| What | Behaviour | Work-around |
|---|---|---|
| `BRWPSet` / `WORKPACKAGESet` MERGE | `501 ..._GET_ENTITY not implemented` | None |
| `WORKSPACESET` MERGE | `204` and silently no-ops | None — set at creation |
| Any `create` answering `201` with an empty entity | A no-op; the reason is in the `sap-message` header (surfaced as `__sap_message`) | Read it before concluding anything was created — e.g. S1CR is "blocked for further business transactions" |
| Reading `RequestedRelease*` / `ActualRelease*` | Blank on **every** work package, including correct ones | Test targeting with `list_scope_components` instead — targeted returns components, untargeted returns `[]` |
| Work package text via `BTTEXTSET` POST or direct MERGE | POST: `201`, nothing persisted. MERGE: `501` no UPDATE_ENTITY | MERGE inside a `$batch` changeset (`write_text_note`); read with `BTTEXTSet?$filter=ConfigId eq 2` |
| `ProjPhaseGuid` | All zeros even on good work packages | Read `WORKSPACESET(...)/BT_ITPPM` and take the GUID from `PpmUrl` |
| `create_work_package` `assigned: true` | **Trustworthy** — `link_verified` really reads `BT_RELATEDTRANSSet` | — |

The Python function accepts `project`, `project_phase`, `release`, `release_component` and
`release_number` even where the MCP tool schema does not expose them. Passing them at
creation produces a fully targeted work package and removes the need to fix each one by hand
in Fiori. This is the work-around for blank `SOLMAN_WP_*` environment settings.

## Work items and scope

| What | Behaviour | Work-around |
|---|---|---|
| `config_item` on `create_work_item` | Dropped whenever `WpSystem` is the bare SID | Send `WpSystem` as `SID:CLIENT` with ConfigItem/IbaseInstance/CmpDesc together (now the default); `set_work_item_component` repairs existing items |
| `BTSCOPESET` deep create with `SCOPE_DOCSet` | Adds and ticks. `Checked: false` / `Deleted: true` do nothing | Remove the structure behind the document with `unassign_structure` while in Scoping — and never tick everything offered |
| `SCOPE_DOCSet` read at In Development or later | Returns nothing (scope frozen) | Not a missing assignment — do not report it as one |

## Reads that lie

| What | Behaviour |
|---|---|
| `$filter` on `PlannedProject` | Ignored; returns everything |
| `$filter` on `RequirementGuid` | Ignored; filter on `RequirementId` |
| `$skip` | Repeats the first page — a 6000-row read yielded 100 distinct rows |
| `$top` above 100 on `WORKSPACESET` | Caps at 100 |
| `ObjectId eq` on `WORKSPACESET` | Works for most objects, returns nothing for some that exist and read fine by `Guid` |
| Single-entity requirement GET | `WpId` / `WpGuid` always blank; `Assignable: "X"` does not mean unassigned |

Consequence: **you cannot enumerate a planned project through OData.** See the
`solman-release-audit` skill for the population-building method.

## Statuses seen in the wild

Requirement: `E0001` Draft, `E0009` In Approval, `E0003` Approved, In Realization, Postponed,
Canceled.
Work package: `E0001` Created, `E0016` Scoping, `E0004` Scope Finalized, Scope Extension,
`E0022` To Be Developed, `E0017` In Development, Rejected.

Use `list_requirement_actions` / `list_workspace_actions` rather than hard-coding action IDs;
the set available changes with status.

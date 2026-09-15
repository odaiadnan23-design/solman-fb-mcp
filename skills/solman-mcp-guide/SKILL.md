---
name: solman-mcp-guide
description: How to work SAP Solution Manager through the solman-fb MCP — which tool answers which question across Focused Build and ChaRM (requirements, work packages, work items, defects, corrections, test cases/plans/packages, requests for change, normal/urgent changes, incidents), how the two transports (OData and RFC-over-SOAP) differ and when each is the only one that works, the safety rails (preflight, read-only, write journal, session self-healing), and the traps that make a wrong answer look right. Load this before any SolMan task, and whenever a SolMan tool returns something surprising.
---

# Working SolMan through the solman-fb MCP

The server speaks to one SAP Solution Manager system through two transports and
exposes ~100 tools. This is the map: what to reach for, what to run first, and the
handful of behaviours that produce a confident wrong answer if you do not know them.

## Start every session here

1. **`preflight`** — TLS mode, cookie age and ACL, session validity, read-only state,
   whether the write targets are configured, whether the RFC transport is up, and a
   `problems` list. Do not write anything while `problems` is non-empty. It catches
   the silent failures: an expired cookie mid-run, an unset planned project that would
   file a requirement outside its release, a broad cookie ACL.
2. **Decide read-only or not.** For an audit, set `SOLMAN_READONLY=1` in the server's
   environment. Every CREATE/MERGE/action import is then refused *before* any HTTP
   call; reads are untouched.
3. Long enumeration ahead? Sessions expire on a clock, not on demand. The client
   re-mints the cookie automatically (headless first, then a briefly visible window)
   and retries once. If that fails you are told to run
   `python refresh_session.py --timeout 240` by hand.

## Two transports, one session

| | OData (`/sap/opu/odata/salm/*`) | RFC over SOAP (`/sap/bc/soap/rfc`) |
|---|---|---|
| Good at | reading one object by GUID, navigations (partners, related documents, work items, transports, test cases, actions), **all writes** | **enumeration**, status **history**, document flow, text-note inventory, table reads, user lookup |
| Cannot | enumerate — `$filter` is ignored on key fields, `$skip` repeats page one, `$top` caps at 100; no service catalog | call most function modules (`S_RFC` is per function group: `READ_TEXT`, `SAVE_TEXT`, `CRM_ORDER_READ` are all "RFC Authority Error"); write anything |
| Guarded by | `SOLMAN_READONLY`, write journal, CSRF handling | a fixed allowlist of nine **reader** function modules; no write path exists here by design |

Both use the same SSO cookie. `rfc_probe` tells you whether the RFC side is up.

## Which tool for which question

### "What exists?" — enumeration and inventory
- **`release_inventory(description_like)`** — counts per object type for a release or
  title fragment. Start an audit here.
- **`find_documents(process_type, description_like, object_id_like)`** — every document
  of a type, complete. `S1IT` work packages, `S1BR` requirements, `S1DM` defects,
  `S1MJ`/`S1CG` work items, `ZMCR` requests for change, `ZMMJ` normal changes,
  `ZMIN` incidents… `list_object_types` gives the 32 Focused Build and ChaRM types
  with their real names.
- Never build a population from requirement assignments alone: that can only find
  work packages that *have* a requirement, which is the wrong blind spot for an
  audit. On one release it hid 11 of 113 packages, including a duplicate.

### "Tell me about this one" — any type
- **`describe_document(ref, process_type?)`** — by id or GUID: header and status,
  partners with names, related documents (requirement↔WP, WP↔work item,
  defect↔correction, RFC↔change), work items, transports, test cases, available
  actions, text notes (id/size/author), **status history** (who moved it, when — OData
  has none) and resolved document flow. Same call for a defect, a normal change, a
  work package. Pass `process_type` when known; set `include_history`/`include_flow`
  false for speed on bulk reads.
- **`document_status_history`**, **`document_actions`**, **`document_texts`** — the
  pieces on their own.
- **`get_workspace` / `list_workspace_actions` / `execute_workspace_action`** — the
  type-agnostic header, lifecycle actions and their execution, for every type
  including ChaRM.

### Requirements
`search_requirements`, `get_requirement`, `list_requirements`, `create_requirement`,
`create_requirements_batch`, `update_requirement`, `submit_requirement_for_approval`,
`approve_requirement`, `reject_requirement`, `withdraw_requirement`,
`list_requirement_elements`, `attach_element`, `detach_element`, `set_element_scope`,
`list_requirement_actions`, `execute_requirement_action`.
- **`requirement_authorized_actions`** — what this user may do (create, assign
  structure, create/assign/unassign work package…).
- **`check_unassign_work_package`** then **`unassign_work_package`** — unassign ONE
  work package from a requirement. Unlike the Fiori unassign, which clears every link
  and resets the status, this targets a pair. The check is read-only; the write is
  journalled. *Not yet exercised live* — treat the first real call as a test.
- **`assign_existing_work_package`** — the import the authorization list names.

### Work packages and work items
`create_work_package`, `assign_work_package`, `withdraw_work_package`,
`list_scope_components`, `create_work_item`, `list_work_items`, `assign_structures`.
A work item is a separate document (S1MJ in `3000…`, S1CG in `4000…`); reach it
through `describe_document` on the package (`related`) or by id.
- **`set_work_item_component`** — fix a blank technical component on an existing work
  item. The backend keeps it only when `WpSystem` is `SID:CLIENT` and ConfigItem,
  IbaseInstance and CmpDesc are sent together; `create_work_item` now does this.
- **`list_structure_assignments` / `wricef_structures_in_scope` / `unassign_structure`**
  — the WP-side Solution Documentation assignment, read and pruned exactly as the
  Documentation app does it. This is the fix for a FIT package still carrying an
  interface after its requirement was re-scoped. **Works while the package is in
  Scoping**; past that the backend answers "Customizing settings prevent assignment
  changes".
- **`read_text_notes` / `write_text_note` / `list_text_note_types`** — text notes with
  content. Reads need the numeric `ConfigId` filter (the WP app uses 2); writes go
  through a `$batch` changeset. This is how the CCB questionnaire gets onto a package
  without Fiori: `write_text_note(guid, "S114" or "S115", text)`.

### Defects, corrections, test management
- Defects (`S1DM`) and corrections (`S1TM`) are fully readable through
  `describe_document` and enumerable through `find_documents`; test cases linked to a
  defect appear under `test_cases`. `sql_defect_*` tools give cached summaries.
- Test cases, steps, parameters, plans, packages, execution status, where-used, xlsx
  round-trip: `list_test_cases`, `get_test_case`, `list_test_case_steps`,
  `set_test_case_steps`, `create_test_case`, `update_test_case`, `delete_test_case`,
  `list_test_parameters`, `list_test_plans`, `list_test_packages`,
  `test_execution_status`, `test_case_where_used`, `download_test_case_xlsx`,
  `upload_test_cases_xlsx`, `test_lookup`.

### ChaRM (ZMCR, ZMMJ, ZMHF, ZMIN, ZMPR, ZMRQ…) and creating documents
Readable and enumerable with the same tools; lifecycle through
`list_workspace_actions`/`execute_workspace_action`; transports under
`describe_document → transports`.
- **`create_request_for_change`** — the generic app's own path (`WS_REQUEST_CHANGESet`).
  On the Gore system it fails loudly with the backend's own words: the Focused Build
  RFC type S1CR is "blocked for further business transactions" in customizing. Gore
  raises ZMCR requests in the CRM WebClient; no OData create path for ZMCR exists.
- **`create_defect`** (+ `list_test_packages_for_defects`, `list_test_cases_in_package`,
  `defect_value_helps`) — the My Test Executions path (`TM_TWL_SRV DefectCreationSet`).
  A defect is always born against a test package and test case; the backend refuses
  otherwise. Payload-complete, not yet exercised on a live test package.

### Tables, people, system
- **`describe_table`** before **`read_table`** — a wrong column name surfaces as a
  confusing `TABLE_WITHOUT_DATA`, not a helpful error.
- **`user_detail`** resolves an SAP user id (`CreatedBy`, text authors, history) to a
  person. **`system_info`** is the cheapest identity check.
- **`rfc_function_search`** / **`rfc_function_interface`** for discovery. Whether you
  may *call* a module is separate: the allowlist holds the proven ones.

### Safety and forensics
- **`recent_writes`** — the local journal of everything this server changed.
- **`harden_connection`** — restrict the cookie file to the current user.
- **`session_status`** — the cheap liveness check; `preflight` is the full one.

## The traps — read before trusting a result

1. **OData `$filter` on `ObjectId` is applied after the first page is cut.** It finds
   recently changed documents and silently misses older ones. `get_workspace(id)`
   returning `{}` does *not* mean the document is missing. `describe_document`
   resolves ids through paths that work; when in doubt pass the GUID.
2. **`$skip` repeats page one.** `results_all` now detects a repeating page and stops,
   flagging `last_page_stalled`. Fewer rows than expected is the honest outcome.
3. **RAW GUIDs and `RFC_READ_TABLE`.** The classic return renders a RAW(16) GUID as 16
   hex characters — a prefix shared across the system. `read_table` avoids this by
   requesting the `ET_DATA` return path, which gives the full 32 digits, so
   `CRMD_ORDERADM_H` resolves any id to its GUID. If you ever see 16-character GUIDs
   again, the kernel has dropped `ET_DATA` and every GUID join is suspect.
4. **`attach_element`'s `verified: true` is a false positive.** Follow with
   `list_requirement_elements`. **`update_requirement` blanks `SolutionId`** and hides
   element links until they are re-attached — do text edits before attaching.
5. **`create_requirement` ignores `planned_project` without `planned_project_guid`** and
   files the requirement outside its release. `preflight` flags the unset target.
6. **Text notes need the `ConfigId` filter to read and a `$batch` changeset to write.**
   `BTTEXTSet` without `ConfigId eq 2` is empty; a direct MERGE answers 501. Both looked
   like "inert" for two days until the Fiori app's own calls were read. Use
   `read_text_notes`/`write_text_note`. The CCB question template is readable live from
   `BT_TEXT_TEMPLATESet`.
7. **Detaching a node from a requirement leaves it in the work package's scope.**
   Structure assignment is separate; check `wricef_structures_in_scope` and prune with
   `unassign_structure` while the package is still in Scoping.
8. **Requirement assignment needs the work package in Scoping.** A Rejected package
   keeps its links and strands its requirements.
9. **A `201 Created` with an empty entity is a no-op with an explanation in the
   `sap-message` header.** `create`/`batch_create` now surface it as `__sap_message`;
   read it before concluding anything was created.
10. **Several `get_*` OData imports execute actions.** `function()` treats everything
    not on the read-only list as a write; POST-only imports go through
    `function_post` — calling them with GET returns a misleading 404.

## Recipes

**Release audit population**
```
release_inventory("GMR6")                 -> counts per type
find_documents("S1IT", "GMR6")            -> every work package (not just those with requirements)
find_documents("S1BR", "GMR6")            -> every requirement
```
then `describe_document` per package with `include_flow=false`; compare the scope
of each work item to its requirements' elements; flag packages whose title names a
WRICEF their requirement does not carry.

**Who changed this and when**
```
document_status_history("2000007715")     -> each status, by whom, date/time
user_detail("MEMILI")                     -> the person behind a user id
```

**Read any table safely**
```
describe_table("CRM_JCDS")                -> pick columns
read_table("CRM_JCDS", ["STAT","USNAM","UDATE"], "OBJNR = '<32-char guid>'")
```

**Before a bulk write**
`preflight` → confirm `problems` is empty and `read_only` is false → do the writes →
`recent_writes` to confirm what landed → re-read with `describe_document`; never trust
a write's own response for anything that has a verified-read alternative.

## Companion skills
`solman-focused-build` (creating and editing objects without the write traps),
`solman-release-audit` (the full audit procedure and script), and the site-specific
conventions skill kept inside your own network.

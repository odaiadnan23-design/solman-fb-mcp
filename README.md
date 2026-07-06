# solman-fb-mcp — SAP SolMan Focused Build Requirements MCP server

An MCP server that lets an AI assistant (Claude, etc.) **create and manage Solution
Manager Focused Build Requirements** — and navigate the Solution Documentation
process hierarchy — directly over the SAP OData API, instead of driving the Fiori
UI through browser automation.

Point it at your own SolMan Focused Build system via `.env`. It is **cookie-only at
runtime** (never opens a browser); an out-of-band refresh script mints the session
cookie via browser SSO.

## Setup

```bash
pip install -r requirements.txt          # mcp + httpx (+ playwright for refresh)
python -m playwright install msedge      # or use an installed Edge/Chromium
cp .env.example .env                      # then fill in your system (see below)
```

Fill `.env` with your host and the site-specific ids. Discover the ids with the
tools once connected: `list_lookup('solutions')`, `list_branches(<solutionId>)`,
`list_lookup('projects'|'categories'|'priorities')`, and the `soldoc_*` tools.

Register the server with your MCP client (stdio), e.g.:

```json
{ "mcpServers": { "solman-fb": {
    "type": "stdio",
    "command": "python",
    "args": ["/path/to/solman-fb-mcp/server.py"]
} } }
```

## Auth model (browser SSO → cookie file → cookie-only server)

Many SolMan systems front HTTPS logon with SAML 2.0 → SAP Cloud Identity (IAS) or
Windows SPNego. Rather than script that, a real browser establishes the session
once and the cookie is reused:

1. **Refresh (out-of-band, occasional):**
   ```powershell
   ./pm1-refresh.ps1          # opens Edge, completes SSO, saves the cookie
   # or: python pm1_refresh.py --timeout 300
   ```
   Writes the SAP session cookie to `%USERPROFILE%\.vsp\cookies-pm1.txt` (never
   committed). First run may need an interactive login; later runs are silent
   (persistent Edge profile).
2. **Server (runtime):** loads the cookie, handles the CSRF token, never opens a
   browser. If the session expires, tools return `SESSION EXPIRED: … run pm1_refresh.py`.

> Note: SolMan's ADT node is usually closed on a Focused Build box, so ADT-based
> tools (e.g. `vsp.exe`) can't be reused for cookie capture — hence the dedicated
> refresh script that lands on an allowed OData path.

## Tools

| Tool | Purpose |
|------|---------|
| `session_status()` | Check the session is live (else run pm1_refresh.py) |
| `list_process_types()` | List Focused Build object types (Requirement, WP, Defect, RfC, Risk, …) |
| `search_requirements(query, top)` | Find requirements by title substring |
| `get_requirement(guid)` | Read a requirement in full |
| `create_requirement(title, priority, classification, …)` | Create a requirement. Title truncated to 40; `external_reference` → the ZZFLD00000B custom field. Optional `element_id` attaches a Solution element. |
| `update_requirement(guid, …)` | MERGE-update editable fields |
| `list_requirement_actions(guid)` | List available lifecycle actions |
| `withdraw_requirement(guid)` | Withdraw/cancel a Draft/To-Be-Approved requirement (PPF action) |
| `submit_requirement_for_approval(guid)` | Draft → To Be Approved |
| `approve_requirement(guid)` | To Be Approved → Approved (required before a WP can be linked) |
| `reject_requirement(guid)` | To Be Approved → Rejected |
| `execute_requirement_action(guid, action_id)` | Run any lifecycle action |
| `search_solution_elements(query, branch_id)` | Flat search of SolDoc elements by name |
| `attach_element(requirement_guid, element_id, …)` | Attach a SolDoc element to a requirement |
| `list_requirement_elements(requirement_guid)` | List the SolDoc elements attached to a requirement |
| `detach_element(requirement_guid, element_id, …)` | Detach a SolDoc element from a requirement |
| `list_lookup(kind)` | Reference values: solutions/priorities/classifications/categories/statuses/projects |
| `list_branches(solution_id)` | Branches for a solution |
| `soldoc_context(branch_id)` | SolDoc root context (solution/branch names) |
| `soldoc_list_scopes(branch_id)` | Scopes/views for a branch (Show All, team/release scopes) |
| `soldoc_browse(parent_element_id, branch_id, scope)` | Navigate the process-structure tree — empty parent = roots, else children. Drill via `element_id` where `has_children`. |
| `soldoc_get_element(element_id, branch_id)` | Resolve one element (type + full path) by id |
| `create_work_package(requirement_guid, title, …)` | Create a Work Package (ProcessType S1IT) and link it to its (Approved) requirement |
| `assign_work_package(requirement_guid, work_package_guid)` | Link an existing WP to an Approved requirement (self-verified) |
| `withdraw_work_package(work_package_guid)` | Withdraw a WP (rejects its scope) |
| `list_work_items(work_package_guid)` | List the Work Items (scope items) under a WP |
| **Generic (any object type)** | |
| `search_workspaces(process_type, query, top)` | Search any type (defect, request_for_change, risk, work_item_nc, …) by title |
| `get_workspace(id_or_guid, process_type)` | Read any object's header by ObjectId or GUID |
| `list_workspace_actions(id_or_guid, process_type)` | List lifecycle actions for any object |
| `execute_workspace_action(id_or_guid, action_id, process_type)` | Run a lifecycle action on any object |

## Coverage (toward full browser parity)

**Covered**
- **Requirements** — full CRUD: create, read, search, update, attach/list/detach SolDoc elements, and the whole lifecycle (submit → approve/reject/withdraw, or any action via `execute_requirement_action`).
- **Work Packages** — create (auto-links to an Approved requirement, self-verified), assign existing, withdraw, list Work Items.
- **SolDoc hierarchy** — navigate the process tree (`soldoc_*`), list scopes, search elements.
- **All object types (read + lifecycle)** — Defect, Request for Change, Risk, Master WP, Work Item, Urgent Change, Defect Correction, plus Requirement/WP — via the generic `search_workspaces` / `get_workspace` / `list_workspace_actions` / `execute_workspace_action` (built on `CRM_GENERIC_SRV`, so one uniform interface across every `ProcessType`).

**Not built yet** (needs more UI capture / per-type flows)
- Creating **Work Items** (BTSCOPE scope-item *fill* step — read is covered by `list_work_items`).
- Editing **WP fields** and WP↔requirement **unassign** (`wpUnassignmentFromRequirement` — structured param, uncaptured).
- Type-specific **create** flows for Defect / RfC / Risk (each is its own form).
- **Attachments/documents** (`DROP_DOC_SRV`) and deeper SolDoc (attributes, assigned docs/test cases, structure editing).
- Richer requirement/query **filters** (by status/project/owner) beyond title substring.

## Connectivity

The HTTP client is **reused across calls** (one cookie load + one CSRF token, connection-pooled) and **auto-reloads** when `pm1_refresh.py` rewrites the cookie file (`client_for()` watches the file mtime). `session_status()` gives a cheap liveness check; an expired SAML session is detected (login-HTML response) and reported as `SESSION EXPIRED` rather than crashing. Refresh is still out-of-band (`pm1_refresh.py`). *Future:* a periodic keepalive ping to reduce session expiries.

## Files

- `config.py` — env-driven configuration (loads `.env`)
- `pm1_refresh.py` / `pm1-refresh.ps1` — Playwright SSO cookie minting
- `client.py` — cookie-only httpx client (CSRF, get/create/merge/function)
- `requirements.py` — requirement domain operations
- `workpackages.py` — Work Package create/assign/withdraw + list work items
- `workspaces.py` — generic operations for every object type (`CRM_GENERIC_SRV`)
- `soldoc.py` — Solution Documentation tree navigation (`soldoc_node_selection_srv`)
- `server.py` — FastMCP stdio server (30 tools)
- `test_mcp.py` — read-only MCP stdio smoke test · `test_units.py` — offline unit tests

## Notes / gotchas (verified against a live SolMan 7.2 Focused Build system)

- Requirement transaction type is configurable (`SOLMAN_REQ_PROCESS_TYPE`, e.g. `S1BR`).
  Primary service = `BUSINESS_REQUIREMENTS_SRV`; `CRM_GENERIC_SRV` is used for the
  requirement search list + some lookups.
- **SolDoc hierarchy** = dedicated `soldoc_node_selection_srv` (real parent/child tree, not the
  capped flat `ELEMENTSet`). Root seed `CrmObjectSet(CrmId='',BranchId=<b>)`; nodes via
  `/elementsTree?$filter=ScopeId eq '<scope>'` (+`and ParentElementId eq '<id>'` for children).
  **`ScopeId` is mandatory** on the tree filter. Node `ElementId` is the id `attach_element` consumes.
- **Create needs Category + Owner** — omitting them returns HTTP 201 with an empty
  entity that silently does not persist.
- **No DELETE / no status field-write.** Lifecycle changes are **PPF actions**
  (`get_ppf_actions?ActionId='…'&WsGuid='…'`), not MERGE of `StatusId`.
- **Attach element** uses the `Assign_Requirement` function import — `POST REQELEMENTSet` is a
  **silent no-op**. Attach the **reference** node (`/Business Processes/…`), not the library original
  (`/Libraries/…`). A step can be referenced into multiple parents — pick the right one.
- `$format=json` is rejected on write requests (use the `Accept` header).
- The WORKSPACE `Guid` (dashed) ↔ `RequirementGuid` (no dashes, upper) — handled internally.
- **Requirement → Work Package chain:** the requirement must be **Approved** before a WP links
  to it (`Assign_Existing_Wp` silently no-ops otherwise). WP create = `POST BRWPSet` (`TypeId="WP"`,
  ProcessType `S1IT`); the create's `REQUIREMENTS` array does NOT persist the link — the
  `Assign_Existing_Wp(WpGuid, RequirementGuid)` function import does. WP targeting (project/phase/
  release) comes from `SOLMAN_WP_*` config. Withdraw a WP via PPF action `S1ITR_REJECT_SCOPE`.
  The link is **self-verified** by reading `WORKSPACESET(<wp>,'S1IT')/BT_RELATEDTRANSSet` (the linked
  requirement appears there as a `WsType='Requirement'` row) — so `assigned` reflects the real state.
- **Lifecycle note:** an **Approved** requirement can no longer be Withdrawn — only **Postponed**
  (`S1BR_POSTPONE`). Withdraw (`S1BR_CANCEL`) is only available from Draft / To Be Approved.
- Work Items are WP **scope items** (`BTSCOPE`): `POST WORKSPACESET(<WP guid>,'S1IT')/BTSCOPESet`
  (not yet exposed as a tool).

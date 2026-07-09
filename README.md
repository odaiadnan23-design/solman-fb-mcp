# solman-fb-mcp — SAP SolMan Focused Build Requirements MCP server

An MCP server that lets an AI assistant (Claude, etc.) operate **SAP Solution
Manager Focused Build** directly over the OData API — no browser automation.
It covers the full **Requirement → Work Package → Work Item** chain (create,
link, lifecycle, all persisting), **attachments** (files + URL links on any
object), **multi-solution** SolDoc navigation with name-based solution/branch/
scope resolution, trustworthy search on a gateway that silently ignores most
filters, and generic read+lifecycle for every Focused Build object type.

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
   ./refresh-session.ps1      # opens Edge, completes SSO, saves the cookie
   # or: python refresh_session.py --timeout 300
   ```
   Writes the SAP session cookie to `%USERPROFILE%\.solman-mcp\cookies.txt` (never
   committed). First run may need an interactive login; later runs are silent
   (persistent Edge profile).
2. **Server (runtime):** loads the cookie, handles the CSRF token, never opens a
   browser. If the session expires, tools return `SESSION EXPIRED: … run refresh_session.py`.

> Note: ADT-based SSO cookie tools can't be reused here because a Focused Build
> system usually has its ADT node closed — hence the dedicated refresh script
> that lands on an allowed OData path.

## Tools (42)

| Tool | Purpose |
|------|---------|
| **Session & discovery** | |
| `session_status()` | Check the session is live (else run refresh_session.py) |
| `list_process_types()` | List Focused Build object types (Requirement, WP, Defect, RfC, Risk, …) |
| `resolve_context(solution, branch, scope)` | Resolve solution/branch/scope **names** ("Release 5") to ids in one call |
| `solution_overview(solution)` | A solution's branches, each with all scopes — one-call orientation |
| `list_lookup(kind)` | Reference values: solutions/priorities/classifications/categories/statuses/projects |
| `list_branches(solution_id)` | Branches for a solution |
| **Requirements** | |
| `search_requirements(query, top)` | Find requirements by title substring |
| `list_requirements(solution, status, owner, project, query, …)` | Filtered listing, newest first, FLP deep links (see query-semantics note) |
| `get_requirement(guid)` | Read a requirement in full (incl. FLP link) |
| `create_requirement(title, …, solution, scope_id, element_id)` | Create — in ANY solution by name; attached element lands in the named scope |
| `create_requirements_batch(items, solution, scope_id, …)` | Many at once; shared context resolved once; continues on row errors |
| `update_requirement(guid, …)` | MERGE-update editable fields |
| `list_requirement_actions` / `execute_requirement_action` | Lifecycle actions (PPF) |
| `withdraw` / `submit_for_approval` / `approve` / `reject_requirement` | Canonical lifecycle steps |
| **SolDoc elements & tree** | |
| `search_solution_elements(query, solution)` | Flat element search in any solution |
| `attach_element(…, scope_id, solution)` | Attach element under a named scope (re-run = re-scope in place) |
| `set_element_scope(requirement_guid, element_id, scope)` | Explicitly re-file an attached element's scope |
| `list_requirement_elements` / `detach_element` | Read / remove element links |
| `soldoc_context` / `soldoc_list_scopes` / `soldoc_browse` / `soldoc_get_element` | Tree navigation; all accept `solution=` names |
| **Attachments (requirements, WPs, WIs)** | |
| `list_attachments(guid)` | Files + URL links on any object |
| `upload_attachment(guid, file_path \| filename+content_b64)` | Attach a file (verified) |
| `attach_url(guid, url, title)` | Attach a URL link (verified) |
| `download_attachment(guid, doc_id, save_to)` | Byte-exact download |
| `delete_attachment(guid, doc_id)` | Remove an attachment (verified) |
| **Work Packages & Work Items** | |
| `create_work_package(requirement_guid, title, …)` | Create WP (S1IT) linked to its Approved requirement (self-verified) |
| `assign_work_package` / `withdraw_work_package` | Link / withdraw |
| `create_work_item(work_package_guid, description, …)` | **Create a Work Item (scope item) that PERSISTS** — auto-scoping + auto component pick |
| `list_scope_components(work_package_guid)` | Valid technical components for WIs under a WP |
| `list_work_items(work_package_guid)` | Scope items under a WP |
| **Generic (any object type)** | |
| `search_workspaces` / `get_workspace` / `list_workspace_actions` / `execute_workspace_action` | Read + lifecycle for every ProcessType (Defect, RfC, Risk, …) |

## Coverage (toward full browser parity)

**Covered**
- **Requirements** — full CRUD in **any solution by name**, batch create, trustworthy filtered listing with FLP deep links, attach/re-scope/detach SolDoc elements, whole lifecycle.
- **The full chain Requirement → Work Package → Work Item, entirely headless** — create+approve a requirement, create+link the WP, move it into Scoping, and create persisting Work Items with valid technical components.
- **Attachments** — upload files, attach URL links, list, download, delete — on requirements, WPs and WIs.
- **SolDoc hierarchy** — tree navigation, scope resolution by name, element search — across all solutions.
- **All object types (read + lifecycle)** — Defect, RfC, Risk, Master WP, Urgent Change, Defect Correction … via the generic layer (`CRM_GENERIC_SRV`).

**Not built yet**
- Type-specific **create** flows for Defect / RfC / Risk (defects are normally born from test executions, RfCs from ChaRM — separate capture projects; read + lifecycle already work via the generic layer).
- Editing **WP fields** and WP↔requirement **unassign** (`wpUnassignmentFromRequirement` — structured param, uncaptured).
- Deeper SolDoc authoring (attributes, assigned docs/test cases, structure editing).

## Query semantics on this gateway (READ FIRST — verified live)

The SALM gateway **silently ignores** most `$filter` fields instead of erroring:

- `REQUIREMENTSet`: only `BranchId`, `RequirementId` (exact) and `RequirementTitle`
  (exact) are honored. Status/priority/owner/project predicates are dropped — and
  when a dropped predicate is present, even `$top` is ignored.
- `or`-chains keep only the **last** predicate. `$orderby` returns 500.
  `$count`/`$inlinecount` return wrong numbers. On a BranchId-filtered set,
  `$skip` **lies** — every page returns the same window.
- The one reliable enumerator is `CRM_GENERIC_SRV/WORKSPACESET` with `ProcessType`
  + `substringof(…, Description)` — honored, and rows come back **newest first**.
  `list_requirements` is built on it; everything else is filtered client-side.

## The Work Item commit recipe (hard-won)

A plain `POST BTSCOPESET` returns 201 and a `WpItemGuid` — and the backend
**silently discards the row**. Three conditions make it persist:

1. the WP is in **Scoping** or later (`S1ITR_HANDOVER_TO_SCOPING` on a fresh WP);
2. `ConfigItem` is **valid for this WP/type** — the component value help itself
   returns empty unless filtered by `ProcessType` **and** `SystemSwitch`;
3. the fill is a **deep create** to top-level `BTSCOPESET` including an (empty)
   `BTSCOPE_PARTNERSSet` array — the deep-create signature triggers the CRM
   one-order save.

`create_work_item` automates all three and self-verifies via `list_work_items`.

## Attachments: RPC-over-OData

`DROP_DOC_SRV` requires an `Action` discriminator on every call, of the form
`<consumer app id> + <operation suffix>` (e.g. `…attachments_Document_Create`,
`…_Document_Create_Url`, delete via HTTP `DELETE …?Action=…_Document_Delete`).
List = `CharmWP_WI_BRSet(CrmId,BranchId)/attachedDeltaDocuments`; download =
`DocumentContentCollection(…)/$value`. Works for any CRM object (Req/WP/WI).

## Connectivity

The HTTP client is **reused across calls** (one cookie load + one CSRF token, connection-pooled) and **auto-reloads** when `refresh_session.py` rewrites the cookie file (`client_for()` watches the file mtime). `session_status()` gives a cheap liveness check; an expired SAML session is detected (login-HTML response) and reported as `SESSION EXPIRED` rather than crashing. Refresh is still out-of-band (`refresh_session.py`). *Future:* a periodic keepalive ping to reduce session expiries.

## Files

- `config.py` — env-driven configuration (loads `.env`)
- `refresh_session.py` / `refresh-session.ps1` — Playwright SSO cookie minting
- `client.py` — cookie-only httpx client (CSRF, transient-fault retries, safe paging)
- `solutions.py` — solution/branch/scope resolution by NAME, cached
- `requirements.py` — requirement domain ops (create/batch/list/lifecycle/elements)
- `attachments.py` — files + URL links on any object (`DROP_DOC_SRV`)
- `workpackages.py` — WP create/assign/withdraw + Work Item create/list/components
- `workspaces.py` — generic operations for every object type (`CRM_GENERIC_SRV`)
- `soldoc.py` — Solution Documentation tree navigation (`soldoc_node_selection_srv`)
- `server.py` — FastMCP stdio server (42 tools)
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
- Work Items are WP **scope items** (`BTSCOPE`). Creating one that PERSISTS needs the
  three-condition recipe (see "The Work Item commit recipe" above) — `create_work_item`
  automates it.

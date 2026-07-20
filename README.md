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

## Quickstart — install with one prompt in Claude Code

New to it? Open **Claude Code** (desktop or CLI) in any folder and paste the prompt
below. Claude does the whole install: clones the repo, sets up Python, helps you sign
in once, registers the server, and verifies the connection. **The only thing you need
to supply is your SolMan host** (you'll be asked for it).

```text
Set up the solman-fb-mcp server for me. Work through these steps and stop to ask me only where noted:

1. Clone https://github.com/odaiadnan23-design/solman-fb-mcp.git into my home directory (skip the clone if it's already there) and cd into it.
2. Make sure Python 3.11+ is available. Create a virtual environment (.venv) in the repo and install requirements.txt into it. Then run `python -m playwright install msedge` (skip if Microsoft Edge is already installed and usable).
3. Copy .env.example to .env. Ask me for my SolMan Focused Build host (for example solman.mycompany.com) and SAP client (default 100), and write them into .env. Leave the other create-defaults blank for now — they're only needed later for creating requirements/work packages and can be discovered with the tools.
4. Mint my login session: run `python refresh_session.py --timeout 300` (on Windows you can use `./refresh-session.ps1`). This opens a browser for single sign-on — I will complete the login. Wait until it writes the cookie file, then continue.
5. Register the server with Claude Code at user scope:
   `claude mcp add solman-fb -s user -e SAP_CLIENT=100 -- <the .venv python path> <the repo>/server.py`
   Use the venv's python and the absolute path to server.py. If the `claude` CLI isn't available, add an equivalent stdio entry named "solman-fb" to my MCP config instead.
6. Tell me to restart Claude Code (or reconnect MCP) so the new tools load.
7. Once the tools are available, verify the connection by calling, in order: session_status, then list_process_types, then list_lookup with kind "solutions". Show me the results. If session_status reports the session expired, tell me to re-run step 4.

Finish with a short summary of what worked and anything that still needs my input.
```

> Only `SOLMAN_HOST` is required to connect and read. The create-defaults in `.env`
> (solution/owner/category, WP release targeting) are optional and only used when you
> *create* requirements or work packages — discover them later with `list_lookup`,
> `solution_overview`, and the `soldoc_*` tools.

## Verify the connection

In Claude Code, after the server is registered and you've refreshed the session:

- `session_status()` → confirms the cookie is live (otherwise run the refresh).
- `list_process_types()` → the Focused Build object types on your system.
- `list_lookup('solutions')` → your solutions; then `solution_overview('<name>')` for its branches + scopes.

If any tool returns `SESSION EXPIRED: … run refresh_session.py`, re-run the refresh and retry — that's the one manual step, and it's usually silent after the first sign-in.

## Tips & use cases

Talk to it in plain language — Claude picks the right tools. Examples:

**Explore**
- "What solutions, branches and scopes exist? Give me an overview of `<solution>`."
- "List the last 20 requirements in `<solution>` with their status and owner."
- "Find requirements about 'credit control' and show their FLP links."

**Author the full Requirement → Work Package → Work Item chain**
- "Create a requirement titled '`<title>`' in `<solution>`, classification WRICEF, and attach it to process step `<S…>`."
- "Approve requirement `<id>`, create a Work Package under `<project>`, and add a Work Item to it."
- "Attach this file / this Jira URL to requirement `<id>`."

**Test Suite**
- "Search test cases for 'invoice' and show which test plans use them."
- "Download the test-case template, then upload this filled xlsx into `<solution>` (validate first)."
- "Show the test execution status for `<solution>`."

**Good habits**
- **Use names, not GUIDs** — say "Release 5" or "`<solution>`"; the server resolves them (`resolve_context`).
- **A requirement must be Approved before a Work Package can link** to it.
- **Attach requirements to the Business-Process *reference* step** (under *Business Processes → …*), **not** the library original (under *Libraries → Process Step Library*) — the reference node is where traceability rolls up.
- **xlsx uploads validate by default.** Review the validation result, then re-run with `validate_only=false` to commit.
- Seeing `SESSION EXPIRED`? Run the refresh script — cookie refresh is the only out-of-band step.

## Requirements & SAP's External Requirement API

This server manages requirements through the Requirements Fiori app's own OData service
(`BUSINESS_REQUIREMENTS_SRV`) — it works over your interactive SSO with no backend setup.
SAP also ships a separate, documented **External Requirement API**
(`/SALM/EXT_R2D_INTEG_SRV`) built for wiring an external backlog (Jira, etc.) into Focused
Build with external-ID mapping and two-way status sync — but it needs Basis to activate it
and a service user.

**[REQUIREMENTS.md](REQUIREMENTS.md)** covers both: the official field catalog
(WRICEF / text / partner / status codes), how each MCP tool maps to the official operations,
the shared rule that a requirement's branch comes from its Solution-Documentation element
(not a create field), and when to use which path.

## Manual setup

Prefer to do it by hand (or not using Claude Code to install)?

```bash
git clone https://github.com/odaiadnan23-design/solman-fb-mcp.git
cd solman-fb-mcp
pip install -r requirements.txt          # mcp + httpx (+ playwright for refresh)
python -m playwright install msedge      # or use an installed Edge/Chromium
cp .env.example .env                      # then fill in your host (see below)
python refresh_session.py --timeout 300   # sign in once; mints the session cookie
```

Fill `.env` with your host (and, when you're ready to create objects, the site-specific
ids). Discover the ids with the tools once connected: `list_lookup('solutions')`,
`list_branches(<solutionId>)`, `list_lookup('projects'|'categories'|'priorities')`, and
the `soldoc_*` tools.

Register the server with your MCP client (stdio), e.g.:

```json
{ "mcpServers": { "solman-fb": {
    "type": "stdio",
    "command": "python",
    "args": ["/path/to/solman-fb-mcp/server.py"],
    "env": { "SAP_CLIENT": "100", "SAP_LANGUAGE": "EN" }
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

## Tools (60)

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
| **Test Suite — test cases, steps, xlsx, plans/packages** | |
| `list_test_cases(query, folder)` / `get_test_case` | Find/read test cases (server-side name search + folder) |
| `list_test_case_steps(case_id)` | Ordered steps (description / expected result / instruction) |
| `create_test_case(name, folder, solution)` | Create a test case (folder id from `test_lookup('folders')`) |
| `update_test_case(case_id, …)` | MERGE header (description/prerequisites/exit-criteria/priority/…) |
| `set_test_case_steps(case_id, steps, append)` | Write steps (deep-save; replace or append) |
| `delete_test_case(case_id)` | Delete (cleanup) |
| `test_case_where_used(case_id)` | Test plans that contain the case (with links) |
| `download_test_case_template(save_to)` | The blank upload template (xlsx) |
| `download_test_case_xlsx(case_id, save_to)` | A case's header+steps as the upload-format xlsx |
| `upload_test_cases_xlsx(file_path, validate_only)` | Upload a filled xlsx — auto-maps template headers; **validate-only by default** |
| `list_test_plans(solution, query)` / `list_test_packages(plan_guid)` | Browse plans & their packages |
| `test_execution_status(solution)` | Test-status progress per plan |
| `list_test_parameters(case_id)` | Test-data parameters (variants) |
| `test_lookup(kind)` | folders / status_schemas / statuses / priorities / solutions |
| **Generic (any object type)** | |
| `search_workspaces` / `get_workspace` / `list_workspace_actions` / `execute_workspace_action` | Read + lifecycle for every ProcessType (Defect, RfC, Risk, …) |

## Coverage (toward full browser parity)

**Covered**
- **Requirements** — full CRUD in **any solution by name**, batch create, trustworthy filtered listing with FLP deep links, attach/re-scope/detach SolDoc elements, whole lifecycle.
- **The full chain Requirement → Work Package → Work Item, entirely headless** — create+approve a requirement, create+link the WP, move it into Scoping, and create persisting Work Items with valid technical components.
- **Attachments** — upload files, attach URL links, list, download, delete — on requirements, WPs and WIs.
- **SolDoc hierarchy** — tree navigation, scope resolution by name, element search, and structure assignment on WPs/WIs — across all solutions.
- **Test Suite** — test cases (CRUD + steps via deep-save), the **xlsx template flow** (download sample / download a case / upload a filled sheet with auto column-mapping, validate-only by default), test plans & packages (read/browse), where-used, execution status, test-data parameters.
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

## Test Suite: deep-save + the xlsx flow

- **Test cases** are keyed `(CaseId GUID, CaseVersion, Language)`. Create =
  `POST TestCaseSet`; header edits = MERGE `TestCaseHeaderSet`.
- **Steps DON'T save individually.** A direct `POST TestCaseStepSet` 201s but is
  silently discarded — steps commit only via a **deep POST to `TestCaseSet`**
  carrying `TestCasetoHeaderNav:[header]` with the whole step list nested in
  `header.TestCaseHeadertoStepsNav` (same shape the designer UI submits).
  `set_test_case_steps` builds that payload.
- **xlsx flow** (`TM_TS_DESIGNER_SRV`): download the blank template
  (`TestCaseDownloadSampleSet('')/$value`) or a case's steps
  (`TestCaseDownloadSet`), and upload in two phases — (1) POST the raw file to
  `TestCaseUploadSet` → the server parses it and returns an `UploadCacheId` plus
  per-column header texts; (2) MERGE the cache entry with the column map
  (`Col<letter> = "<ATTR.KEY>:<index>"`, e.g. `CASE.NAME`, `STXT.EXPECTED_RESULT`)
  and `ValidateOnly`. Standard template headers auto-map. The SolDoc-path column
  is only accepted when a branch is supplied ("Upload into SolDoc").
- **Test plans/packages** are read via `TM_DASH_SRV` (`TestPlanSet` needs a
  `SolutionId`; `TestPackageSet` filters by the plan's `TplnGuid`). Where-used per
  case is the `TestCaseHeadertoWuTplnNav` header nav (the flat set returns nothing
  standalone). Plan/package *creation* is the classic STWB_2 transaction — no OData.

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
- `soldoc.py` — Solution Documentation tree navigation + structure assignment (`soldoc_node_selection_srv`)
- `testsuite.py` — Test Suite: test cases/steps, xlsx upload/download, plans/packages (`TM_TS_DESIGNER_SRV`, `TM_TS_PARAM_SRV`, `TM_DASH_SRV`)
- `server.py` — FastMCP stdio server (60 tools)
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

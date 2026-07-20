# Managing Requirements in Focused Build

There are **two** OData ways to create and manage Focused Build requirements. This MCP
uses one; SAP documents the other. This page explains both, the official field catalog,
and when to use which — so requirement management is deliberate, not guesswork.

## Two paths in

| | **This MCP** (`BUSINESS_REQUIREMENTS_SRV`) | **SAP External Requirement API** (`/SALM/EXT_R2D_INTEG_SRV`) |
|---|---|---|
| What it is | The Requirements **Fiori app's own** OData service | A **purpose-built, documented** integration API for wiring an external tool (Jira, etc.) into Focused Build |
| Auth | Your **interactive SSO** cookie — you act as yourself | An **ICF service/technical user** + delivered role `SAP_OST_FB_SYNC_REQ` |
| Backend setup | **None** — works today | Activate `SALM_FB` service + system aliases; define the external system + field mapping in the IMG; (optional) enhancement spot `/SALM/EXT_R2D_INTEG` |
| Keyed by | FB GUID / ObjectId | **(external system, external ID)** — the Jira key lives on the FB requirement |
| Dedup | none (a re-run creates a new requirement) | re-sending the same `extId` **updates** rather than duplicates |
| Status | PPF lifecycle actions | `ChangeStatus` endpoint **+ outbound webhook back to the external tool** |
| Scope | full **Requirement → Work Package → Work Item**, attachments, Test Suite | requirements only (create/read/update/status/attachments/elements) |
| Best for | interactive & agent-driven authoring, ad-hoc loads, the whole chain | **productionized, ongoing two-way Jira ↔ FB requirement sync** |

**For a one-time / interactive load (e.g. the 30-row Excel template): use this MCP.** It
needs no backend enablement, runs as you, and covers the whole chain. The External
Requirement API is the right tool once you want a *standing* Jira→FB integration with
external-ID dedup and status round-trip — but that requires Basis to enable it first.

## The rule both APIs share (the one we learned the hard way)

A requirement's **solution/branch association is not a field on the create call.** It comes
from **adding a Solution Documentation element**:

- This MCP: `attach_element(...)` → the `Assign_Requirement` function.
- External API: `POST .../RequirementSet(...)/toReqSolutionElement` with `{ occId, solutionId }`.

And per SAP's own doc, that element **can only be added while the requirement is still in
the initial status (Draft `E0001`)** — *"not possible in any later status."* So the correct
order is always **create → attach element (sets branch/solution) → then advance status**.
A requirement created without an element is unanchored (blank branch).

## External Requirement API — operations (official)

Service base: `/sap/opu/odata/SALM/EXT_R2D_INTEG_SRV` (ST-OST add-on; SP08+ for the modern
set. Legacy create up to SP07 was `EXT_INTEG_CREATE_SRV/IsCreateSet`.)

| Operation | HTTP | Endpoint |
|---|---|---|
| Read (with `$expand`) | GET | `RequirementSet(guid=…,extSysGuid=…,extId=…)` |
| **Create** (deep) | POST | `RequirementSet` |
| Change header | PUT/PATCH | `RequirementSet(…)` |
| Post a text | POST | `RequirementSet(…)/toReqText` |
| WRICEF flags add/remove | POST/DELETE | `…/toReqWricef` |
| Attachment (file) | POST/DELETE | `…/toReqAttachment` |
| URL attachment (SP9) | POST | `…/toReqUrl` |
| Change business partner | POST | `…/toReqPartner` |
| **Add solution element** (SP9) | POST | `…/toReqSolutionElement` → `{ occId, solutionId }` |
| Customer fields (SP10) | PUT/PATCH | `CustomerHSet(…)` |
| **Change status** | POST | `ChangeStatus?guid=…&extSysGuid=…&extId=…&userStatus=E0009` |

`$expand` values on read: `toReqPartner, toReqText, toReqWricef, toReqCategory,
toReqAttachment, toReqUrl, toReqSolutionElement, toReqCustomerH`.

**Create payload (deep):**
```json
{
  "extSysGuid": "EXT_SYSTEM_01", "extId": "MYBACKLOG-1",
  "title": "…", "priority": "2", "classification": "1",
  "plannedProject": "MY_BUILD_PROJECT", "valuePt": 100, "effortPt": 50,
  "toReqWricef":   [{ "wricefId": "E" }, { "wricefId": "I" }],
  "toReqPartner":  [{ "partnerId": "0000001955", "type": "/SALM/01" }],
  "toReqCategory": [{ "catId": "CAT1_CAT01_02_01" }],
  "toReqText":     [{ "textType": "S115", "isRichText": true, "textContent": "<p>…</p>" }]
}
```

## Field catalog / codes (useful whichever path you take)

- **classification** — `1` = WRICEF (also Fit / Gap / Non-Functional in your config). This MCP takes the friendly names `fit | gap | wricef | non-functional`.
- **WRICEF flags** (`toReqWricef.wricefId`) — single letters, one entry per applicable type: **W** Workflow, **R** Report, **I** Interface, **C** Conversion, **E** Enhancement, **F** Form (confirm the exact set your system returns).
- **Text types** (`toReqText.textType`) — `S115` Description, `S126` Assumptions/Comments, `S004` Solution Description, `S105` Comment. `isRichText: true` for HTML content.
- **Partner functions** — requirement **Owner** = `HT000012`, **Business Process Expert** = `/SALM/01` (the legacy create resolves partners by SolMan username → first/last name → email).
- **Priority** — `1` High, `2` Medium, `3` Low.
- **User status** — `E0001` Draft → `E0009` To Be Approved → `E0003` Approved. (Withdraw `S1BR_CANCEL` only from Draft/To-Be-Approved; an Approved requirement can only be Postponed.)
- **Category** — multilevel categorization; pass the **leaf** `catId` (e.g. `CAT1_CAT01_02_01`).
- **Solution element** — `occId` (occurrence id of the element) + `solutionId`; attach the **Business-Process reference** node, not the library original.
- **Customer fields** — active fields in customizing table `/SALM/C_CUSTFLD`; dates as `/Date(<epoch-ms>)/`.
- **Process type** — requirements are `S1BR`.

## Outbound sync (External API only)

On a status change (CRM Change-Request-Management action `S1_UPD_EXT`), SolMan calls **back**
to the external tool via an RFC destination of type **G** (SM59, HTTPS). The external ID is
injected into the callback URL by masking it with `#` (e.g.
`/Issues('#EXTERNAL_ID#')` → `/Issues('BRF-123')`). This is what keeps Jira status in step
with Focused Build automatically. Configured under *IMG → SAP Solution Manager → Focused
Build → Integration → Integrate External Requirements*.

## How this MCP maps to the official operations

| Official operation | This MCP tool |
|---|---|
| Create requirement | `create_requirement` / `create_requirements_batch` |
| Read (expanded) | `get_requirement`, `list_requirement_elements`, `list_attachments` |
| Change header | `update_requirement` |
| Add solution element | `attach_element` / `set_element_scope` / `detach_element` |
| WRICEF classification | `create_requirement(classification=…)` |
| Attachment / URL attachment | `upload_attachment` / `attach_url` / `download_attachment` / `delete_attachment` |
| Change status | `submit_for_approval` / `approve` / `reject_requirement` / `execute_requirement_action` |

## Recommendation

- **Now / interactive / bulk from a spreadsheet:** stay on this MCP. It's the fastest path,
  needs no backend work, and does what the External API can't (Work Packages, Work Items,
  Test Suite).
- **When you want a standing Jira ↔ Focused Build sync:** ask Basis to enable the External
  Requirement API (activate `SALM_FB`, create the service user + role `SAP_OST_FB_SYNC_REQ`,
  define the external system + field mapping in the IMG). Once it's live, a small MCP module
  keyed on the Jira issue key can create/update/close requirements from the backlog with
  built-in dedup and status round-trip. Ping me and I'll build it.

> Source: *External Requirement API in Focused Build for SAP Solution Manager* (SAP, V2.2,
> July 2022).

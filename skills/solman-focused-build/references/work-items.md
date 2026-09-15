# Work items: creation, change type, and ticking scope documents

## Creating the work item

```python
import workpackages as wp

comps = wp.list_scope_components(wp_guid, "nc", True)   # [] means the WP is not targeted
item = wp.create_work_item(
    wp_guid, "<description>",
    text="<scope item text>",
    wricef="wricef",          # or "non-functional" / "fit" — see the classification rules
    wp_type="nc",             # nc = Normal Change (S1MJ), gc = General Change (S1CG)
    priority="2",
    auto_scope=True,
)
```

`list_scope_components` returning `[]` means the work package has no project/release and
therefore no system landscape — `create_work_item` will fail with "No valid scope components
for this WP/type". Fix the work package first; it cannot be fixed after creation through
OData.

## Change type: default to Normal Change

A **General Change (S1CG) cannot carry a transport.** Configuration that needs a customizing
transport request still requires a **Normal Change (S1MJ)**. Only choose `gc` for work that
moves no transport at all.

Do not infer the change type from whether the requirement is FIT or WRICEF — a FIT
configuration item usually still produces a customizing transport, and a WRICEF always
produces development objects. Ask what will actually be transported.

## Ticking the scope documents

A work package will not leave Scoping until its work item's scope documents are ticked, and
there is no tool for it. The only working route is a **deep create on `BTSCOPESET`** carrying
the entire `SCOPE_DOCSet` array:

```python
from client import client_for
import config, workpackages as wp

c = client_for(config.SVC_GENERIC)
key = f"BTSCOPESET(WpGuid='{wg}',WpItemGuid='{ig}')"
docs = c.results(f"{key}/SCOPE_DOCSet")
item = next(x for x in wp.list_work_items(wg) if x["item_guid"] == ig)

# Filter `docs` to the documents that belong on THIS work item before building the payload.
payload = [{
    "WpGuid": wg, "ItemGuid": ig, "Checked": True, "Selectable": True,
    "ContextOcc": d["ContextOcc"], "Leaf": d["Leaf"], "Path": d["Path"],
    "OccTxt": d["OccTxt"], "ObjectType": d["ObjectType"],
    "ObjectDesc": d["ObjectDesc"], "SbraTxt": d["SbraTxt"], "Client": d["Client"],
    "Context": "", "Objid": "", "Title": "", "StatusText": "", "DocuTypeText": "",
    "AuthorFullname": "", "RfcGuid": "00000000-0000-0000-0000-000000000000",
    "ViewTxt": "", "SlanDesc": "", "Guid": "", "Url": d["Url"], "Parent": "",
    "Deleted": False,
} for d in docs]

c.create("BTSCOPESET", {
    "WpGuid": wg, "WpItemGuid": ig, "WpType": item["type"],
    "WpDescription": item["description"], "Wricef": "W", "WricefKey": "W",
    "PriorityId": item.get("priority_id") or "2", "Changeable": True,
    "ValuePoints": 0, "StoryPoints": 0, "ConfigItem": item.get("config_item") or "",
    "IbaseInstance": "", "CmpDesc": "", "WpSystem": "", "ProcTypeDesc": "", "Sprint": "",
    "WpScope": "", "WpStatus": "", "Url": "", "Text": item.get("text") or "",
    "BTSCOPE_PARTNERSSet": [], "SCOPE_DOCSet": payload,
})

after = c.results(f"{key}/SCOPE_DOCSet")            # verify
print(sum(1 for d in after if d.get("Checked") and d.get("ItemGuid") == ig), "/", len(after))
```

### It only adds

Re-posting with `Checked: False` or `Deleted: True` has no effect. Unticking and removing a
document are **Fiori-only**. So:

- **Never pass every offered document.** The offer list is derived from the structures
  assigned to the work package, which can include things the requirement no longer owns.
  Ticking the lot silently pulls those onto the work item's scope, permanently as far as the
  API is concerned.
- Decide the intended document set first — usually the elements attached to the work
  package's own requirement — and pass only those.

### The related gotcha

A work package's **structure assignment is independent of its requirement's elements.**
Detaching an element from a requirement does **not** remove it from the work package's scope
document offer. After any requirement re-scoping, re-read `SCOPE_DOCSet` for every affected
work package and compare it against the requirement's elements — the two drift apart silently,
and that drift is invisible in the Fiori requirement view.

### Reading it back

`SCOPE_DOCSet` returns nothing once the work package reaches In Development or later — scope
is frozen at that point. Treat an empty read at those statuses as unreadable, not empty.

## Moving the work package to Scoping

```python
import workspaces as ws
ws.execute_action(wp_guid, "S1ITR_HANDOVER_TO_SCOPING", "S1IT")   # "Define Scope"
```

This executes but leaves the work package in `E0001 Created` if the work package has no
project/release. Check the status afterwards; do not trust the action response.

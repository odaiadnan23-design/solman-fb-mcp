# Why you cannot enumerate, and what to do instead

## The reads that lie

All measured against a live SolMan 7.2 Focused Build system. Every one returns HTTP 200, so a
wrong answer is indistinguishable from a right one.

| Read | Actual behaviour |
|---|---|
| `REQUIREMENTSet?$filter=PlannedProject eq '...'` | Filter **ignored**. Returns everything. |
| `REQUIREMENTSet?$filter=RequirementGuid eq '...'` | Filter **ignored**. Use `RequirementId`. |
| `$skip` on any large collection | **Repeats the first page.** A 6000-row paged read produced 100 distinct rows. |
| `$top` above 100 on `WORKSPACESET` | Silently capped at 100. |
| `WORKSPACESET?$filter=ObjectId eq '...'` | Works for most objects; returns nothing for some that exist and read fine by `Guid`. |
| Single-entity requirement GET | `WpId` / `WpGuid` **always blank**, even for requirements in Realization with work packages. `Assignable: "X"` does not mean unassigned. |

Two real consequences from the GMR-release audits:

- An audit built on `$filter` + `$skip` returned 100 rows, **none of them on the target
  project**, and looked plausible.
- An audit built on a work-package title sweep found 79 of about 94 packages and was blind to
  every requirement with no package — the exact population an audit exists to find.

## The method that works

**1. Get the ID list from outside OData.** A Fiori `REQUIREMENTSet` extract from the user is
the best source: it is the same list the KPI report sees, so an audit built on it answers the
question the user is actually asking. Failing that, an ID range you created yourself.

**2. Read each requirement individually, from the collection with an `$filter` on
`RequirementId`** — not the single entity:

```python
rows = c.results("REQUIREMENTSet", {"$filter": f"RequirementId eq '{rid}'"})
```

This returns **one row per assigned work package**, each with `WpId`, `WpGuid` and
`WpDescription` populated. It is simultaneously the requirement read and the only reliable
requirement→work-package lookup. Filter the rows by `PlannedProject` **in Python**, since the
server will not do it.

**3. Take work-package GUIDs from those rows** and read each header by GUID:

```python
dashed = f"{g[:8]}-{g[8:12]}-{g[12:16]}-{g[16:20]}-{g[20:32]}"
rows = c.results("WORKSPACESET", {"$filter": f"ProcessType eq 'S1IT' and Guid eq guid'{dashed}'",
                                  "$top": "1"})
```

Never by `ObjectId`, and never assume `get_workspace` returning `{}` means the object does not
exist.

**4. Supplement with a title sweep, and label it as a supplement.** It is the only way to
reach a work package with no requirement, and it is incomplete. Say so in the report.

## Cost and mechanics

Around 2 requests per requirement and 2 + *n* per work package (one per work item for scope
documents). For ~150 requirements and ~100 work packages that is roughly 20 minutes.

- **Check `session_status` first**, and refresh with
  `python refresh_session.py --timeout 240` if needed. An expiry half way through wastes the
  whole read.
- **Run it in the background** and poll the log; do not block a conversation on it.
- **Write the raw result to JSON** and do the analysis in a second pass. Findings definitions
  change as you learn what matters, and re-running the read to try a different rule is
  20 minutes you do not need to spend.
- Beware shell redirects when generating scripts: `python - <<PY > audit.py` sends the
  *generator's* stdout over the file you are writing. Write the file, then compile it, then
  run it.

---
name: solman-release-audit
description: Audit a SAP Solution Manager Focused Build release — every requirement, work package and work item on a planned project — against composition, assignment and completeness rules, and report the findings. Use when asked for a release health check, a requirement or work package audit, "what is missing on this release", a KPI-report gap analysis, or to verify a bulk creation or clean-up landed correctly. Also use before answering any question of the form "what is the status of all our requirements".
---

# Auditing a Focused Build release

Two things make this harder than it looks, and both have produced confidently wrong reports:

1. **You cannot enumerate a planned project.** The gateway ignores `$filter` on
   `PlannedProject`, and `$skip` repeats the first page. A "6000-row" read returns about 100
   distinct rows. Any audit built on a collection read is fiction.
2. **Requirement-level cleanliness does not imply work-package-level cleanliness.** A work
   package's assigned structures are a *separate* assignment from its requirement's elements,
   so a rule can pass on every requirement and still fail on nine work packages.

Read `references/enumeration.md` before building a population and `references/checks.md` for
what each check means and why it exists.

## Run it

```bash
python scripts/audit_release.py \
    --projects MYPROJ_1.0_CHG0001234,MYPROJ_1.0_BUILD \
    --ids extract.csv \
    --id-range 1000012340-1000012400 \
    --title-sweep "MYREL" \
    --expected-scope "<scope_id every element should be on>" \
    --out audit.json
```

It is read-only and safe to re-run. It writes `audit.json` (full detail, for rendering a
report) and prints a findings summary.

**Check `session_status` first and refresh if needed** — a 20-minute enumeration that expires
half way through wastes the whole read. **Run it in the background**: roughly 4 seconds per
requirement and 3 per work package, so ~150 requirements and ~100 work packages takes about
20 minutes.

## Building the population

The script needs requirement IDs because nothing else is reliable. In order of preference:

1. **A Fiori `REQUIREMENTSet` extract from the user.** Best source — it is the same list the
   KPI report sees. Save as CSV and pass with `--ids`; every 8+ digit run in the file is
   treated as an ID, so no cleaning is needed.
2. **ID ranges you created yourself** (`--id-range`), for a recent bulk run.
3. **A work-package title sweep** (`--title-sweep`) as a *supplement only*. It caps at 100
   rows and is blind to requirements that have no work package. A sweep-only audit missed
   roughly a tenth of the work packages and every unassigned requirement.

From the requirement rows the script follows `WpId` / `WpGuid` to the work packages, which is
the one reliable requirement→work-package link. Work packages with no requirement at all are
only reachable via the title sweep — say so in the report rather than implying full coverage.

## Reporting the findings

The output is usually for a release or change manager, so it has to survive scrutiny:

- **Separate defects from accepted exceptions.** An intentional Pre Load / Post Load pair, or
  a requirement that genuinely cannot be split until Solution Documentation catches up, is not
  a finding. The script detects a real phase pair; verify it *is* a pair from the work package
  descriptions — a "Post Load" title with no Pre half is just a name.
- **Give ID lists the reader can paste**, comma-separated, not prose.
- **Name what you could not verify.** Scope documents are unreadable once a work package
  reaches In Development; that is a read limitation, not an empty scope, and must not be
  reported as a gap.
- **Date the read.** Statuses move between runs. If you are re-reporting after a fix, re-read
  first — do not claim a fix landed on the strength of the write response
  (see the `solman-focused-build` skill).
- **Publish it as a page, not a wall of terminal text**, if it is going to anyone else. A
  filterable work-package register with status, requirement IDs, NC/GC work item counts and
  the check result per row is what people actually use.

## Interpreting the four findings that recur

| Finding | Why it matters | Fix |
|---|---|---|
| Requirement whose only work packages are **Rejected** | Rejection keeps the link, so the requirement is stranded with no delivery vehicle. Invisible to a check that only asks "does it have a work package?" | Confirm the rejection was deliberate, then move the requirements to a package **in Scoping** — the only status that accepts an assignment |
| **WRICEF in a work package's work-item scope** that its requirement does not own | Left behind by a requirement re-scope; breaks the one-WRICEF-per-package rule one level below where anyone looks | Remove the structure in the Fiori Work Package app. There is no API route — re-posting the scope unticked does nothing |
| Work item with **no technical component** | The package cannot progress cleanly | Set once in Fiori, then copy what the app writes; the value is silently dropped on API creation |
| Single WRICEF on a **General Change only** | A GC cannot carry a transport, and a WRICEF always produces one | Add a Normal Change (S1MJ) work item |

## Scope of this skill

Deliberately free of site-specific identifiers — no hostnames, solution or project GUIDs,
release names or process codes. The organisation-specific layer (which planned projects are
in play, the expected scope ID, local composition rules, who the findings go to) belongs in a
companion conventions skill kept inside your own network.

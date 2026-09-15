# The checks, and why each one exists

Twelve checks, at two levels. A release can pass every requirement check and still fail on
work packages — run both.

## Requirement level

**1. Has at least one Solution Documentation element.**
A requirement with no node is not traceable to anything and will not appear in scope. Note
that a blank element list can also be a *symptom* rather than a fact: `update_requirement`
blanks `SolutionId`, which makes the element list read empty while the assignments still
exist. Re-attach with an explicit solution before concluding the nodes are gone.

**2. Does not mix FIT and WRICEF nodes, and carries at most one WRICEF.**
The common composition rule: a requirement is either for configuration/fit content **or**
for one single development object. Mixing them, or bundling several WRICEFs, breaks
traceability and the release KPI reporting, because a WRICEF's scope ends up spread across or
buried inside fit objects.

**3. Classification matches content.**
Classified "Fit" while holding a WRICEF node, or "WRICEF" with no WRICEF node. Usually the
residue of a split that was only half done.

**4. Planned project is set.**
`create_requirement` silently produces a blank planned project if `planned_project_guid` is
omitted. The requirement then exists, looks fine in its own app, and is **invisible to every
project-scoped view and to the KPI report**. This is only detectable by comparing counts, so
check it explicitly.

**5. Element scope is the expected one.**
The scope on an element link decides whether the node counts as in-release. Pass
`--expected-scope` with the release scope ID and anything else is flagged. Derive that ID
from a requirement known to be correct, not from a list of plausible names.

**6. Status is Approved or later.**
A Draft or In Approval requirement is not deliverable, and a work package link silently
no-ops before Approved. Drafts also tend to be the residue of a Fiori edit that reset the
status without anyone noticing.

**7. Assigned to exactly one work package.**
A genuine Pre Load / Post Load **pair** is intentional in a data-migration release and is not
a finding — the script detects one from the two descriptions. Anything beyond that means the
same scope is being delivered twice, or a split left the parent attached everywhere.

**8. Its work packages are not all Rejected.**
A Rejected work package **keeps** its requirement links, so rejecting one strands its
requirements rather than freeing them. A check that only asks "does this requirement have a
work package?" reports these as clean. Since assignment requires status Scoping, recovery
needs a new package created in Scoping.

## Work package level

**9. Has a requirement, and has at least one work item.**
A package with no requirement has nothing justifying it; one with no work item cannot deliver.
Both are more often accidents of a Fiori unassign than deliberate.

**10. Its requirements collectively are FITs or one single WRICEF.**
The same composition rule as check 2, applied to everything assigned to the package. Two
single-WRICEF requirements on one package each pass check 2 and fail this one.

**11. Its work-item scope documents carry no WRICEF the requirement does not own.**
**The check most likely to be missed.** A work package's structure assignment is independent
of its requirement's elements: detaching a node from the requirement does not remove it from
the package's scope. So the clean-up that fixes checks 2 and 10 leaves this one broken, and
it is invisible from the requirement side. Compare the WRICEF IDs ticked as scope documents
against the WRICEF IDs on the package's own requirements.

**12. Work items are complete and of the right type.**
Three sub-checks:
- **Technical component set.** Silently dropped on API creation; the package cannot progress
  cleanly without it.
- **All offered scope documents ticked.** A package will not leave Scoping otherwise.
- **A Normal Change present wherever a transport will be produced.** A General Change (S1CG)
  cannot carry a transport, so any package producing development objects — every WRICEF, and
  most configuration — needs an S1MJ.

## What is *not* a finding

- **An empty scope-document read at In Development or later.** Scope is frozen at those
  statuses and the service returns nothing. Report it as unreadable, never as empty.
- **A blank release or project field on a work package.** These read blank on correctly
  configured packages too. Test targeting with `list_scope_components` instead.
- **A requirement spread across several packages when the split is genuinely impossible** —
  e.g. a form or enhancement with no named process step in Solution Documentation yet. Record
  it as an accepted exception with the reason, so it stops being re-reported every audit.

## Detecting a WRICEF node

Name matches `^(IDD|EDD|FDD|RDD|CDD|WDD)\s*\d{3,5}` **or** element type is an interface
reference (`REF_IFACE` / `IFACE`). Adjust the prefix set to your organisation's naming.

Note the asymmetry this creates: interfaces are reliably detected because they are their own
structure nodes, but forms, enhancements and reports are only detected when the process step
is *named* with the object ID. An object with no named step cannot be split onto its own
requirement at all — which is the usual legitimate reason a requirement spans several
packages.

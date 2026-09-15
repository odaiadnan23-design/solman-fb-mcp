"""Work-package structure assignment — the Solution Documentation nodes a work
package (or work item / requirement) is assigned to, read and unassigned through
DROP_DOC_SRV exactly as the Focused Build "Documentation" app does it.

WHY THIS EXISTS. A requirement's elements and a work package's structure
assignment are separate things. Detaching a WRICEF node from a requirement left
it assigned to the work package, where it still surfaced as a scope document on
the work item — a FIT package carrying an interface, one level below where anyone
looks. Re-posting the scope with the document unticked did nothing (the deep-create
only adds). This module is the removal path.

HOW THE APP DOES IT (from /sap/bc/ui5_ui5/salm/drop_doc + drop_core sources)
    read     CharmWP_WI_BRSet(CrmId='<32-char guid>',BranchId='<branch>')/allStructureAssignments
             (RelevantStructureSet itself refuses a direct read: "missing branch ID")
    unassign PUT RelevantStructureSet(StructureId,CrmId,BranchId) with the full row and
             Action = "<app id>_RelevantStructure_Update_Unassign"
             app id = com.sap.solman.fb.dropdoc; model defaultUpdateMethod PUT, no batch
    assign   POST function import DiagramAssignStructures(CrmId, BranchId, StructureId,
             StructureType)
Proven 16-Sep-2026 on work package 2000007715: nine structures -> eight, IDD0816 gone,
and the interface disappeared from the work item's scope documents.

Status matters: a Rejected package answers 500 on these reads — fix structures while
the package is still live.
"""
from __future__ import annotations

import config
from client import SolmanError, client_for

APP_ID = "com.sap.solman.fb.dropdoc"
ACTION_UNASSIGN = APP_ID + "_RelevantStructure_Update_Unassign"
ACTION_ASSIGN = APP_ID + "_RelevantStructure_Update_Assign"


def _g(guid: str) -> str:
    return guid.replace("-", "").upper()


def _branch(branch_id: str) -> str:
    return branch_id or config.DEFAULT_BRANCH_ID or config.require("SOLMAN_BRANCH_ID")


def list_assignments(crm_guid: str, branch_id: str = "") -> list[dict]:
    """Every Solution Documentation structure assigned to a document (work package,
    work item, requirement), with the ids needed to unassign it."""
    rows = client_for(config.SVC_DROP_DOC).results(
        f"CharmWP_WI_BRSet(CrmId='{_g(crm_guid)}',BranchId='{_branch(branch_id)}')/allStructureAssignments")
    return [{"structure_id": r.get("StructureId"), "name": r.get("ObjectName"),
             "element_type": r.get("ElementType"), "structure_type": r.get("StructureType"),
             "scope": r.get("ScopeName"), "deleteable": bool(r.get("IsDeleteable")),
             "marked_for_deletion": bool(r.get("IsMarkedForDeletion")),
             "process": r.get("BusinessProcess"), "scenario": r.get("BusinessScenario"),
             "_row": {k: v for k, v in r.items() if k != "__metadata" and not isinstance(v, dict)}}
            for r in rows]


def unassign(crm_guid: str, structure_id: str, branch_id: str = "") -> dict:
    """Remove ONE structure from a document's assignment. Journalled; kill-switchable.

    Verifies by re-reading the assignments: the result says whether the structure
    is gone, and how many remain, rather than trusting the 204.
    """
    br = _branch(branch_id)
    crm = _g(crm_guid)
    before = list_assignments(crm, br)
    target = next((a for a in before if a["structure_id"] == structure_id), None)
    if not target:
        raise SolmanError(f"structure {structure_id} is not assigned to {crm} on branch {br}; "
                          f"assigned: {[a['structure_id'] for a in before]}")
    body = dict(target["_row"])
    body["Action"] = ACTION_UNASSIGN
    key = f"RelevantStructureSet(StructureId='{structure_id}',CrmId='{crm}',BranchId='{br}')"
    client_for(config.SVC_DROP_DOC).put(key, body)
    after = list_assignments(crm, br)
    return {"unassigned": all(a["structure_id"] != structure_id for a in after),
            "structure": target["name"], "structures_before": len(before),
            "structures_after": len(after)}


def unassign_by_name(crm_guid: str, name_prefix: str, branch_id: str = "") -> dict:
    """Unassign the structure whose name starts with name_prefix (e.g. 'IDD0816')."""
    hits = [a for a in list_assignments(crm_guid, branch_id)
            if (a["name"] or "").upper().startswith(name_prefix.upper())]
    if len(hits) != 1:
        raise SolmanError(f"{len(hits)} structures match {name_prefix!r}: {[h['name'] for h in hits]}")
    return unassign(crm_guid, hits[0]["structure_id"], branch_id)


def assign(crm_guid: str, structure_id: str, structure_type: str = "", branch_id: str = "") -> dict:
    """Assign a structure through the DiagramAssignStructures POST import (untested live)."""
    br = _branch(branch_id)
    crm = _g(crm_guid)
    resp = client_for(config.SVC_DROP_DOC).function_post(
        "DiagramAssignStructures", {"CrmId": crm, "BranchId": br, "StructureId": structure_id,
                                    "StructureType": structure_type})
    after = list_assignments(crm, br)
    return {"assigned": any(a["structure_id"] == structure_id for a in after),
            "structures_after": len(after), "response": resp.get("d", resp)}


def wricef_in_scope(crm_guid: str, branch_id: str = "") -> list[dict]:
    """The WRICEF structures (IDD/EDD/FDD/RDD/CDD/WDD…) assigned to a document — the
    check that catches a FIT package still carrying an interface."""
    import re
    pat = re.compile(r"^\s*(IDD|EDD|FDD|RDD|CDD|WDD)\s*\d{3,5}", re.I)
    return [a for a in list_assignments(crm_guid, branch_id)
            if pat.match(a["name"] or "") or (a.get("element_type") or "").upper() in ("REF_IFACE", "IFACE")]

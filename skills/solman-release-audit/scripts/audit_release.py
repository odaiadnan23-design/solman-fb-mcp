# -*- coding: utf-8 -*-
"""Audit every requirement and work package on one or more Focused Build planned projects.

The gateway cannot enumerate a planned project (see references/enumeration.md), so the
population has to be supplied as a list of requirement IDs. Each is read individually, its
work packages are followed, and every work item and scope document is read.

Usage
-----
    python audit_release.py --projects MYPROJ_1.0_BUILD,MYPROJ_1.0_CHG0001234 \
                            --ids ids.txt --out audit.json

    # extra candidates, e.g. a block you created yourself
    ... --id-range 1000012340-1000012400

--ids accepts a text/CSV file; every 8-or-more-digit run in it is treated as a requirement
ID, so a raw Fiori extract saved as CSV works without cleaning.

Writes <out> (default audit.json) and prints a findings summary. Safe to re-run: read-only.
Expect roughly 4 seconds per requirement and 3 per work package — run it in the background.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys
import time

# Import the MCP server's own modules. Override with SOLMAN_MCP_DIR if this script is not
# living inside the repo.
_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
sys.path.insert(0, os.environ.get("SOLMAN_MCP_DIR", _DEFAULT_ROOT))

from client import SolmanClient, client_for, odata_literal  # noqa: E402
import config  # noqa: E402
import requirements as rq  # noqa: E402
import workpackages as wp  # noqa: E402

WRICEF = re.compile(r"^\s*((?:IDD|EDD|FDD|RDD|CDD|WDD)\s*\d{3,5})", re.I)
WRICEF_ANY = re.compile(r"((?:IDD|EDD|FDD|RDD|CDD|WDD)\s*\d{3,5})", re.I)
IFACE_TYPES = {"REF_IFACE", "IFACE"}
# Statuses at which SCOPE_DOCSet stops returning anything — scope is frozen, not empty.
SCOPE_FROZEN = {"In Development", "To Be Developed", "Confirmed", "Completed"}
PHASE = re.compile(r"(pre|post)\s*load", re.I)


def wricef_id(name: str) -> str | None:
    m = WRICEF.match(name or "")
    return m.group(1).upper().replace(" ", "") if m else None


def load_ids(args) -> list[str]:
    ids: set[str] = set()
    for path in args.ids or []:
        with open(path, encoding="utf-8", errors="replace") as fh:
            ids.update(re.findall(r"\b\d{8,}\b", fh.read()))
    for spec in args.id_range or []:
        lo, _, hi = spec.partition("-")
        ids.update(str(i) for i in range(int(lo), int(hi) + 1))
    return sorted(ids)


def read_requirements(ids, projects, verbose=True):
    """Read each requirement from the REQUIREMENTSet collection (never the single entity)."""
    out, wp_guids = {}, {}
    t0 = time.time()
    with SolmanClient(service=config.SVC_BIZ_REQ) as c:
        for n, rid in enumerate(ids, 1):
            rows = c.results("REQUIREMENTSet", {"$filter": f"RequirementId eq '{odata_literal(rid)}'"})
            if not rows:
                continue
            r0 = rows[0]
            if projects and (r0.get("PlannedProject") or "") not in projects:
                continue
            guid = r0["RequirementGuid"]
            try:
                els = rq.list_elements(guid)
            except Exception:
                els = []
            elements = []
            for e in els:
                name = e.get("name") or ""
                etype = (e.get("type") or "").upper()
                elements.append({
                    "name": name, "type": e.get("type"),
                    "wricef": etype in IFACE_TYPES or bool(WRICEF.match(name)),
                    "wid": wricef_id(name), "scope": e.get("scope_id"),
                })
            packages = []
            for row in rows:
                if row.get("WpId"):
                    packages.append(row["WpId"])
                    wp_guids[row["WpId"]] = (row.get("WpGuid") or "").replace("-", "").upper()
            out[rid] = {
                "id": rid, "guid": guid, "title": r0.get("RequirementTitle"),
                "status": r0.get("Status"), "status_id": r0.get("StatusId"),
                "creator": r0.get("CreatedBy"), "owner": r0.get("OwnerName"),
                "classification": (r0.get("ClassifAttributes") or {}).get("Value"),
                "project": r0.get("PlannedProject"),
                "wps": sorted(set(packages)), "elements": elements,
                "n_elements": len(elements),
                "n_wricef": sum(1 for e in elements if e["wricef"]),
                "n_fit": sum(1 for e in elements if not e["wricef"]),
                "wids": sorted({e["wid"] for e in elements if e["wid"]}),
                "scopes": sorted({e["scope"] for e in elements if e.get("scope")}),
            }
            if verbose and n % 40 == 0:
                print(f"  requirement {n}/{len(ids)}  kept {len(out)}  ({time.time()-t0:.0f}s)", flush=True)
    return out, wp_guids


def sweep_titles(queries, wp_guids):
    """Supplement the population by work-package title. NOT a complete enumeration: the
    gateway caps $top at 100 and ignores $skip."""
    c = client_for(config.SVC_GENERIC)
    for q in queries or []:
        try:
            rows = c.results("WORKSPACESET", {
                "$filter": f"ProcessType eq 'S1IT' and substringof('{odata_literal(q)}',Description)",
                "$top": "100"})
        except Exception as ex:
            print(f"  title sweep '{q}' failed: {ex}", flush=True)
            continue
        for r in rows:
            wp_guids.setdefault(r.get("ObjectId"), (r.get("Guid") or "").replace("-", "").upper())


def read_work_packages(wp_guids, verbose=True):
    c = client_for(config.SVC_GENERIC)
    out = {}
    t0 = time.time()
    for n, (wid, guid) in enumerate(sorted(wp_guids.items()), 1):
        rec = {"id": wid, "guid": guid}
        header = {}
        try:
            if guid:
                dashed = f"{guid[:8]}-{guid[8:12]}-{guid[12:16]}-{guid[16:20]}-{guid[20:32]}"
                rows = c.results("WORKSPACESET", {
                    "$filter": f"ProcessType eq 'S1IT' and Guid eq guid'{dashed}'", "$top": "1"})
            else:
                rows = c.results("WORKSPACESET", {
                    "$filter": f"ProcessType eq 'S1IT' and ObjectId eq '{odata_literal(wid)}'",
                    "$top": "1"})
            if rows:
                header = rows[0]
                rec["guid"] = guid = (header.get("Guid") or "").replace("-", "").upper()
        except Exception as ex:
            rec["header_error"] = str(ex)
        rec.update({
            "description": header.get("Description") or "",
            "status_id": header.get("Status"),
            "status": header.get("Concatstatuser"),
            "priority": header.get("PriorityTxt"),
            "exists": bool(header),
        })
        items = []
        if guid:
            try:
                for it in wp.list_work_items(guid):
                    ig = it["item_guid"]
                    docs = []
                    try:
                        raw = c.results(f"BTSCOPESET(WpGuid='{guid}',WpItemGuid='{ig}')/SCOPE_DOCSet")
                        docs = [{"checked": bool(d.get("Checked")), "type": d.get("ObjectType"),
                                 "name": d.get("OccTxt") or ""}
                                for d in raw if d.get("ItemGuid") == ig]
                    except Exception:
                        pass
                    items.append({
                        "guid": ig, "type": it["type"], "description": it["description"],
                        "status": it["status"], "wricef": it["wricef"],
                        "config_item": it["config_item"], "docs": docs,
                        "n_docs": len(docs), "n_checked": sum(1 for d in docs if d["checked"]),
                    })
            except Exception as ex:
                rec["item_error"] = str(ex)
        rec["items"] = items
        rec["n_nc"] = sum(1 for i in items if i["type"] == "S1MJ")
        rec["n_gc"] = sum(1 for i in items if i["type"] == "S1CG")
        rec["no_component"] = [i["description"] for i in items if not (i["config_item"] or "").strip()]
        scope_wids, ifaces = set(), 0
        for i in items:
            for d in i["docs"]:
                if not d["checked"]:
                    continue
                m = WRICEF_ANY.search(d["name"])
                if m:
                    scope_wids.add(m.group(1).upper().replace(" ", ""))
                if (d["type"] or "").upper() in IFACE_TYPES:
                    ifaces += 1
        rec["scope_wids"] = sorted(scope_wids)
        rec["scope_ifaces"] = ifaces
        rec["scope_readable"] = rec["status"] not in SCOPE_FROZEN
        out[wid] = rec
        if verbose and n % 20 == 0:
            print(f"  work package {n}/{len(wp_guids)}  ({time.time()-t0:.0f}s)", flush=True)
    return out


def evaluate(REQ, WP, expected_scope=None):
    """Apply the checks. See references/checks.md for what each one means."""
    active = {k: r for k, r in REQ.items() if r["status"] != "Canceled"}

    def desc(wid):
        return (WP.get(wid) or {}).get("description") or ""

    def is_phase_pair(r):
        ds = [desc(x) for x in r["wps"]]
        return (len(ds) == 2 and any(PHASE.search(d) and "pre" in d.lower() for d in ds)
                and any(PHASE.search(d) and "post" in d.lower() for d in ds))

    for r in REQ.values():
        r["phase_pair"] = is_phase_pair(r)
        f = []
        if r["status"] != "Canceled":
            if not r["n_elements"]:
                f.append("no Solution Documentation element")
            if r["n_wricef"] and r["n_fit"]:
                f.append(f"mixes FIT and WRICEF ({r['n_fit']} FIT + {r['n_wricef']} WRICEF)")
            if len(r["wids"]) > 1:
                f.append(f"{len(r['wids'])} WRICEFs: {', '.join(r['wids'])}")
            cls = (r["classification"] or "").lower()
            if cls == "fit" and r["n_wricef"]:
                f.append("classified Fit but carries a WRICEF node")
            if cls == "wricef" and not r["n_wricef"]:
                f.append("classified WRICEF but carries no WRICEF node")
            if r["status"] in ("Draft", "In Approval"):
                f.append(f"status {r['status']} — not deliverable")
            if expected_scope and [s for s in r["scopes"] if s != expected_scope]:
                f.append("element(s) on an unexpected scope: "
                         + ", ".join(s for s in r["scopes"] if s != expected_scope))
            if not r["wps"]:
                f.append("no work package")
            else:
                statuses = {WP[x]["status"] for x in r["wps"] if x in WP}
                if statuses and statuses <= {"Rejected"}:
                    f.append("only work package(s) are Rejected — stranded: "
                             + ", ".join(r["wps"]))
                elif len(r["wps"]) > 1 and not r["phase_pair"]:
                    f.append(f"on {len(r['wps'])} work packages: {', '.join(r['wps'])}")
        r["findings"] = f

    for wid, w in WP.items():
        rs = [r for r in active.values() if wid in r["wps"]]
        w["reqs"] = sorted(r["id"] for r in rs)
        w["n_fit"] = sum(r["n_fit"] for r in rs)
        w["n_wricef"] = sum(r["n_wricef"] for r in rs)
        w["wids"] = sorted({i for r in rs for i in r["wids"]})
        f = []
        if w["status"] != "Rejected":
            if not w["reqs"]:
                f.append("no requirement assigned")
            if not w["items"]:
                f.append("no work item")
            if w["n_fit"] and w["n_wricef"]:
                f.append(f"requirements mix FIT and WRICEF ({w['n_fit']}/{w['n_wricef']})")
            if len(w["wids"]) > 1:
                f.append(f"{len(w['wids'])} WRICEFs: {', '.join(w['wids'])}")
            extra = [x for x in w["scope_wids"] if x not in w["wids"]]
            if len(w["scope_wids"]) > 1:
                f.append(f"{len(w['scope_wids'])} WRICEFs in work-item scope: "
                         + ", ".join(w["scope_wids"]))
            elif extra and not w["wids"]:
                f.append("FIT work package, but " + ", ".join(extra) + " sits in work-item scope")
            elif extra:
                f.append("work-item scope carries " + ", ".join(extra) + " beyond its requirement")
            if w["no_component"]:
                f.append(f"{len(w['no_component'])} work item(s) with no technical component")
            unticked = [i for i in w["items"] if i["n_docs"] and i["n_checked"] < i["n_docs"]]
            if unticked:
                f.append(f"{len(unticked)} work item(s) with unticked scope documents")
            if w["wids"] and w["n_gc"] and not w["n_nc"]:
                f.append("WRICEF on a General Change only — a GC carries no transport")
        w["findings"] = f
    return active


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--projects", default="",
                    help="comma-separated planned project ids to keep (blank = keep all)")
    ap.add_argument("--ids", action="append", help="file of requirement ids (repeatable)")
    ap.add_argument("--id-range", action="append", help="inclusive range, e.g. 1000012340-1000012400")
    ap.add_argument("--title-sweep", action="append",
                    help="work-package title substring to sweep in as well (repeatable)")
    ap.add_argument("--expected-scope", default="",
                    help="scope_id every element should be on; flags any other")
    ap.add_argument("--out", default="audit.json")
    args = ap.parse_args()

    projects = tuple(p.strip() for p in args.projects.split(",") if p.strip())
    ids = load_ids(args)
    if not ids:
        ap.error("no requirement ids — pass --ids and/or --id-range")
    print(f"candidate requirement ids: {len(ids)}", flush=True)

    t0 = time.time()
    REQ, wp_guids = read_requirements(ids, projects)
    print(f"requirements kept {len(REQ)}; work packages from their rows {len(wp_guids)}", flush=True)
    sweep_titles(args.title_sweep, wp_guids)
    print(f"work package population {len(wp_guids)}", flush=True)
    WP = read_work_packages(wp_guids)
    active = evaluate(REQ, WP, args.expected_scope or None)

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump({"generated": time.strftime("%Y-%m-%d %H:%M"), "projects": projects,
                   "requirements": REQ, "work_packages": WP}, fh, indent=1, ensure_ascii=False)

    bad_r = sorted((r for r in active.values() if r["findings"]), key=lambda x: x["id"])
    bad_w = sorted((w for w in WP.values() if w["findings"]), key=lambda x: x["id"])
    print(f"\nrequirements {len(REQ)} ({len(active)} active, {len(REQ)-len(active)} canceled)"
          f"  clean {len(active)-len(bad_r)}  with findings {len(bad_r)}")
    print("  status:", dict(collections.Counter(r["status"] for r in REQ.values())))
    print(f"work packages {len(WP)}  clean {len(WP)-len(bad_w)}  with findings {len(bad_w)}")
    print("  status:", dict(collections.Counter(w["status"] or "?" for w in WP.values())))
    print(f"work items: {sum(w['n_nc'] for w in WP.values())} NC (S1MJ), "
          f"{sum(w['n_gc'] for w in WP.values())} GC (S1CG); "
          f"{sum(len(w['no_component']) for w in WP.values())} with no technical component")

    if bad_r:
        print("\n--- requirement findings ---")
        for r in bad_r:
            print(f"  {r['id']} [{str(r['status'])[:14]:14s}] {str(r['title'])[:34]:36s}"
                  f" {str(r['creator'])[:12]:13s} {'; '.join(r['findings'])}")
    if bad_w:
        print("\n--- work package findings ---")
        for w in bad_w:
            print(f"  {w['id']} [{str(w['status'])[:16]:16s}] {w['description'][:36]:38s}"
                  f" nc={w['n_nc']} gc={w['n_gc']} reqs={len(w['reqs'])}"
                  f" :: {'; '.join(w['findings'])}")
    print(f"\nelapsed {time.time()-t0:.0f}s -> {args.out}")


if __name__ == "__main__":
    main()

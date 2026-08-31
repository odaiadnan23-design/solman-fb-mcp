"""Customizing Scout -- compare configuration table contents across systems.

Solution Manager ships transaction ``SCOUT`` (Customizing Scout, SV-SMG-IMP): it
compares customizing between systems that are connected to SolMan by RFC and set
up for Customizing Distribution. This module does the same job over a different
road. It reads table contents from each system through ``vsp query``, which uses
the standard ADT data-preview API, so there is no RFC destination to maintain, no
Customizing Distribution configuration, and no SolMan-side authorization in play.
Anything the caller can already read in SE16 is what they get here.

WHY NOT THE SOLMAN ODATA APIS
-----------------------------
SCOUT is a classic ABAP/Web Dynpro tool. It publishes no OData service, so the
Focused Build SALM services this server otherwise speaks cannot reach it. Going
directly at the managed systems is not a workaround -- it is the shorter path,
and it works on systems SolMan has never been told about.

READ-ONLY BY CONSTRUCTION
-------------------------
The only external command this module builds is ``vsp query`` (enforced in _run
before exec). ``vsp query`` has no write mode. There is no code path here that
can create, change or delete anything in any system.

FAIL CLOSED ON AN ALLOWLIST
---------------------------
``SCOUT_SYSTEMS`` names the systems Scout may touch. If it is unset, every call
refuses. This is deliberate, and it is stronger than blocking known production
SIDs: a production system cannot be reached by forgetting to add it to a
denylist, only by explicitly typing it into the allowlist. It also keeps
site-specific system names out of this file, which is public.

THE BROWSER CAVEAT -- read this before trusting a timeout
---------------------------------------------------------
When a cached SSO session has gone stale, vsp tries a silent refresh; if that
needs a human it opens a browser window and waits (default five minutes). In
server mode ``--sso-on-expiry error`` suppresses that, but **that flag is not
accepted by CLI subcommands**, and the ``on_expiry`` key in ``.vsp.json`` is
ignored -- both verified against v2.54.0 on 2026-08-31. So this module cannot
prevent a window from opening. What it can do, and does, is refuse to wait:
SCOUT_TIMEOUT (default 120s) kills the child, and vsp's browser marker on stderr
becomes an error naming the fix. Mint sessions out of band with
``vsp sso refresh -s <sid>`` and this never comes up.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

# Imported for its side effect: config loads .env into os.environ. The module
# constants below are read at import time, so without this the settings in .env
# would apply or not depending purely on which module Python imported first.
import config  # noqa: F401

VSP_BIN = os.environ.get(
    "SCOUT_VSP_BIN", str(Path.home() / ".local" / "bin" / "vsp.exe")
)
VSP_JSON = Path(os.environ.get("SCOUT_VSP_JSON", str(Path.home() / ".vsp.json")))
TIMEOUT = int(os.environ.get("SCOUT_TIMEOUT", "120"))
MAX_ROWS = int(os.environ.get("SCOUT_MAX_ROWS", "5000"))
ALLOW_PERSONAL = os.environ.get("SCOUT_ALLOW_PERSONAL_DATA", "") in ("1", "true", "yes")

# Person-level data, refused by default. Scout is for configuration; these are
# not configuration, and a diff would copy personal data somewhere nobody agreed
# to put it. SCOUT_ALLOW_PERSONAL_DATA=1 lifts this for a caller who has decided
# they need it -- it is a default, not a lock.
_PERSONAL_PREFIXES = (
    "PA0", "PB0", "PCL", "HRP",           # HR master / infotypes / planned data
    "ADRC", "ADR6", "ADRP",               # central address + email
    "USR21", "USADDRESS",                 # user -> person link
    "KNA1", "LFA1", "BUT000", "BUT020",   # customer / vendor / business partner
)

# Never run these, whatever a caller passes.
_BANNED_SUBCOMMANDS = (
    "deploy", "execute", "install", "copy", "transport", "debug", "adt",
    "recover-failed-create", "rename-preview", "lua", "workflow", "compile",
)


class ScoutError(RuntimeError):
    """Scout could not do what was asked. The message is for a human."""


# --------------------------------------------------------------------------
# systems
# --------------------------------------------------------------------------
def _allowlist() -> list[str]:
    raw = os.environ.get("SCOUT_SYSTEMS", "").strip()
    if not raw:
        raise ScoutError(
            "SCOUT_SYSTEMS is not set, so Scout refuses to read any system. Set it "
            "in .env to the systems Scout may read, comma separated, e.g. "
            "SCOUT_SYSTEMS=dev,qa -- names must match entries in .vsp.json. "
            "Never list a production system."
        )
    return [s.strip().lower() for s in raw.split(",") if s.strip()]


def _configured() -> dict:
    if not VSP_JSON.exists():
        raise ScoutError(f"no vsp system config at {VSP_JSON}")
    try:
        return json.loads(VSP_JSON.read_text(encoding="utf-8")).get("systems", {})
    except Exception as exc:
        raise ScoutError(f"{VSP_JSON} is not readable JSON: {exc}") from exc


def _check_system(name: str) -> str:
    sid = (name or "").strip().lower()
    if not sid:
        raise ScoutError("no system given")
    allowed = _allowlist()
    if sid not in allowed:
        raise ScoutError(
            f"system {sid!r} is not in SCOUT_SYSTEMS ({', '.join(allowed)}). Scout "
            "only reads systems named there. If this is a production system, do "
            "not add it."
        )
    if sid not in {k.lower() for k in _configured()}:
        raise ScoutError(
            f"system {sid!r} is in SCOUT_SYSTEMS but has no entry in {VSP_JSON}"
        )
    return sid


def systems() -> dict:
    """The systems Scout may read, and whether each has a cached SSO session.

    ``cached_session`` means a session file exists, and lists its cookie names.
    It does NOT mean the session still works: ``vsp sso status`` does not report
    liveness, so nothing here can promise it. The only proof is a real read.
    """
    cfg = {k.lower(): v for k, v in _configured().items()}
    out = []
    for sid in _allowlist():
        entry = cfg.get(sid)
        cache = Path.home() / ".vsp" / "sso" / f"{sid}.json"
        row = {
            "system": sid,
            "configured": entry is not None,
            "client": (entry or {}).get("client", ""),
            "auth": (entry or {}).get("auth", ""),
            "cached_session": cache.exists(),
        }
        if cache.exists():
            try:
                data = json.loads(cache.read_text(encoding="utf-8"))
                row["session_cookies"] = sorted(data.get("cookies", {}))
                row["captured_at"] = data.get("captured_at", "")
            except Exception:
                row["session_cookies"] = []
        out.append(row)
    return {
        "systems": out,
        "note": "cached_session is not proof of liveness; only a real read is.",
    }


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------
def _check_table(table: str) -> str:
    t = (table or "").strip().upper()
    if not t:
        raise ScoutError("no table given")
    if not all(c.isalnum() or c in "_/" for c in t):
        raise ScoutError(f"{t!r} is not a table name")
    if not ALLOW_PERSONAL and t.startswith(_PERSONAL_PREFIXES):
        raise ScoutError(
            f"{t} holds person-level data, which Scout refuses by default -- it is "
            "not configuration, and a diff would copy it somewhere new. If you need "
            "it, read it directly with vsp query, or set "
            "SCOUT_ALLOW_PERSONAL_DATA=1 deliberately."
        )
    return t


def _run(argv: list[str]) -> str:
    """The one place a command is executed. Shape is enforced, not assumed."""
    if "query" not in argv:
        raise ScoutError("internal: Scout may only run 'vsp query'")
    for banned in _BANNED_SUBCOMMANDS:
        if banned in argv:
            raise ScoutError(f"internal: refusing to run vsp {banned}")
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired as exc:
        raise ScoutError(
            f"vsp did not answer within {TIMEOUT}s and was stopped. The usual cause "
            "is a stale SSO session waiting on a browser window. Refresh out of "
            "band: vsp sso refresh -s <system>"
        ) from exc
    except FileNotFoundError as exc:
        raise ScoutError(
            f"vsp not found at {VSP_BIN}. Set SCOUT_VSP_BIN. (If it was there "
            "before, check whether endpoint protection quarantined it.)"
        ) from exc

    err = (proc.stderr or "").replace("\r", "")
    if "opening a browser window" in err or "browser was closed" in err:
        raise ScoutError(
            "vsp needed an interactive sign-in for this system, and Scout will not "
            "wait on a browser. Refresh out of band: vsp sso refresh -s <system>"
        )
    if proc.returncode != 0:
        first = next((ln for ln in err.splitlines() if ln.startswith("Error:")), "")
        raise ScoutError(first or err.strip()[:400] or f"vsp exited {proc.returncode}")
    return (proc.stdout or "").replace("\r", "")


def _parse(text: str) -> tuple[list[str], list[list[str]]]:
    """Parse vsp query's block: header TSV, dashed rule, rows, blank, 'N rows'."""
    header: list[str] = []
    rows: list[list[str]] = []
    for line in text.split("\n"):
        if not header:
            if line.strip() and "\t" in line:
                header = line.split("\t")
            continue
        stripped = line.strip()
        if not stripped or set(stripped) == {"-"}:
            continue
        if stripped.endswith(" rows") or stripped.endswith(" row"):
            continue
        cells = line.split("\t")
        # Pad/trim to header width so downstream indexing is always safe.
        rows.append((cells + [""] * len(header))[: len(header)])
    return header, rows


def key_fields(table: str, system: str) -> list[str]:
    """The table's real primary key, read from DD03L. MANDT is dropped.

    Keying a diff on a guessed field produces confident nonsense, so ask the
    dictionary. MANDT is excluded: each system is read in its own client, and
    carrying it into the key would make every row differ for the wrong reason.
    """
    t = _check_table(table)
    sid = _check_system(system)
    out = _run([VSP_BIN, "-s", sid, "query", "DD03L",
                "--fields", "FIELDNAME,KEYFLAG,POSITION",
                "--where", f"TABNAME = '{t}' AND KEYFLAG = 'X'",
                "--top", "60"])
    header, rows = _parse(out)
    if not header:
        raise ScoutError(f"DD03L returned nothing for {t}; does the table exist?")
    fi, pi = header.index("FIELDNAME"), header.index("POSITION")
    ordered = sorted(((r[pi], r[fi]) for r in rows), key=lambda x: x[0])
    keys = [f for _, f in ordered if f != "MANDT"]
    if not keys:
        raise ScoutError(
            f"{t} has no key field besides MANDT, so rows cannot be matched up "
            "between systems. Compare specific fields with scout_read instead."
        )
    return keys


def read(table: str, system: str, fields: str = "", where: str = "",
         top: int = 200, order: str = "") -> dict:
    """Read one table from one system. Read-only."""
    t = _check_table(table)
    sid = _check_system(system)
    n = max(1, min(int(top or 200), MAX_ROWS))
    argv = [VSP_BIN, "-s", sid, "query", t, "--top", str(n)]
    if fields:
        argv += ["--fields", fields]
    if where:
        # The ADT data preview caps the WHERE clause. Say so rather than letting
        # it truncate and return a plausible wrong answer.
        if len(where) > 250:
            raise ScoutError(
                f"WHERE clause is {len(where)} chars; the ADT data preview caps it "
                "near 255. Shorten it, or filter after the read."
            )
        argv += ["--where", where]
    if order:
        if "desc" in order.lower():
            raise ScoutError(
                "the ADT data preview rejects ORDER BY ... DESC. Sort ascending and "
                "reverse the result yourself."
            )
        argv += ["--order", order]
    header, rows = _parse(_run(argv))
    return {
        "system": sid,
        "table": t,
        "fields": header,
        "row_count": len(rows),
        "truncated": len(rows) >= n,
        "rows": [dict(zip(header, r)) for r in rows],
    }


# --------------------------------------------------------------------------
# comparing
# --------------------------------------------------------------------------
# Fields ignored in every comparison unless the caller overrides. MANDT differs
# for reasons that have nothing to do with configuration; the rest are audit
# stamps recording WHEN a row was touched, not WHAT it says, so leaving them in
# makes legitimately-identical rows look different.
DEFAULT_IGNORE = ("MANDT", "AEDAT", "AENAM", "AEZET", "ERDAT", "ERNAM",
                  "UPD_TMSTMP", "LAST_CHANGED", "CHANGED_ON", "CHANGED_BY")


def compare(table: str, left: str, right: str, key: str = "", fields: str = "",
            where: str = "", top: int = 500, ignore: str = "") -> dict:
    """Compare one customizing table between two systems.

    Rows are matched on the table's real primary key (from DD03L, MANDT dropped),
    then classified: present on one side only, present on both but differing, or
    identical. Field-level differences are reported per row.

    A structural difference -- the two systems disagreeing about which fields the
    table has -- is reported as a finding in its own right rather than smoothed
    over, because it means the systems hold different versions of the object.
    """
    t = _check_table(table)
    lsid, rsid = _check_system(left), _check_system(right)
    if lsid == rsid:
        raise ScoutError("left and right are the same system; nothing to compare")

    keys = [k.strip().upper() for k in key.split(",") if k.strip()] or key_fields(t, lsid)
    ignored = {f.strip().upper() for f in ignore.split(",") if f.strip()} or set(DEFAULT_IGNORE)
    ignored -= set(keys)  # never ignore a key: it is how rows are matched

    # Order both sides by the key. Without this, two systems that both hit the
    # row cap can return DIFFERENT rows, and the diff then reports differences
    # that are artifacts of row order rather than of configuration.
    order_by = ",".join(keys)
    lres = read(t, lsid, fields=fields, where=where, top=top, order=order_by)
    rres = read(t, rsid, fields=fields, where=where, top=top, order=order_by)

    lf, rf = set(lres["fields"]), set(rres["fields"])
    structure = {
        "identical": lf == rf,
        "only_in_" + lsid: sorted(lf - rf),
        "only_in_" + rsid: sorted(rf - lf),
    }
    missing_keys = [k for k in keys if k not in lf or k not in rf]
    if missing_keys:
        raise ScoutError(
            "key field(s) " + ", ".join(missing_keys) + " are not in the result. "
            "Either the field list excludes them, or the table differs between "
            "systems. Include the keys in `fields`, or leave `fields` empty."
        )

    compared = sorted((lf & rf) - ignored - set(keys))

    def index(res):
        """Key -> row. Collisions mean the key is not unique, so say so."""
        out, collisions = {}, 0
        for row in res["rows"]:
            kt = tuple(row.get(k, "") for k in keys)
            if kt in out:
                collisions += 1
            out[kt] = row
        return out, collisions

    li, lcol = index(lres)
    ri, rcol = index(rres)
    if lcol or rcol:
        raise ScoutError(
            "key " + ",".join(keys) + " is not unique in " + t + " (" + str(lcol) +
            " duplicate(s) in " + lsid + ", " + str(rcol) + " in " + rsid + "), so "
            "rows cannot be matched up reliably. Pass the full key via `key`, or "
            "compare a narrower slice with `where`."
        )
    only_left = sorted(li.keys() - ri.keys())
    only_right = sorted(ri.keys() - li.keys())
    both = sorted(li.keys() & ri.keys())

    differing = []
    for k in both:
        deltas = {
            f: {lsid: li[k].get(f, ""), rsid: ri[k].get(f, "")}
            for f in compared
            if li[k].get(f, "") != ri[k].get(f, "")
        }
        if deltas:
            differing.append({"key": dict(zip(keys, k)), "differences": deltas})

    truncated = lres["truncated"] or rres["truncated"]
    return {
        "table": t,
        # A truncated read cannot support "these systems agree". Callers must be
        # able to tell a real all-clear from an unfinished one.
        "conclusive": not truncated,
        "left": lsid,
        "right": rsid,
        "key_fields": keys,
        "fields_compared": compared,
        "fields_ignored": sorted(ignored & (lf | rf)),
        "structure": structure,
        "summary": {
            "rows_in_" + lsid: lres["row_count"],
            "rows_in_" + rsid: rres["row_count"],
            "identical": len(both) - len(differing),
            "differing": len(differing),
            "only_in_" + lsid: len(only_left),
            "only_in_" + rsid: len(only_right),
        },
        "differing_rows": differing,
        "rows_only_in_" + lsid: [dict(zip(keys, k)) for k in only_left],
        "rows_only_in_" + rsid: [dict(zip(keys, k)) for k in only_right],
        "truncated": truncated,
        "warning": (
            "INCONCLUSIVE: the row cap of " + str(top) + " was hit on at least one "
            "side, so only the first " + str(top) + " rows by key were compared. "
            "Differences may exist beyond the cap -- 'no differences' here does NOT "
            "mean the systems agree. Raise `top` or narrow with `where`."
        ) if truncated else "",
    }


# --------------------------------------------------------------------------
# table catalog
# --------------------------------------------------------------------------
# Standard SAP customizing tables, grouped for Record-to-Report work. These are
# SAP's own names and carry nothing site-specific. Deliberately excluded: TCURR
# (exchange rates -- volatile, enormous, not really configuration) and master
# data, which the personal-data guard also covers.
CATALOG = {
    "FI": [
        ("T001", "Company codes"),
        ("T001B", "Permitted posting periods"),
        ("T003", "Document types"),
        ("T003T", "Document type texts"),
        ("T004", "Charts of accounts"),
        ("T009", "Fiscal year variants"),
        ("T009B", "Fiscal year variant periods"),
        ("T043G", "Tolerance groups, customers/vendors"),
        ("T030", "Automatic account determination"),
        ("T001R", "Rounding rules"),
    ],
    "CO": [
        ("TKA01", "Controlling areas"),
        ("TKA02", "Controlling area assignment"),
        ("TKA09", "Versions, general"),
        ("CSKA", "Cost elements, chart-of-accounts level"),
        ("CSKB", "Cost elements, controlling-area level"),
        ("TKT09", "Version texts"),
    ],
    "AA": [
        ("T093", "Real depreciation areas"),
        ("T093B", "Depreciation area company-code data"),
        ("T093C", "Company codes in Asset Accounting"),
        ("T095", "Balance sheet accounts for asset classes"),
        ("T096", "Chart-of-depreciation definition"),
    ],
    "TAX": [
        ("T005", "Countries"),
        ("T007A", "Tax keys"),
        ("T007B", "Tax processing keys"),
        ("T007S", "Tax code names"),
        ("T059P", "Withholding tax types"),
    ],
    "BANK": [
        ("T012", "House banks"),
        ("T012K", "House bank accounts"),
        ("T042", "Payment program configuration"),
        ("T042Z", "Payment methods"),
        ("T042E", "Payment method / company code data"),
    ],
    "CROSS": [
        ("T000", "Clients"),
        ("TCURV", "Exchange rate type properties"),
        ("TCURF", "Exchange rate conversion factors"),
        ("T882", "Ledgers"),
        ("T881", "Ledger definition"),
    ],
}


def table_catalog(area: str = "") -> dict:
    """The customizing tables Scout knows about, by area.

    A starting point, not a boundary: compare() accepts any table name.
    """
    a = (area or "").strip().upper()
    if a and a not in CATALOG:
        raise ScoutError("unknown area " + repr(a) + ". Known: " + ", ".join(sorted(CATALOG)))
    areas = [a] if a else sorted(CATALOG)
    return {
        "areas": {
            k: [{"table": t, "description": d} for t, d in CATALOG[k]] for k in areas
        },
        "note": "compare() works on any table; this list is a convenience.",
    }


def compare_area(area: str, left: str, right: str, top: int = 500) -> dict:
    """Sweep every table in an area and report which ones differ.

    One call to find WHERE two systems have drifted, before spending calls on
    WHAT drifted. Tables are independent: one that cannot be read (missing, not
    authorized, not in this release) is recorded as an error against that table
    and the sweep continues.
    """
    a = (area or "").strip().upper()
    if a not in CATALOG:
        raise ScoutError("unknown area " + repr(a) + ". Known: " + ", ".join(sorted(CATALOG)))
    lsid, rsid = _check_system(left), _check_system(right)

    results, differ, same, failed, inconclusive = [], 0, 0, 0, 0
    for tbl, desc in CATALOG[a]:
        try:
            c = compare(tbl, lsid, rsid, top=top)
        except ScoutError as exc:
            failed += 1
            results.append({"table": tbl, "description": desc, "error": str(exc)})
            continue
        s = c["summary"]
        n_diff = s["differing"] + s["only_in_" + lsid] + s["only_in_" + rsid]
        differs = bool(n_diff) or not c["structure"]["identical"]
        if differs:
            differ += 1
        elif c["conclusive"]:
            same += 1
        else:
            # Found nothing, but did not look at everything. Counting this as
            # "identical" would turn an unfinished check into a clean bill of
            # health, which is the one outcome nobody can afford to fake.
            inconclusive += 1
        results.append({
            "table": tbl,
            "description": desc,
            "differs": differs,
            "conclusive": c["conclusive"],
            "verdict": ("differs" if differs
                        else "identical" if c["conclusive"]
                        else "inconclusive (row cap hit)"),
            "summary": s,
            "structure_identical": c["structure"]["identical"],
            "truncated": c["truncated"],
        })
    return {
        "area": a,
        "left": lsid,
        "right": rsid,
        "totals": {"tables": len(CATALOG[a]), "differ": differ,
                   "identical": same, "inconclusive": inconclusive,
                   "unreadable": failed},
        "tables": results,
        "note": "Drill into any table with scout_compare for field-level detail.",
    }

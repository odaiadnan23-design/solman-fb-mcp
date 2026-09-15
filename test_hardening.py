"""Offline tests for the hardening layer and the RFC transport's pure logic.

No network, no SAP session: these cover the parts that must be right even when
the system is unreachable, which is exactly when the old code failed quietly.

Run:  .venv\\Scripts\\python.exe test_hardening.py
"""
from __future__ import annotations

import json
import os
import sys

FAILED: list[str] = []


def check(name: str, got, want) -> None:
    if got == want:
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}\n         got  {got!r}\n         want {want!r}")


def raises(name: str, fn, exc) -> None:
    try:
        fn()
    except exc:
        print(f"  ok   {name}")
        return
    except Exception as e:  # noqa: BLE001
        FAILED.append(name)
        print(f"  FAIL {name}: raised {type(e).__name__}, wanted {exc.__name__}")
        return
    FAILED.append(name)
    print(f"  FAIL {name}: did not raise {exc.__name__}")


# --------------------------------------------------------------------------
print("hardening: read-only kill switch")
import hardening  # noqa: E402

os.environ["SOLMAN_READONLY"] = "1"
check("read_only() true when set", hardening.read_only(), True)
raises("guard_write blocks", lambda: hardening.guard_write("CREATE", "X"),
       hardening.ReadOnlyBlocked)
for flag in ("0", "false", "no", ""):
    os.environ["SOLMAN_READONLY"] = flag
    check(f"read_only() false for {flag!r}", hardening.read_only(), False)
del os.environ["SOLMAN_READONLY"]

print("hardening: auto-refresh switch")
for flag, want in (("0", False), ("false", False), ("no", False), ("1", True)):
    os.environ["SOLMAN_AUTO_REFRESH"] = flag
    check(f"auto_refresh_enabled({flag!r})", hardening.auto_refresh_enabled(), want)
del os.environ["SOLMAN_AUTO_REFRESH"]
check("auto-refresh defaults on", hardening.auto_refresh_enabled(), True)

print("hardening: journal payload summary")
big = {"short": "v", "long": "y" * 500, "items": [1, 2, 3], "nested": {"a": "b" * 400}}
s = hardening._summarize(big)
check("short value kept", s["short"], "v")
check("long value trimmed", len(s["long"]) < 260, True)
check("trim is annotated", s["long"].endswith("chars)"), True)
check("list becomes a count", s["items"], "<3 items>")
check("nested dict summarised", s["nested"]["a"].endswith("chars)"), True)
check("summary is JSON-serialisable", bool(json.dumps(s)), True)

print("hardening: refresh lock is exclusive")
check("first acquire", hardening._acquire_lock(), True)
check("second acquire blocked", hardening._acquire_lock(), False)
hardening._release_lock()
check("acquire again after release", hardening._acquire_lock(), True)
hardening._release_lock()

# --------------------------------------------------------------------------
print("rfc: WHERE splitting (OPTIONS-TEXT is CHAR(72))")
import rfc  # noqa: E402

check("empty where", rfc._split_where(""), [])
rows = rfc._split_where("PROCESS_TYPE = 'S1IT' AND DESCRIPTION LIKE '%GMR6%'")
check("short where stays one row", len(rows), 1)
long_where = ("PROCESS_TYPE = 'S1IT' AND DESCRIPTION LIKE '%GMR6%' AND "
              "CREATED_BY = 'OABDELQA' AND OBJECT_ID LIKE '20000077%' AND "
              "PROCESS_TYPE <> 'S1DM'")
rows = rfc._split_where(long_where)
check("long where is split", len(rows) > 1, True)
check("every row within 72 chars", all(len(r) <= 72 for r in rows), True)
check("rejoining restores the clause", " ".join(rows), long_where)
raises("oversized token rejected",
       lambda: rfc._split_where("FIELD = '" + "x" * 90 + "'"), ValueError)

print("rfc: literal escaping and table-name validation")
check("apostrophe doubled", rfc._sql("O'Brien"), "O''Brien")
raises("newline rejected", lambda: rfc._sql("a\nDROP"), ValueError)
raises("implausible table rejected",
       lambda: rfc.read_table("not a table!", ["X"]), ValueError)
raises("FM off the allowlist rejected",
       lambda: rfc._call("SAVE_TEXT", ""), ValueError)
check("allowlist holds only readers",
      sorted(rfc._ALLOWED), ["RFC_GET_FUNCTION_INTERFACE", "RFC_PING", "RFC_READ_TABLE"])

print("rfc: response unescaping")
check("ampersand entity", rfc._unescape("W.L. Gore &#38; Associates"),
      "W.L. Gore & Associates")
check("angle brackets", rfc._unescape("&lt;tag&gt;"), "<tag>")

print("rfc: truncation warnings are documented")
check("CRMD_ORDERADM_H warned", "GUID" in (rfc.truncation_note("CRMD_ORDERADM_H") or ""), True)
check("lowercase accepted", rfc.truncation_note("crmd_orderadm_h") is not None, True)
check("unknown table has no note", rfc.truncation_note("T000"), None)

# --------------------------------------------------------------------------
print("client: paging guard refuses to duplicate rows")
import client  # noqa: E402


class _StalledGateway(client.SolmanClient):
    """A gateway that ignores $skip and returns page one forever — the real bug."""

    def __init__(self):            # noqa: D107 - no session needed
        self.calls = 0

    def results(self, path, params=None):
        self.calls += 1
        return [{"id": n} for n in range(100)]


stalled = _StalledGateway()
rows = client.SolmanClient.results_all(stalled, "REQUIREMENTSet", page_size=100, max_rows=2000)
check("stops instead of duplicating", len(rows), 100)
check("stall is flagged", stalled.last_page_stalled, True)
check("only two pages fetched", stalled.calls, 2)


class _GoodGateway(_StalledGateway):
    def results(self, path, params=None):
        self.calls += 1
        skip = int((params or {}).get("$skip", 0))
        if skip >= 250:
            return []
        return [{"id": n} for n in range(skip, min(skip + 100, 250))]


good = _GoodGateway()
rows = client.SolmanClient.results_all(good, "X", page_size=100, max_rows=2000)
check("honest paging reads everything", len(rows), 250)
check("no stall flagged", good.last_page_stalled, False)
check("ids are distinct", len({r["id"] for r in rows}), 250)

print("client: function() escapes its parameters")
check("odata_literal doubles quotes", client.odata_literal("Jake's node"), "Jake''s node")

# --------------------------------------------------------------------------
print()
if FAILED:
    print(f"{len(FAILED)} FAILED: {', '.join(FAILED)}")
    sys.exit(1)
print("all offline hardening tests passed")

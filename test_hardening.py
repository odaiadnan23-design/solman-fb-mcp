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
check("allowlist holds the nine proven readers", len(rfc._ALLOWED), 9)
check("allowlist has no writer", not any(w in fm for fm in rfc._ALLOWED
      for w in ("SAVE", "CREATE", "CHANGE", "DELETE", "MODIFY", "UPDATE", "POST")), True)

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


# --------------------------------------------------------------------------
# appended 16-Sep: RFC response parsing and document id handling (offline)
# --------------------------------------------------------------------------
def _late_checks():
    print("rfc: response parsing prefers ET_DATA and keeps full GUIDs")
    et = ("<FIELDS><item><FIELDNAME>GUID</FIELDNAME></item><item><FIELDNAME>OBJECT_ID</FIELDNAME></item></FIELDS>"
          "<ET_DATA><item><LINE>DBBD843BAFA21FD1A0919903A11BDCE5|2000007100</LINE></item></ET_DATA>"
          "<DATA><item><WA>DBBD843BAFA21FD1|2000007100</WA></item></DATA>")
    rows = rfc._parse_rows(et, ["GUID", "OBJECT_ID"])
    check("one row parsed", len(rows), 1)
    check("full 32-char GUID from ET_DATA", rows[0]["GUID"], "DBBD843BAFA21FD1A0919903A11BDCE5")
    check("object id column", rows[0]["OBJECT_ID"], "2000007100")
    wa_only = "<FIELDS><item><FIELDNAME>MANDT</FIELDNAME></item></FIELDS><DATA><item><WA>100</WA></item></DATA>"
    check("WA fallback when no ET_DATA", rfc._parse_rows(wa_only, ["MANDT"])[0]["MANDT"], "100")
    check("echoed FIELDS win over caller order",
          rfc._parse_rows(et, ["OBJECT_ID", "GUID"])[0]["OBJECT_ID"], "2000007100")
    check("entities unescaped in cells",
          rfc._parse_rows("<ET_DATA><item><LINE>W.L. Gore &#38; Associates</LINE></item></ET_DATA>", ["X"])[0]["X"],
          "W.L. Gore & Associates")
    check("read_table requests ET_DATA", "USE_ET_DATA_4_RETURN" in open(rfc.__file__, encoding="utf-8").read(), True)

    print("documents: id/guid handling")
    import documents
    check("dashed guid normalised", documents._nodash("dbbd843b-afa2-1fd1-ac88-45e443b68df0"),
          "DBBD843BAFA21FD1AC8845E443B68DF0")
    check("guid regex accepts 32 hex", bool(documents._GUID_RE.match("DBBD843BAFA21FD1AC8845E443B68DF0")), True)
    check("guid regex rejects an object id", bool(documents._GUID_RE.match("2000007715")), False)
    check("system status names", documents._system_status("I1002"), "Open")
    check("unknown system status passes through", documents._system_status("I9999"), "I9999")

    print("hardening: POST function imports are guarded")
    import client, inspect
    src = inspect.getsource(client.SolmanClient.function_post)
    check("function_post calls guard_write", "guard_write" in src, True)
    check("function_post journals", "journal(" in src, True)
    check("rfc allowlist is readers only",
          all(not any(w in fm for w in ("SAVE", "CREATE", "CHANGE", "DELETE", "MODIFY", "UPDATE"))
              for fm in rfc._ALLOWED), True)


_late_checks()
if FAILED:
    print(f"{len(FAILED)} FAILED: {', '.join(FAILED)}")
    sys.exit(1)
print("late checks passed")


def _module_checks():
    print("structures: constants and guards")
    import structures
    check("unassign action carries the app id", structures.ACTION_UNASSIGN,
          "com.sap.solman.fb.dropdoc_RelevantStructure_Update_Unassign")
    check("guid normalised", structures._g("dbbd843b-afa2-1fd1-ac88-45e443b68df0"),
          "DBBD843BAFA21FD1AC8845E443B68DF0")
    print("charm: text key building")
    import charm, inspect
    src = inspect.getsource(charm.write_text)
    check("write_text uses the batch path", "batch_merge" in src, True)
    check("write_text escapes the type name", "odata_literal" in src, True)
    check("WP app configId is 2", charm.WP_CONFIG_ID, 2)
    print("defects: context is mandatory")
    import defects
    raises("create_defect refuses without test context",
           lambda: defects.create_defect("", "", "x", "y"), Exception)
    print("client: new write verbs are guarded and journalled")
    import client
    for name in ("batch_merge", "batch_create", "put"):
        s2 = inspect.getsource(getattr(client.SolmanClient, name))
        check(f"{name} guarded", "guard_write" in s2, True)
        check(f"{name} journalled", "journal(" in s2, True)
    print("workpackages: WpSystem is SID:CLIENT")
    import workpackages
    s3 = inspect.getsource(workpackages.create_work_item)
    check("create_work_item appends the client", 'config.SAP_CLIENT' in s3 and ':' in s3, True)
    check("set_work_item_component exists", hasattr(workpackages, "set_work_item_component"), True)


_module_checks()
if FAILED:
    print(f"{len(FAILED)} FAILED: {', '.join(FAILED)}")
    sys.exit(1)
print("module checks passed")

"""Unit tests for pure logic (no network). Run: python test_units.py  or  pytest test_units.py"""
from __future__ import annotations

import tempfile
from pathlib import Path

import client
import requirements as rq
import workpackages as wp
import workspaces as wsp
from client import SessionExpired


def test_odata_literal_escapes_quotes():
    assert client.odata_literal("O'Brien") == "O''Brien"
    assert client.odata_literal("won't - can't") == "won''t - can''t"
    assert client.odata_literal("plain") == "plain"


def test_dash_normalizes_guid():
    assert rq._dash("dbbd843b-afa2-1fd1-9dac") == "DBBD843BAFA21FD19DAC"
    assert rq._dash("ABCDEF") == "ABCDEF"


def test_ptype_friendly_and_code():
    assert wsp._ptype("work_package") == "S1IT"
    assert wsp._ptype("requirement") == "S1BR"
    assert wsp._ptype("S1BR") == "S1BR"
    assert wsp._ptype("s1dm") == "S1DM"          # unknown friendly -> upper()
    for bad in ("", None):
        try:
            wsp._ptype(bad)  # type: ignore[arg-type]
            assert False, "expected ValueError"
        except (ValueError, AttributeError):
            pass


def test_redash_roundtrip_and_is_guid():
    g = "DBBD843BAFA21FD19DAC41D474156E7F"
    assert wsp._redash(g) == "DBBD843B-AFA2-1FD1-9DAC-41D474156E7F"
    assert wsp._nodash(wsp._redash(g)) == g
    assert wsp._is_guid(g) and wsp._is_guid(wsp._redash(g))
    assert not wsp._is_guid("1000038547")
    assert not wsp._is_guid("short")


def test_wp_redash_matches_workspaces():
    g = "DBBD843BAFA21FD19DAC41D474156E7F"
    assert wp._redash(g) == wsp._redash(g)


def test_classification_map():
    assert set(rq.CLASSIFICATION) == {"fit", "gap", "wricef", "non-functional"}
    assert rq.CLASSIFICATION["fit"] == ("F", "Fit")


def test_load_cookies_parses_and_validates():
    good = ("# Netscape HTTP Cookie File\n"
            "host\tFALSE\t/\tTRUE\t0\tSAP_SESSIONID_S01_100\tABC123\n"
            "host\tFALSE\t/\tTRUE\t0\tsap-usercontext\tsap-client=100\n")
    p = Path(tempfile.mktemp())
    p.write_text(good, encoding="utf-8")
    try:
        ck = client.load_cookies(p)
        assert ck["SAP_SESSIONID_S01_100"] == "ABC123"
        assert ck["sap-usercontext"] == "sap-client=100"
    finally:
        p.unlink()


def test_load_cookies_requires_session_cookie():
    bad = "# Netscape\nhost\tFALSE\t/\tTRUE\t0\tsap-usercontext\tsap-client=100\n"
    p = Path(tempfile.mktemp())
    p.write_text(bad, encoding="utf-8")
    try:
        client.load_cookies(p)
        assert False, "expected SessionExpired"
    except SessionExpired:
        pass
    finally:
        p.unlink()


def test_create_requirement_validates_before_network():
    for kwargs in ({"title": ""}, {"title": "x", "classification": "bogus"}):
        try:
            rq.create_requirement(**kwargs)
            assert False, f"expected ValueError for {kwargs}"
        except ValueError:
            pass


def test_create_work_package_validates_before_network():
    for kwargs in ({"requirement_guid": "", "title": "x"},
                   {"requirement_guid": "g", "title": ""},
                   {"requirement_guid": "g", "title": "x", "classification": "nope"}):
        try:
            wp.create_work_package(**kwargs)
            assert False, f"expected ValueError for {kwargs}"
        except ValueError:
            pass


def test_update_requirement_rejects_unknown_field():
    try:
        rq.update_requirement("guid", BogusField="x")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_retry_io_recovers_from_transient_faults():
    import httpx
    calls = {"n": 0}
    saved = client._RETRY_BASE_DELAY
    client._RETRY_BASE_DELAY = 0.01
    try:
        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise httpx.ConnectError("transient")
            return "ok"
        assert client._retry_io(flaky, client._RETRIABLE_READ) == "ok"
        assert calls["n"] == 3
    finally:
        client._RETRY_BASE_DELAY = saved


def test_retry_io_write_does_not_retry_protocol_errors():
    import httpx
    calls = {"n": 0}
    def protofail():
        calls["n"] += 1
        raise httpx.RemoteProtocolError("may have been sent")
    try:
        client._retry_io(protofail, client._RETRIABLE_WRITE)
        assert False, "expected RemoteProtocolError"
    except httpx.RemoteProtocolError:
        assert calls["n"] == 1  # a write must NOT be re-sent on ambiguous faults


def test_solution_match_semantics():
    import solutions as sol
    rows = [{"id": "AAA1", "name": "Main Solution-ALPHA"},
            {"id": "BBB2", "name": "Main Solution-BETA"},
            {"id": "CCC3", "name": "Non-SAP Solution-GAMMA"}]
    m = lambda q: sol._match(q, rows, "id", "name", "solution")
    assert m("BBB2")["id"] == "BBB2"                       # exact id
    assert m("main solution-alpha")["id"] == "AAA1"        # exact name, case-insensitive
    assert m("GAMMA")["id"] == "CCC3"                      # unique substring
    try:
        m("Main")                                          # ambiguous substring
        assert False, "expected ValueError"
    except ValueError as e:
        assert "ambiguous" in str(e)
    try:
        m("ZZZ")                                           # no match
        assert False, "expected ValueError"
    except ValueError as e:
        assert "no solution matches" in str(e)


def test_results_all_pages_and_caps():
    class Fake:
        def __init__(self, total): self.total = total
        def results(self, path, params):
            top, skip = int(params["$top"]), int(params["$skip"])
            return [{"i": n} for n in range(skip, min(skip + top, self.total))]
    Fake.results_all = client.SolmanClient.results_all
    f = Fake(237)
    rows = f.results_all("X", page_size=100)
    assert len(rows) == 237 and rows[-1]["i"] == 236
    assert len(f.results_all("X", page_size=100, max_rows=150)) == 150


def test_testsuite_key_builder():
    import testsuite as tst
    k = tst._key("ABC123", 2, "DE")
    assert k == "TestCaseHeaderSet(CaseId='ABC123',CaseVersion=2,Language='DE')"
    assert "Language='EN'" in tst._key("X")  # default language


def test_testsuite_upload_field_map_covers_template():
    import testsuite as tst
    headers = ["Test Case Name", "Folder", "Step Number", "Step Description",
               "Step Expected Result", "Test Case Description"]
    for h in headers:
        assert tst._UPLOAD_FIELD_KEYS.get(h.lower()), f"no key for {h!r}"
    assert tst._UPLOAD_FIELD_KEYS["test case name"] == "CASE.NAME"
    assert tst._UPLOAD_FIELD_KEYS["step expected result"] == "STXT.EXPECTED_RESULT"


def test_testsuite_update_rejects_unknown_field():
    import testsuite as tst
    try:
        tst.update_test_case("cid", 1, "EN", BogusField="x")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_no_mcp_tool_calls_itself():
    """A tool must not reference its own name in its body.

    server.py imports helpers (`session_status`) and also defines tools with the
    same name. The def shadows the import, so `_wrap(session_status)` hands the
    tool to itself and recurses until the process is wedged -- which reads as a
    hung MCP call, not as an error. Import the helper under an alias instead.
    """
    import ast
    from pathlib import Path

    tree = ast.parse(Path(__file__).with_name("server.py").read_text(encoding="utf-8"))

    def is_tool(node):
        return isinstance(node, ast.FunctionDef) and any(
            (isinstance(d, ast.Call) and getattr(d.func, "attr", None) == "tool")
            or getattr(d, "attr", None) == "tool"
            for d in node.decorator_list
        )

    tools = {n.name for n in tree.body if is_tool(n)}
    offenders = []
    for node in tree.body:
        if not is_tool(node):
            continue
        for sub in ast.walk(node):
            # A bare Name that is itself a tool: either self-recursion, or a
            # helper import shadowed by a tool of the same name. Both wedge.
            if isinstance(sub, ast.Name) and sub.id in tools:
                offenders.append(f"{node.name} -> {sub.id} (line {sub.lineno})")
    assert not offenders, "tool body references a tool name: " + ", ".join(offenders)


# --- Customizing Scout (no network) ---------------------------------------

_VSP_BLOCK = (
    "BUKRS\tBUTXT\tWAERS\n"
    "--------------------------------------------------------------------------------\n"
    "0001\tAlpha Manufacturing\tEUR\n"
    "0002\tBeta Trading\tUSD\n"
    "\n"
    "2 rows\n"
)


def test_scout_parses_vsp_query_block():
    import scout
    header, rows = scout._parse(_VSP_BLOCK)
    assert header == ["BUKRS", "BUTXT", "WAERS"]
    assert len(rows) == 2, rows          # the 'N rows' footer must not become a row
    assert rows[0] == ["0001", "Alpha Manufacturing", "EUR"]


def test_scout_parse_pads_short_rows():
    """Trailing empty columns must not shift values into the wrong field."""
    import scout
    header, rows = scout._parse("A\tB\tC\n---\nx\ty\n\n1 rows\n")
    assert rows == [["x", "y", ""]]


def test_scout_refuses_person_level_tables():
    import scout
    for tbl in ("PA0002", "ADRC", "KNA1", "LFA1", "BUT000"):
        try:
            scout._check_table(tbl)
            assert False, f"{tbl} should be refused"
        except scout.ScoutError as e:
            assert "person-level" in str(e)
    assert scout._check_table("t001") == "T001"   # normal customizing still fine


def test_scout_rejects_non_table_names():
    import scout
    for bad in ("T001; DROP", "T001 OR 1=1", "--", ""):
        try:
            scout._check_table(bad)
            assert False, f"{bad!r} should be refused"
        except scout.ScoutError:
            pass


def test_scout_fails_closed_without_allowlist():
    """No SCOUT_SYSTEMS means no system is reachable. This is the production guard."""
    import os
    import scout
    saved = os.environ.pop("SCOUT_SYSTEMS", None)
    try:
        try:
            scout._check_system("anything")
            assert False, "should refuse when SCOUT_SYSTEMS is unset"
        except scout.ScoutError as e:
            assert "SCOUT_SYSTEMS is not set" in str(e)
    finally:
        if saved is not None:
            os.environ["SCOUT_SYSTEMS"] = saved


def test_scout_refuses_system_outside_allowlist():
    import os
    import scout
    saved = os.environ.get("SCOUT_SYSTEMS")
    os.environ["SCOUT_SYSTEMS"] = "dev,qa"
    try:
        try:
            scout._check_system("prd")
            assert False, "a system outside the allowlist must be refused"
        except scout.ScoutError as e:
            assert "not in SCOUT_SYSTEMS" in str(e)
    finally:
        if saved is None:
            os.environ.pop("SCOUT_SYSTEMS", None)
        else:
            os.environ["SCOUT_SYSTEMS"] = saved


def test_scout_run_refuses_anything_but_query():
    """_run is the only exec point; it must reject every write-capable subcommand."""
    import scout
    for bad in ("deploy", "execute", "install", "transport"):
        try:
            scout._run(["vsp", "-s", "dev", bad, "X"])
            assert False, f"vsp {bad} should be refused"
        except scout.ScoutError as e:
            assert "refusing to run" in str(e) or "only run" in str(e)


def test_scout_ignores_mandt_by_default():
    import scout
    assert "MANDT" in scout.DEFAULT_IGNORE


def test_scout_catalog_areas_are_well_formed():
    import scout
    assert {"FI", "CO", "AA", "TAX", "BANK"} <= set(scout.CATALOG)
    for area, tables in scout.CATALOG.items():
        for tbl, desc in tables:
            assert tbl == tbl.upper() and desc, (area, tbl)
            # the catalog must never steer a caller at person-level data
            assert not tbl.startswith(scout._PERSONAL_PREFIXES), (area, tbl)

if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for fn in fns:
        try:
            fn()
            passed += 1
            print(f"  PASS {fn.__name__}")
        except Exception as e:  # noqa: BLE001
            print(f"  FAIL {fn.__name__}: {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(fns)} passed")

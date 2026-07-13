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

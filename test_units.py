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
            "host\tFALSE\t/\tTRUE\t0\tSAP_SESSIONID_PM1_100\tABC123\n"
            "host\tFALSE\t/\tTRUE\t0\tsap-usercontext\tsap-client=100\n")
    p = Path(tempfile.mktemp())
    p.write_text(good, encoding="utf-8")
    try:
        ck = client.load_cookies(p)
        assert ck["SAP_SESSIONID_PM1_100"] == "ABC123"
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

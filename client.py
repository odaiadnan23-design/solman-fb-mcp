"""Headless HTTP client for SolMan OData — cookie-only, no browser.

Loads the session cookie minted by ``refresh_session.py`` and handles the SAP CSRF
token dance. Targets BUSINESS_REQUIREMENTS_SRV (the purpose-built requirements
API) by default; can be pointed at any SALM service.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Callable, TypeVar

import httpx

import config
import hardening

# httpx logs every request line at INFO, which puts full query strings — filters,
# ids, GUIDs — into whatever captures stdout. Useful when debugging, noise and a
# small disclosure risk otherwise. Set SOLMAN_HTTP_LOG=1 to get it back.
if os.environ.get("SOLMAN_HTTP_LOG", "").strip().lower() not in {"1", "true", "yes"}:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

T = TypeVar("T")

# Transient network faults worth retrying. GETs are idempotent -> retry broadly
# (dead pooled connections after a server-side keepalive close raise
# RemoteProtocolError/ReadError on reuse; a VPN flap raises ConnectError).
# Writes retry ONLY on ConnectError (the request never left the machine).
_RETRIABLE_READ = (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError, httpx.ReadTimeout)
_RETRIABLE_WRITE = (httpx.ConnectError,)
_RETRY_TRIES = 3
_RETRY_BASE_DELAY = 1.0  # seconds; doubles per attempt


def _retry_io(call: Callable[[], T], retriable: tuple[type[BaseException], ...]) -> T:
    """Run call() with exponential backoff on the given transient exceptions."""
    delay = _RETRY_BASE_DELAY
    for attempt in range(_RETRY_TRIES):
        try:
            return call()
        except retriable:
            if attempt == _RETRY_TRIES - 1:
                raise
            time.sleep(delay)
            delay *= 2
    raise AssertionError("unreachable")


class SessionExpired(RuntimeError):
    """Cookie missing/expired — user must run refresh_session.py."""


class SolmanError(RuntimeError):
    """A SAP OData error (non-auth)."""


def load_cookies(path: Path = config.COOKIE_FILE) -> dict[str, str]:
    """Parse a Netscape cookie file into a name->value dict."""
    if not path.exists():
        raise SessionExpired(f"No cookie file at {path}. Run: python refresh_session.py")
    cookies: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            cookies[parts[5]] = parts[6]
    if not any(k.startswith(config.SESSION_COOKIE_PREFIX) for k in cookies):
        raise SessionExpired(f"No {config.SESSION_COOKIE_PREFIX}* cookie in {path}. Run refresh_session.py.")
    return cookies


def odata_literal(value: str) -> str:
    """Escape a string for use inside an OData v2 single-quoted literal (' -> '')."""
    return str(value).replace("'", "''")


def _sap_error_message(resp: httpx.Response) -> str:
    try:
        err = resp.json().get("error", {})
        msg = err.get("message", {})
        return msg.get("value", "") if isinstance(msg, dict) else str(msg)
    except Exception:  # noqa: BLE001
        return resp.text[:400]


class SolmanClient:
    def __init__(self, service: str = config.SVC_BIZ_REQ) -> None:
        self.service = service
        self._csrf: str | None = None
        self._http = httpx.Client(
            base_url=config.BASE_URL,
            cookies=load_cookies(),
            verify=config.VERIFY_TLS,
            # Split timeouts: fail fast when the VPN is down (connect), stay
            # patient once the gateway has the request (read). A flat 60s meant a
            # dropped tunnel hung every call for a minute.
            timeout=httpx.Timeout(connect=10.0, read=90.0, write=30.0, pool=10.0),
            limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
            headers={"Accept": "application/json"},
            params={"sap-client": config.SAP_CLIENT},
        )

    def _reload_cookies(self) -> None:
        """Pick up a freshly minted cookie without discarding the connection pool."""
        self._http.cookies.clear()
        for name, value in load_cookies().items():
            self._http.cookies.set(name, value)
        self._csrf = None

    def _recover_session(self, retry: Callable[[], T]) -> T:
        """Re-mint the session once, then run retry(). Raises if it cannot.

        This is the difference between a 20-minute audit dying half way and it
        simply continuing: the cookie expires on a clock, not on demand, so a long
        read is almost guaranteed to cross the boundary.
        """
        if not hardening.auto_refresh_enabled():
            raise
        outcome = hardening.refresh_session()
        if not outcome.get("refreshed"):
            raise SessionExpired(
                "session expired and the automatic refresh did not succeed: "
                f"{outcome.get('reason')}"
            )
        self._reload_cookies()
        return retry()

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "SolmanClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- auth / csrf -------------------------------------------------------
    def _raise_for_auth(self, r: httpx.Response) -> None:
        if r.status_code in (401, 403) and "require" not in r.headers.get("x-csrf-token", "").lower():
            raise SessionExpired(
                f"HTTP {r.status_code} from the SolMan system — session expired/unauthorized. "
                "Run: python refresh_session.py"
            )

    def _ensure_csrf(self) -> str:
        if self._csrf:
            return self._csrf
        r = _retry_io(lambda: self._http.get(f"{self.service}/", headers={"X-CSRF-Token": "Fetch"}),
                      _RETRIABLE_READ)
        token = r.headers.get("x-csrf-token")
        if not token:
            self._raise_for_auth(r)
            raise SessionExpired("CSRF fetch returned no token — session likely expired. Run refresh_session.py.")
        self._csrf = token
        return token

    # -- reads -------------------------------------------------------------
    def _check_session(self, r: httpx.Response) -> None:
        """An expired SAML/IAS session returns the login HTML (200), not JSON."""
        if "text/html" in r.headers.get("content-type", "").lower():
            raise SessionExpired(
                "SolMan session expired (SAML login page returned). Run: python refresh_session.py"
            )

    def get(self, path: str, params: dict | None = None,
            _recovered: bool = False) -> dict:
        p = {"$format": "json", **(params or {})}
        r = _retry_io(
            lambda: self._http.get(f"{self.service}/{path}", params=p,
                                   headers={"Accept": "application/json"}),
            _RETRIABLE_READ,
        )
        try:
            self._raise_for_auth(r)
            self._check_session(r)
        except SessionExpired:
            if _recovered:
                raise
            return self._recover_session(
                lambda: self.get(path, params, _recovered=True))
        if r.status_code >= 400:
            raise SolmanError(f"GET {path} -> {r.status_code}: {_sap_error_message(r)}")
        return r.json()

    def results(self, path: str, params: dict | None = None) -> list[dict]:
        d = self.get(path, params).get("d", {})
        rows = d.get("results", d) if isinstance(d, dict) else d
        return rows if isinstance(rows, list) else [rows]

    def results_all(self, path: str, params: dict | None = None,
                    page_size: int = 100, max_rows: int = 2000) -> list[dict]:
        """Page through an entity set, refusing to duplicate rows.

        THE TRAP THIS GUARDS: on this gateway ``$skip`` is not always honoured —
        measured on REQUIREMENTSet, where every page came back as page one, so a
        naive loop "read" 6000 rows that were 100 rows repeated 60 times. The old
        version returned them, and an audit built on it was silently wrong.

        Each page is fingerprinted; if a page repeats one already seen, paging is
        not advancing and we stop there and mark the result. Callers get fewer rows
        than they asked for, never phantom ones.
        """
        rows: list[dict] = []
        seen: set[str] = set()
        skip = 0
        self.last_page_stalled = False
        while len(rows) < max_rows:
            page = self.results(path, {**(params or {}),
                                       "$top": str(page_size), "$skip": str(skip)})
            if not page:
                break
            fingerprint = json.dumps(page[0], sort_keys=True, default=str)[:512]
            if fingerprint in seen:
                self.last_page_stalled = True
                break
            seen.add(fingerprint)
            rows.extend(page)
            if len(page) < page_size:
                break
            skip += page_size
        return rows[:max_rows]

    # -- writes ------------------------------------------------------------
    def create(self, entityset: str, body: dict) -> dict:
        hardening.guard_write("CREATE", entityset)
        token = self._ensure_csrf()
        r = self._retry_csrf(lambda t: _retry_io(lambda: self._http.post(
            f"{self.service}/{entityset}",
            headers={"X-CSRF-Token": t, "Content-Type": "application/json", "Accept": "application/json"},
            content=json.dumps(body).encode("utf-8"),
        ), _RETRIABLE_WRITE), token)
        self._raise_for_auth(r)
        if r.status_code not in (200, 201):
            hardening.journal("CREATE", self.service, entityset, body,
                              status=r.status_code, error=_sap_error_message(r))
            raise SolmanError(f"CREATE {entityset} -> {r.status_code}: {_sap_error_message(r)}")
        hardening.journal("CREATE", self.service, entityset, body, status=r.status_code)
        return r.json().get("d", r.json())

    def merge(self, key_path: str, body: dict) -> None:
        hardening.guard_write("MERGE", key_path)
        token = self._ensure_csrf()
        r = self._retry_csrf(lambda t: _retry_io(lambda: self._http.request(
            "MERGE", f"{self.service}/{key_path}",
            headers={"X-CSRF-Token": t, "Content-Type": "application/json", "Accept": "application/json"},
            content=json.dumps(body).encode("utf-8"),
        ), _RETRIABLE_WRITE), token)
        self._raise_for_auth(r)
        if r.status_code not in (200, 204):
            hardening.journal("MERGE", self.service, key_path, body,
                              status=r.status_code, error=_sap_error_message(r))
            raise SolmanError(f"MERGE {key_path} -> {r.status_code}: {_sap_error_message(r)}")
        hardening.journal("MERGE", self.service, key_path, body, status=r.status_code)

    def function(self, name: str, str_params: dict | None = None) -> dict:
        """Call a FunctionImport (GET). String params are OData-quoted automatically.

        Note: several SALM 'get_*' function imports actually EXECUTE actions
        (e.g. get_ppf_actions runs a PPF action), so this requires a CSRF token.
        """
        if name not in hardening.READ_ONLY_FUNCTIONS:
            hardening.guard_write("FUNCTION", name)
        token = self._ensure_csrf()
        q: dict = {"$format": "json"}
        for k, v in (str_params or {}).items():
            # odata_literal matters here: an unescaped apostrophe in a value
            # closed the literal and the gateway parsed the rest as OData.
            q[k] = f"'{odata_literal(v)}'" if isinstance(v, str) else v
        r = self._retry_csrf(lambda t: _retry_io(lambda: self._http.get(
            f"{self.service}/{name}", params=q,
            headers={"X-CSRF-Token": t, "Accept": "application/json"}), _RETRIABLE_WRITE), token)
        self._raise_for_auth(r)
        if name not in hardening.READ_ONLY_FUNCTIONS:
            hardening.journal("FUNCTION", self.service, name, str_params,
                              status=r.status_code,
                              error=None if r.status_code < 400 else _sap_error_message(r))
        if r.status_code >= 400:
            raise SolmanError(f"FUNCTION {name} -> {r.status_code}: {_sap_error_message(r)}")
        return r.json()

    def _retry_csrf(self, call, token: str) -> httpx.Response:
        """Run call(token); if CSRF token went stale (403 + 'Required'), refetch once."""
        r = call(token)
        if r.status_code == 403 and "require" in r.headers.get("x-csrf-token", "").lower():
            self._csrf = None
            r = call(self._ensure_csrf())
        return r


# --- Shared client cache (reused across calls; reloads when the cookie changes) ---
_CLIENTS: dict[str, tuple["SolmanClient", float]] = {}


def _cookie_mtime() -> float:
    try:
        return config.COOKIE_FILE.stat().st_mtime
    except OSError:
        return 0.0


def client_for(service: str = config.SVC_BIZ_REQ) -> "SolmanClient":
    """Return a cached client for a service, rebuilt if the cookie file changed on disk.

    Reuses the httpx connection pool and CSRF token across calls; picks up a fresh
    session automatically after refresh_session.py rewrites the cookie file.
    """
    mtime = _cookie_mtime()
    cached = _CLIENTS.get(service)
    if cached and cached[1] == mtime:
        return cached[0]
    if cached:
        cached[0].close()
    _CLIENTS[service] = (SolmanClient(service), mtime)
    return _CLIENTS[service][0]


def reset_clients() -> None:
    """Drop all cached clients (e.g. after a session error)."""
    for c, _ in _CLIENTS.values():
        c.close()
    _CLIENTS.clear()


def session_status() -> dict:
    """Cheap check that the cookie is present and the session is live."""
    try:
        client_for(config.SVC_BIZ_REQ).get("PRIORITYSet", {"$top": "1"})
        return {"valid": True, "host": config.SAP_HOST, "client": config.SAP_CLIENT}
    except SessionExpired as e:
        reset_clients()
        return {"valid": False, "reason": str(e)}


def preflight() -> dict:
    """Everything worth knowing before trusting this connection, in one call.

    Written because the failures that cost the most time were never "the call
    returned an error" — they were a silent wrong answer from a half-working
    connection: an expired cookie mid-run, TLS quietly unverified, a write landing
    in the wrong solution because a target was unset, a read-only session the
    caller did not know was read-only.
    """
    import hardening as _h

    out: dict = {
        "host": config.SAP_HOST or "(unset)",
        "client": config.SAP_CLIENT,
        "base_url": config.BASE_URL,
    }

    # --- TLS: say plainly whether certificates are being checked -----------
    v = config.VERIFY_TLS
    if v is False:
        out["tls"] = {"verifying": False,
                      "mode": "DISABLED via SOLMAN_INSECURE_TLS",
                      "warning": "certificates are not being checked — traffic is "
                                 "interceptable. Unset SOLMAN_INSECURE_TLS."}
    elif isinstance(v, str):
        out["tls"] = {"verifying": True, "mode": f"CA bundle {v}"}
    elif v is True:
        out["tls"] = {"verifying": True, "mode": "certifi (truststore unavailable)",
                      "note": "an internal CA usually is not in certifi; install "
                              "truststore if verification fails"}
    else:
        out["tls"] = {"verifying": True, "mode": "OS trust store (truststore)"}

    # --- session -----------------------------------------------------------
    out["cookie"] = _h.cookie_health()
    out["session"] = session_status()
    out["auto_refresh"] = _h.auto_refresh_enabled()

    # --- write posture -----------------------------------------------------
    out["read_only"] = _h.read_only()
    out["config"] = config.validate_env()
    out["journal"] = {"path": str(_h.JOURNAL),
                      "recent_writes": len(_h.recent_writes(limit=1000))}

    # --- the RFC read transport -------------------------------------------
    try:
        import rfc as _rfc
        out["rfc_transport"] = _rfc.probe()
    except Exception as ex:  # noqa: BLE001
        out["rfc_transport"] = {"available": False, "reason": f"{type(ex).__name__}: {ex}"}

    problems = []
    if not out["session"].get("valid"):
        problems.append("session not valid — run refresh_session.py")
    if out["tls"]["verifying"] is False:
        problems.append("TLS verification disabled")
    if out["cookie"].get("warning"):
        problems.append(out["cookie"]["warning"])
    if isinstance(out["cookie"].get("acl"), str) and "BROAD" in out["cookie"]["acl"]:
        problems.append("cookie file ACL is broad — run hardening.harden_cookie()")
    if not out["config"].get("writes_ready"):
        problems.append("writes blocked: " + ", ".join(out["config"]["missing_for_writes"]))
    out["problems"] = problems
    out["ok"] = not problems
    return out

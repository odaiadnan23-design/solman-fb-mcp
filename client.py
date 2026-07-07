"""Headless HTTP client for SolMan OData — cookie-only, no browser.

Loads the session cookie minted by ``refresh_session.py`` and handles the SAP CSRF
token dance. Targets BUSINESS_REQUIREMENTS_SRV (the purpose-built requirements
API) by default; can be pointed at any SALM service.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx

import config


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
            timeout=60.0,
            headers={"Accept": "application/json"},
            params={"sap-client": config.SAP_CLIENT},
        )

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
        r = self._http.get(f"{self.service}/", headers={"X-CSRF-Token": "Fetch"})
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

    def get(self, path: str, params: dict | None = None) -> dict:
        p = {"$format": "json", **(params or {})}
        r = self._http.get(f"{self.service}/{path}", params=p, headers={"Accept": "application/json"})
        self._raise_for_auth(r)
        self._check_session(r)
        if r.status_code >= 400:
            raise SolmanError(f"GET {path} -> {r.status_code}: {_sap_error_message(r)}")
        return r.json()

    def results(self, path: str, params: dict | None = None) -> list[dict]:
        d = self.get(path, params).get("d", {})
        rows = d.get("results", d) if isinstance(d, dict) else d
        return rows if isinstance(rows, list) else [rows]

    # -- writes ------------------------------------------------------------
    def create(self, entityset: str, body: dict) -> dict:
        token = self._ensure_csrf()
        r = self._retry_csrf(lambda t: self._http.post(
            f"{self.service}/{entityset}",
            headers={"X-CSRF-Token": t, "Content-Type": "application/json", "Accept": "application/json"},
            content=json.dumps(body).encode("utf-8"),
        ), token)
        self._raise_for_auth(r)
        if r.status_code not in (200, 201):
            raise SolmanError(f"CREATE {entityset} -> {r.status_code}: {_sap_error_message(r)}")
        return r.json().get("d", r.json())

    def merge(self, key_path: str, body: dict) -> None:
        token = self._ensure_csrf()
        r = self._retry_csrf(lambda t: self._http.request(
            "MERGE", f"{self.service}/{key_path}",
            headers={"X-CSRF-Token": t, "Content-Type": "application/json", "Accept": "application/json"},
            content=json.dumps(body).encode("utf-8"),
        ), token)
        self._raise_for_auth(r)
        if r.status_code not in (200, 204):
            raise SolmanError(f"MERGE {key_path} -> {r.status_code}: {_sap_error_message(r)}")

    def function(self, name: str, str_params: dict | None = None) -> dict:
        """Call a FunctionImport (GET). String params are OData-quoted automatically.

        Note: several SALM 'get_*' function imports actually EXECUTE actions
        (e.g. get_ppf_actions runs a PPF action), so this requires a CSRF token.
        """
        token = self._ensure_csrf()
        q: dict = {"$format": "json"}
        for k, v in (str_params or {}).items():
            q[k] = f"'{v}'" if isinstance(v, str) else v
        r = self._retry_csrf(lambda t: self._http.get(
            f"{self.service}/{name}", params=q, headers={"X-CSRF-Token": t, "Accept": "application/json"}), token)
        self._raise_for_auth(r)
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

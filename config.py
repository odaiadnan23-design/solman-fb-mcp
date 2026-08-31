"""Shared configuration for the SolMan Focused Build MCP server.

All deployment-specific values (host, client, solution/branch/project ids, owner)
come from environment variables, loaded from a local ``.env`` if present. Nothing
site-specific is hardcoded here — copy ``.env.example`` to ``.env`` and fill it in.
Secrets (the session cookie) live OUTSIDE the repo under ``%USERPROFILE%\\.solman-mcp``.
"""
from __future__ import annotations

import os
from pathlib import Path


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no external dependency). Existing env vars win."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv(Path(__file__).parent / ".env")


def _req(name: str) -> str:
    """Env value, or "" at import time.

    Deliberately does NOT raise here: the module is imported by read-only tools
    that need none of these, and raising at import would take the whole server
    down over a value they never touch. Enforcement happens at the point of use
    via require() below, which is what the old docstring promised and never did.
    """
    return os.environ.get(name, "")


class ConfigError(RuntimeError):
    """A required deployment value is missing or blank."""


# Every value that must be present before a WRITE, with what it is and how to
# find it. Used by require() for the error text and by validate_env() at startup.
REQUIRED_FOR_WRITES: dict[str, str] = {
    "SOLMAN_SOLUTION_ID": "target solution GUID — list with solution_overview()",
    "SOLMAN_BRANCH_ID": "target branch GUID — list with list_branches(solution_id)",
    "SOLMAN_OWNER_BP": "requirement owner business partner number",
    "SOLMAN_OWNER_NAME": "requirement owner display name",
    "SOLMAN_TEAM_NAME": "development team name",
    "SOLMAN_TEAM_BP": "development team business partner number",
    "SOLMAN_PLANNED_PROJECT": "planned project id (e.g. MYPROJ_1.0_BUILD)",
    "SOLMAN_PLANNED_PROJECT_GUID": "planned project GUID",
}


def require(name: str) -> str:
    """Return a required env value, or raise with a message that says what to do.

    WHY THIS EXISTS — the wrong-target trap. The previous build defaulted every
    write to a single SOLMAN_SOLUTION_ID. Where a system hosts several solutions
    (commonly one per landscape), work for one of them filed against the default
    landed in the wrong solution SILENTLY, because a missing value became "" and
    the call went ahead anyway. v2 fails closed: an unset target is an error,
    never a guess.
    """
    val = os.environ.get(name, "").strip()
    if not val:
        hint = REQUIRED_FOR_WRITES.get(name, "")
        raise ConfigError(
            f"{name} is not set" + (f" ({hint})" if hint else "") + ".\n"
            "This value is deployment-specific and is never defaulted, because a wrong "
            "solution silently files work in the wrong place. Set it in .env, or pass "
            "the target explicitly on the call."
        )
    return val


def validate_env() -> dict:
    """Report configuration health without raising. For session_status()/startup."""
    missing = [k for k in REQUIRED_FOR_WRITES if not os.environ.get(k, "").strip()]
    return {
        "host": SAP_HOST or "(unset)",
        "client": SAP_CLIENT,
        "tls_verify": VERIFY_TLS if isinstance(VERIFY_TLS, str) else bool(VERIFY_TLS),
        "reads_ready": bool(SAP_HOST),
        "writes_ready": not missing,
        "missing_for_writes": missing,
        "note": ("reads work; writes are blocked until the values above are set"
                 if missing else "fully configured"),
    }


# --- Target system --------------------------------------------------------
SAP_HOST = os.environ.get("SOLMAN_HOST", "")            # SolMan host, no scheme (from .env)
SAP_PORT = os.environ.get("SOLMAN_PORT", "44300")
SAP_CLIENT = os.environ.get("SAP_CLIENT", "100")
SAP_LANGUAGE = os.environ.get("SAP_LANGUAGE", "EN")
BASE_URL = f"https://{SAP_HOST}:{SAP_PORT}"

# --- OData services (standard SolMan Focused Build SALM services) ---------
SVC_BIZ_REQ = "/sap/opu/odata/salm/BUSINESS_REQUIREMENTS_SRV"   # PRIMARY: create/manage requirements
SVC_SOLDOC = "/sap/opu/odata/salm/soldoc_node_selection_srv"    # Solution Documentation tree
SVC_GENERIC = "/sap/opu/odata/salm/CRM_GENERIC_SRV"             # requirement search list / lookups

# The page the interactive sign-in is driven from. This is NOT the same as the
# endpoint used to verify the session afterwards, and the difference matters.
#
# Navigating to an OData `$metadata` URL does not reliably present the IAS login
# flow: the browser lands on something that never gives the user a usable sign-in,
# so the refresh sits until it times out while looking like it is working.
# Measured 2026-08-31: a refresh against the $metadata URL timed out repeatedly;
# the same session signed in immediately once the Fiori launchpad shell was opened
# by hand. The launchpad is a real UI and drives the SAML/IAS redirect properly.
#
# Same lesson as vsp's --sso-trigger-url: an authentication-gated URL is not
# automatically a usable login page. Verify with the API, log in through the UI.
LOGIN_URL = os.environ.get("SOLMAN_LOGIN_URL", "") or (
    f"{BASE_URL}/sap/bc/ui2/flp?sap-client={SAP_CLIENT}#Shell-home"
)
SVC_SERVICE = "/sap/opu/odata/salm/CRM_SERVICE_SRV"
SVC_DROP_DOC = "/sap/opu/odata/SALM/DROP_DOC_SRV"

# --- Focused Build config (varies per landscape; override via env) --------
REQUIREMENT_PROCESS_TYPE = os.environ.get("SOLMAN_REQ_PROCESS_TYPE", "S1BR")
DEFAULT_SCOPE = os.environ.get("SOLDOC_DEFAULT_SCOPE", "SAP_DEFAULT_SCOPE")

# Deployment defaults for create_requirement (all site-specific → from env).
DEFAULT_SOLUTION_ID = _req("SOLMAN_SOLUTION_ID")
DEFAULT_BRANCH_ID = _req("SOLMAN_BRANCH_ID")
DEFAULT_OWNER_BP = _req("SOLMAN_OWNER_BP")
DEFAULT_OWNER_NAME = _req("SOLMAN_OWNER_NAME")
DEFAULT_CATEGORY_ID = os.environ.get("SOLMAN_CATEGORY_ID", "")
DEFAULT_TEAM_NAME = _req("SOLMAN_TEAM_NAME")
DEFAULT_TEAM_BP = _req("SOLMAN_TEAM_BP")
DEFAULT_PLANNED_PROJECT = _req("SOLMAN_PLANNED_PROJECT")
DEFAULT_PLANNED_PROJECT_GUID = _req("SOLMAN_PLANNED_PROJECT_GUID")

# Work Package defaults (release/project targeting — site + release specific).
WP_PROJECT = os.environ.get("SOLMAN_WP_PROJECT", "")                    # e.g. MYPROJ_1.0_BUILD
WP_PROJECT_PHASE = os.environ.get("SOLMAN_WP_PROJECT_PHASE", "")        # project phase GUID
WP_RELEASE = os.environ.get("SOLMAN_WP_RELEASE", "")                    # RequestedRelease description
WP_RELEASE_COMPONENT = os.environ.get("SOLMAN_WP_RELEASE_COMPONENT", "")
WP_RELEASE_NUMBER = os.environ.get("SOLMAN_WP_RELEASE_NUMBER", "")
WP_DEV_TEAM_BP = os.environ.get("SOLMAN_WP_DEV_TEAM_BP", "")

# --- Local, out-of-repo state (kept in a dedicated home-dir folder) -------
STATE_DIR = Path(os.environ.get("SOLMAN_MCP_HOME", Path.home() / ".solman-mcp"))
COOKIE_FILE = Path(os.environ.get("SOLMAN_COOKIE_FILE", STATE_DIR / "cookies.txt"))
EDGE_PROFILE = Path(os.environ.get("SOLMAN_EDGE_PROFILE", STATE_DIR / "edge-profile"))
SESSION_COOKIE_PREFIX = "SAP_SESSIONID_"

# TLS. v2 verifies by DEFAULT.
#
# The previous line was `VERIFY_TLS = CA_BUNDLE if CA_BUNDLE else False`, so an
# unset SOLMAN_CA_BUNDLE silently disabled certificate checking on every call.
# An internal SolMan host usually presents a certificate from a corporate CA, so
# the fallback was 'solving' a trust problem by removing the check entirely.
# Note a CA bundle shipped by some other internal tool is unlikely to help: it is
# typically scoped to that tool's own endpoint, not to SolMan.
#
#   SOLMAN_CA_BUNDLE set   -> verify against that PEM
#   unset                  -> verify against the default trust store
#   SOLMAN_INSECURE_TLS=1  -> explicit, deliberate opt-out; nothing else disables it
CA_BUNDLE = os.environ.get("SOLMAN_CA_BUNDLE", "").strip()
_INSECURE = os.environ.get("SOLMAN_INSECURE_TLS", "").strip().lower() in {"1", "true", "yes"}


def _default_verify():
    """Verify using the WINDOWS certificate store, not certifi.

    Measured against a live internal SolMan host:

        stdlib ssl (loads the OS store)        -> VERIFIES OK
        certifi (Mozilla roots, httpx default) -> CERTIFICATE_VERIFY_FAILED

    The host's certificate chains to a corporate CA that the OS trusts and certifi
    has never heard of. So `verify=True` alone does NOT work here -- httpx would
    reject a certificate the machine considers perfectly valid, which is exactly
    the kind of failure that tempts someone back into verify=False.

    truststore hands httpx an SSLContext backed by the OS store, so this tracks
    whatever IT already manages centrally and there is no PEM in the repo to go
    stale. Falls back to certifi rather than to False: if this ever breaks it
    should fail loudly, never silently stop checking certificates.
    """
    try:
        import ssl
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        return True


# SOLMAN_CA_BUNDLE set   -> verify against that PEM
# SOLMAN_INSECURE_TLS=1  -> explicit, deliberate opt-out; nothing else disables it
# otherwise              -> verify against the OS trust store (see above)
VERIFY_TLS = CA_BUNDLE if CA_BUNDLE else (False if _INSECURE else _default_verify())


def require_host() -> None:
    """Raise a clear error if the target host isn't configured."""
    if not SAP_HOST:
        raise RuntimeError(
            "SOLMAN_HOST is not set. Copy .env.example to .env and fill in your system."
        )

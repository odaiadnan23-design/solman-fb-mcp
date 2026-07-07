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
    """Env value or a clear error at call time (not import time)."""
    return os.environ.get(name, "")


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

# TLS: internal hosts often use a corporate CA absent from Python's trust store.
# Point SOLMAN_CA_BUNDLE at the corporate root PEM to verify; else verification off.
CA_BUNDLE = os.environ.get("SOLMAN_CA_BUNDLE")
VERIFY_TLS: "bool | str" = CA_BUNDLE if CA_BUNDLE else False


def require_host() -> None:
    """Raise a clear error if the target host isn't configured."""
    if not SAP_HOST:
        raise RuntimeError(
            "SOLMAN_HOST is not set. Copy .env.example to .env and fill in your system."
        )

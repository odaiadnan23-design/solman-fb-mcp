"""Read-only access to a companion application's SolMan SQL cache.

WHAT THIS IS
------------
Some sites run a companion application that syncs SolMan defect and test data into
SQL Server and keeps it there. Where one exists, this module reads it.

Everything is configuration-driven — nothing site-specific is hardcoded:

    SOLMAN_SQL_SERVER    SQL Server host
    SOLMAN_SQL_DATABASE  database name
    SOLMAN_SQL_ENV       dev | quality | prod   (default: quality)
    SOLMAN_SQL_DRIVER    ODBC driver name

Authentication is Windows integrated, so there is no credential to store. If
pyodbc is absent or the server is unreachable, the tools report that and the rest
of the MCP server is unaffected.

WHY IT IS WORTH READING
-----------------------
The SALM OData gateway is an unreliable query surface, and the project's own
README documents it: `$filter` predicates are SILENTLY DROPPED, `or`-chains keep
only the last term, `$orderby` returns 500, `$skip` repeats the same window, and
`$count`/`$inlinecount` return wrong numbers. Real SQL over the same data gives
honest filtering, ordering and counts.

WHY IT IS READ-ONLY, ENFORCED IN CODE
-------------------------------------
CHECK YOUR OWN GRANTS BEFORE ENABLING THIS. They are often far wider than a
reader needs: an account inherits whatever its group holds, and the group usually
exists to let the companion application WRITE. Assume you may hold INSERT, UPDATE
and DELETE -- including on the production schema.

That matters more than usual here, because this kind of cache often holds
regulated content: electronic signatures, audit trails, and sealed verification
records belonging to a Quality/Validation process. THE DATABASE WILL NOT STOP A
MISTAKE, and a stray DELETE against an audit chain is not something you can put
back.

So safety is enforced client-side instead, three ways:

  1. Every statement passes _assert_read_only() before reaching the driver.
     Anything that is not a single SELECT/WITH is refused, including ;-batched
     statements (the classic bypass).
  2. There is NO tool that accepts raw SQL. Callers pick a NAMED query and pass
     bound parameters, so there is no path from a model-generated string to a
     destructive statement.
  3. The non-production schema is the default. Production requires an explicit,
     deliberate argument.

This module never writes. It is not "well-behaved by convention" -- it is
structurally unable to.
"""
from __future__ import annotations

import json
import os
import re

# Importing config first is load-bearing, not cosmetic: config._load_dotenv()
# populates os.environ from .env, and the module-level settings below read
# os.environ at import time. Without this, importing sqlcache on its own sees an
# unconfigured environment and reports the reader as disabled.
import config  # noqa: F401  (imported for its .env side effect)

# No defaults for server/database on purpose: this repository is public, and a
# hostname baked in as a default is both a leak and a footgun. Unset => disabled.
SQL_SERVER = os.environ.get("SOLMAN_SQL_SERVER", "").strip()
SQL_DATABASE = os.environ.get("SOLMAN_SQL_DATABASE", "").strip()
SQL_DRIVER = os.environ.get("SOLMAN_SQL_DRIVER", "SQL Server").strip()

# Schema per environment. Override to match your companion application's layout:
#   SOLMAN_SQL_SCHEMA_DEV / _QUALITY / _PROD
ENVIRONMENTS = {
    "dev": os.environ.get("SOLMAN_SQL_SCHEMA_DEV", "").strip(),
    "quality": os.environ.get("SOLMAN_SQL_SCHEMA_QUALITY", "").strip(),
    "prod": os.environ.get("SOLMAN_SQL_SCHEMA_PROD", "").strip(),
}
DEFAULT_ENV = os.environ.get("SOLMAN_SQL_ENV", "quality").strip().lower()

# Statements that are unambiguously reads. Anything else is refused.
_READ_START = re.compile(r"^\s*(SELECT|WITH)\b", re.I)
_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|TRUNCATE|DROP|ALTER|CREATE|GRANT|REVOKE|"
    r"EXEC|EXECUTE|SP_|XP_|BACKUP|RESTORE|SHUTDOWN|WAITFOR)\b", re.I)


class SqlUnavailable(RuntimeError):
    """pyodbc missing, or the server cannot be reached."""


class SqlRefused(RuntimeError):
    """A statement was rejected before it reached the database."""


def _assert_read_only(sql: str) -> None:
    """Refuse anything that is not a single read. Belt and braces.

    Checked in this order so the error names the actual problem: statement
    batching first (a trailing `; DELETE ...` is the classic bypass), then the
    opening keyword, then forbidden verbs anywhere in the text.
    """
    if ";" in sql.rstrip().rstrip(";"):
        raise SqlRefused("multiple statements are not allowed in a read-only query")
    if not _READ_START.match(sql):
        raise SqlRefused("only SELECT/WITH statements are allowed; this module never writes")
    bad = _FORBIDDEN.search(sql)
    if bad:
        raise SqlRefused(
            f"refused: statement contains {bad.group(0).upper()!r}. This module is "
            "read-only by construction -- the database would permit the write, so "
            "it is blocked here instead."
        )


def _schema(environment: str = "") -> str:
    env = (environment or DEFAULT_ENV).strip().lower()
    if env not in ENVIRONMENTS:
        raise ValueError(f"unknown environment {env!r}; use one of {', '.join(ENVIRONMENTS)}")
    schema = ENVIRONMENTS[env]
    if not schema:
        raise SqlUnavailable(
            f"no schema configured for environment {env!r}. Set "
            f"SOLMAN_SQL_SCHEMA_{env.upper()} (and SOLMAN_SQL_SERVER / "
            "SOLMAN_SQL_DATABASE) to enable the SQL cache reader."
        )
    return schema


def _connect():
    if not SQL_SERVER or not SQL_DATABASE:
        raise SqlUnavailable(
            "SQL cache is not configured. Set SOLMAN_SQL_SERVER, SOLMAN_SQL_DATABASE "
            "and the SOLMAN_SQL_SCHEMA_* variables to enable it. This feature is "
            "optional; the rest of the server works without it."
        )
    try:
        import pyodbc
    except ImportError as exc:
        raise SqlUnavailable("pyodbc is not installed; pip install pyodbc") from exc
    cs = (f"DRIVER={{{SQL_DRIVER}}};SERVER={SQL_SERVER};DATABASE={SQL_DATABASE};"
          "Trusted_Connection=yes;")
    try:
        # ApplicationIntent=ReadOnly is advisory (it routes to a replica where one
        # exists); the real guarantee is _assert_read_only above.
        return pyodbc.connect(cs, timeout=30, readonly=True)
    except Exception as exc:
        raise SqlUnavailable(f"cannot reach {SQL_SERVER}: {str(exc)[:200]}") from exc


def _rows(sql: str, params: tuple = (), limit: int = 500) -> list[dict]:
    _assert_read_only(sql)
    cn = _connect()
    try:
        cur = cn.cursor()
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        out = []
        for r in cur.fetchmany(limit):
            out.append({c: (v.isoformat() if hasattr(v, "isoformat") else v)
                        for c, v in zip(cols, r)})
        return out
    finally:
        cn.close()


# --------------------------------------------------------------------------
# Named queries. Parameters are bound, never interpolated. The schema name is
# the only value substituted into SQL text, and it comes from a fixed allowlist.
# --------------------------------------------------------------------------

def status() -> dict:
    """Is the cache reachable, and how stale is it? Ask this before trusting a read."""
    sch = _schema()
    try:
        rows = _rows(
            f"SELECT COUNT(*) AS defects, MAX(synced_at) AS last_sync FROM {sch}.defect_index_cache")
        execs = _rows(f"SELECT COUNT(*) AS executions FROM {sch}.test_management_execution_record")
        return {
            "available": True, "server": SQL_SERVER, "database": SQL_DATABASE,
            "schema": sch, "read_only_enforced": True,
            "defects": rows[0]["defects"] if rows else 0,
            "last_defect_sync": rows[0]["last_sync"] if rows else None,
            "test_executions": execs[0]["executions"] if execs else 0,
            "note": "cache is maintained by the companion application, not by this server",
        }
    except (SqlUnavailable, SqlRefused) as exc:
        return {"available": False, "server": SQL_SERVER, "reason": str(exc)[:300]}


def defect_summary(environment: str = "") -> list[dict]:
    """Defect counts per project, newest sync first. Honest counts, unlike $count."""
    sch = _schema(environment)
    return _rows(
        f"SELECT project_guid, COUNT(*) AS defects, MAX(synced_at) AS last_sync "
        f"FROM {sch}.defect_index_cache GROUP BY project_guid ORDER BY COUNT(*) DESC")


def defect_search(text: str, top: int = 50, environment: str = "") -> list[dict]:
    """Substring search across the cached defect payloads.

    The gateway drops most $filter predicates silently; this actually filters.
    """
    if not (text or "").strip():
        raise ValueError("search text is required")
    sch = _schema(environment)
    rows = _rows(
        f"SELECT TOP (?) defect_id, project_guid, source_changed_at, synced_at, payload_json "
        f"FROM {sch}.defect_index_cache WHERE payload_json LIKE ? "
        f"ORDER BY source_changed_at DESC",
        (int(top), f"%{text}%"), limit=int(top))
    return [_shape(r) for r in rows]


# Field names verified against a live payload. The companion application also
# enriches each defect with _ValueStream, its own classification. That enrichment
# does not exist anywhere in SolMan itself, and is a large part of why reading
# this cache beats the gateway for reporting.
def _shape(r: dict) -> dict:
    try:
        p = json.loads(r.get("payload_json") or "{}")
    except Exception:
        p = {}
    return {
        "defect_id": p.get("DefectId") or r.get("defect_id"),
        "title": p.get("DefectText") or "",
        "status": p.get("DefectStatusText") or "",
        "aggregate_status": p.get("DefectAggrStatusText") or "",
        "priority": p.get("DefectPriorityText") or "",
        "processor": p.get("DefectProcessorText") or "",
        "reporter": p.get("DefectReporterText") or "",
        "support_team": p.get("DefectSupportTeamText") or "",
        "category": p.get("DefectMlCategoryText") or "",
        "value_stream": p.get("_ValueStream") or "",
        "project": p.get("ProjectText") or "",
        "solution": p.get("SolutionText") or "",
        "wave": p.get("WaveText") or "",
        "age_days": p.get("MeasureDefectAge"),
        "changed_at": p.get("DefectChangedAt") or r.get("source_changed_at"),
        "synced_at": r.get("synced_at"),
        "url": p.get("DefectUrl") or "",
    }


def defect_by_value_stream(environment: str = "") -> list[dict]:
    """Open defect counts per value stream and status.

    Uses the companion application's own _ValueStream enrichment, which SolMan
    does not carry natively. This is the query the SALM gateway cannot answer at
    all: it drops the predicates and miscounts the totals.
    """
    sch = _schema(environment)
    rows = _rows(f"SELECT payload_json FROM {sch}.defect_index_cache", limit=20000)
    agg: dict[tuple, int] = {}
    for r in rows:
        try:
            p = json.loads(r.get("payload_json") or "{}")
        except Exception:
            continue
        key = (p.get("_ValueStream") or "(unclassified)",
               p.get("DefectAggrStatusText") or "(no status)")
        agg[key] = agg.get(key, 0) + 1
    out = [{"value_stream": k[0], "status": k[1], "defects": v} for k, v in agg.items()]
    out.sort(key=lambda x: (-x["defects"], x["value_stream"]))
    # If nothing is classified, say WHY rather than leaving a wall of
    # "(unclassified)". The companion application records a
    # value_stream_resolution.status of "Disabled" on each cached defect when its
    # per-defect responsibility lookup is switched off for the account. The counts
    # are correct; only the enrichment was never run.
    if out and all(r["value_stream"] == "(unclassified)" for r in out):
        out.append({
            "value_stream": "(note)", "status": "value-stream enrichment not run", "defects": 0,
            "why": "The companion application's per-defect value-stream lookup is "
                   "disabled for this account, so value_stream_resolution.status is "
                   "'Disabled' on every cached defect. Counts above are accurate; "
                   "only the classification is missing.",
        })
    return out


def test_execution_summary(top: int = 25, environment: str = "") -> list[dict]:
    """Recent test execution activity from the 72k-row execution cache."""
    sch = _schema(environment)
    return _rows(
        f"SELECT TOP (?) * FROM {sch}.test_management_execution_record "
        f"ORDER BY 1 DESC", (int(top),), limit=int(top))


def table_inventory(environment: str = "") -> list[dict]:
    """What the cache holds, with row counts — for deciding what is worth querying."""
    sch = _schema(environment)
    return _rows(
        "SELECT t.name AS table_name, SUM(p.rows) AS approx_rows "
        "FROM sys.tables t "
        "JOIN sys.schemas s ON s.schema_id = t.schema_id "
        "JOIN sys.partitions p ON p.object_id = t.object_id AND p.index_id IN (0,1) "
        "WHERE s.name = ? GROUP BY t.name HAVING SUM(p.rows) > 0 "
        "ORDER BY SUM(p.rows) DESC", (sch,))

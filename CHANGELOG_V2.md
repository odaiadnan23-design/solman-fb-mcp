# v2 — safety, multi-release targeting, and an optional SQL cache reader

Rebuilt on the v1 skeleton. 60 tools → **68**. Everything below was measured
against a live SolMan 7.2 Focused Build system, not inferred.

Site-specific values (hostnames, solution ids, release names) are deliberately
absent — this repository is public. Everything here is configuration-driven.

---

## Defects fixed

### 1. TLS verification could be silently off

```python
VERIFY_TLS = CA_BUNDLE if CA_BUNDLE else False    # v1
```

An unset `SOLMAN_CA_BUNDLE` disabled certificate checking on **every** call.

The obvious fix — `verify=True` — does not work on a corporate network, and it
is worth knowing why:

| Trust source | Result |
|---|---|
| stdlib `ssl` (loads the **OS** certificate store) | verifies |
| **certifi** (Mozilla roots — what **httpx** uses by default) | **fails** |

An internal SolMan host typically presents a certificate from a corporate CA that
the OS trusts and certifi has never heard of. So `verify=True` rejects a
certificate the machine considers perfectly valid — which is exactly the
frustration that tempts people back to `verify=False`.

v2 uses `truststore` to give httpx an SSLContext backed by the **OS trust store**,
so it tracks whatever your IT already manages and there is no PEM in the repo to
go stale. It falls back to certifi, never to `False`: if this breaks it should
fail loudly, not quietly stop checking certificates.

`SOLMAN_INSECURE_TLS=1` is now the only way to disable verification, and it has
to be set deliberately.

### 2. `_req()` did not do what its docstring said

It promised *"a clear error at call time"*. It returned `""`.

That is the mechanism behind the wrong-target class of bug: a missing solution id
became an empty string and the write proceeded anyway. v2 adds `require()`, which
raises with a message naming the missing value and how to find it, plus
`validate_env()` for a health report that distinguishes `reads_ready` from
`writes_ready` and lists what is missing.

Import-time behaviour is unchanged on purpose — read-only tools must not fail to
import over values they never touch.

### 3. Unpinned dependencies broke the build

`requirements.txt` said `mcp>=1.27`. pip resolved **mcp 2.x**, where `FastMCP` was
renamed to `MCPServer`, and the server stopped importing. v2 targets the 2.x API
and pins every major range.

---

## Wrong-target safety

A SolMan system commonly hosts **several solutions** — for example one per
landscape. v1 pinned a single `SOLMAN_SOLUTION_ID` in `.env` and used it as a
silent default for every write.

That is a quiet failure: filing against the wrong solution does not error, it just
puts the work somewhere nobody is looking. It is easy to hit when the same
requirement legitimately exists in more than one landscape.

v2 **fails closed** — a blank solution raises instead of defaulting — and any
write can name its target explicitly.

## Multiple concurrent releases

Where an organisation runs several releases at once, "the planned project" does
not exist. v1's single `SOLMAN_PLANNED_PROJECT` recreated the same trap in a
second dimension: work silently filed against last release.

v2 leaves it **blank by design** and resolves releases by name using the same
rules as solutions — exact id > exact name > unique substring — and **raises on
ambiguity** rather than picking one:

```
resolve_project("REL_13.0_14.0")   -> exact match
resolve_project("REL_5.0")         -> raises, lists the candidates
```

New tools: `list_projects`, `resolve_project`.

Worth knowing: a requirement's `PlannedProject` field stores the project
**description**, not its id. The resolver matches either.

---

## New: optional read-only SQL cache reader (`sqlcache.py`)

Some sites run a companion application that syncs SolMan defect and test data
into SQL Server. Where one exists, reading it directly is far more reliable than
querying the gateway.

**Why.** This project's own README documents that the SALM gateway silently drops
`$filter` predicates, keeps only the last term of an `or`-chain, returns 500 on
`$orderby`, repeats the same window on `$skip`, and reports wrong `$count`. Real
SQL over the same data gives honest filtering, ordering and counts.

**Why it is read-only, enforced in code.** Check your own grants before enabling
this. They may be far wider than a reader needs — a service account's group
membership can carry `INSERT`/`UPDATE`/`DELETE` on schemas holding electronic
signatures and audit chains. **Assume the database will not stop a mistake**, and
enforce it client-side:

1. `_assert_read_only()` refuses anything that is not a single `SELECT`/`WITH`,
   including `;`-batched statements — verified against several bypass shapes.
2. **No tool accepts raw SQL.** Named queries with bound parameters only, so there
   is no path from a model-generated string to a destructive statement.
3. The non-production schema is the default; production requires an explicit
   argument.

Configure with `SOLMAN_SQL_SERVER`, `SOLMAN_SQL_DATABASE`, `SOLMAN_SQL_ENV`.
Windows integrated auth, so there is no credential to store. If `pyodbc` is
absent or the server is unreachable, the tools report that and the rest of the
server is unaffected.

New tools: `sql_cache_status`, `sql_defect_search`, `sql_defect_summary`,
`sql_defect_by_value_stream`, `sql_table_inventory`.

`sql_cache_status` reports **staleness** — the cache is maintained by that other
application, not by this server, so it can lag. Ask before trusting a read.

### Scope boundary: do not write what another system owns

If a companion application owns defect and test records — particularly where it
seals them for a regulated process with signatures and audit chains — **this
server should read that data and not write it.** v1 listed type-specific create
flows for Defect / RfC / Risk as "not built yet"; where such an application
exists, they should stay unbuilt. A second writer into a validated, audit-chained
space is a validation problem, not a missing feature.

v2 authors the Requirement → Work Package → Work Item chain, which such tools
typically do not touch, and reads the rest.

---

## New: OData bridge reuse

`refresh_session.py` already writes cookies in **Netscape format**, so a generic
OData-to-MCP bridge (e.g. `oisee/odata_mcp_go`) can consume the same cookie file
with **zero additional authentication** — point it at a service URL with
`--cookie-file` and `--ro`.

`CRM_GENERIC_SRV` alone exposes on the order of a hundred entity sets, far more
than the 68 tools shape. Transport and ATC-result entity sets are among them,
which is the natural link between a Work Item and the ABAP objects it produced.

Caveat: the Gateway **catalog service may not be activated**, in which case
services cannot be enumerated the usual way and must be reached by known name.

---

## Field notes

- **ADT may be closed on a SolMan host** while OData is open — they are gated on
  different authorisations. Do not conclude a system is unreachable from an ADT
  probe. (Separately: an ADT root returning `ExceptionResourceNotFound` is
  *normal* even on healthy systems.)
- **Check which port carries TLS.** A SolMan host may answer plain HTTP on another
  port, where a session cookie would travel in clear. Do not use it.
- **A SAML-protected host can return the login page at HTTP 200**, so a `200`
  proves nothing. `refresh_session.py` already gets this right: it polls until the
  OData metadata actually returns `edmx`, rather than trusting a status code or
  the presence of a cookie.
- If a companion application performs its own enrichment (value-stream
  classification and similar), that data may be **empty by configuration rather
  than by fault**. `sql_defect_by_value_stream` surfaces the recorded reason
  instead of showing a wall of `(unclassified)`.
- Where a **GxP / regulated flag** exists as a requirement category, v2 defaults to
  the **non-regulated** value deliberately. Marking something regulated should be
  a conscious act, never inherited from a config file.

## Session handling

`session_keepalive` addresses the v1 README's *"Future: a periodic keepalive ping"*.
The client already auto-reloads the cookie when `refresh_session.py` rewrites it
(it watches the file mtime), so the only remaining friction was the server-side
idle timer. One cheap authenticated GET resets it. It cannot create a session —
`refresh_session.py` remains the out-of-band way back in.

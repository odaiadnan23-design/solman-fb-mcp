# v3 — Customizing Scout

Cross-system configuration comparison, plus a fix for two tools that could wedge
the server. 68 tools → **73**. Everything below was measured against live
systems, not inferred.

Site-specific values (hostnames, system names, solution ids) are deliberately
absent — this repository is public. Everything here is configuration-driven.

---

## New: Customizing Scout

SAP Solution Manager ships transaction `SCOUT` (Customizing Scout, SV-SMG-IMP),
which compares customizing between systems connected to SolMan by RFC and set up
for Customizing Distribution. `scout.py` does the same job over a different road:
it reads table contents from each system through `vsp query`, which uses the
standard ADT data-preview API.

That means **no RFC destination, no Customizing Distribution setup, and no
SolMan-side authorization** — and it works on systems SolMan has never been told
about. SCOUT is a classic ABAP/Web Dynpro tool and publishes no OData service, so
the SALM services this server otherwise speaks cannot reach it; going at the
managed systems directly is the shorter path, not a workaround.

| Tool | What it does |
|---|---|
| `scout_systems` | Systems Scout may read, and whether each has a cached SSO session |
| `scout_table_catalog` | Known customizing tables by area (FI, CO, AA, TAX, BANK, CROSS) |
| `scout_read` | Read one configuration table from one system |
| `scout_compare` | Diff one table between two systems, field by field |
| `scout_compare_area` | Sweep an area to find *where* two systems drifted |

### Rows are matched on the real primary key

Read from `DD03L`, not guessed. `MANDT` is dropped: each system is read in its
own client, and keeping it would make every row differ for the wrong reason.

If the key turns out not to be unique in the result, the comparison **refuses**
rather than silently overwriting colliding rows — a diff that quietly drops rows
is worse than no diff.

### A truncated comparison is never reported as agreement

This one was caught by testing rather than by reading the code, and it is the
most important behaviour in the module.

An area sweep with a 300-row cap reported `T093` as **`differs=False`** — no
differences. Re-run with a cap of 4000, the same table shows **452 rows vs 479,
15 differing and 42 present only on one side.** The first answer was not a small
error; it was a clean bill of health for a table that had drifted badly.

Two causes, both fixed:

1. **No `ORDER BY`.** Two systems that both hit the cap can return *different*
   rows, so the diff reports differences that are artifacts of row order. Both
   sides are now ordered by the key.
2. **Truncation read as a conclusion.** `compare()` now returns `conclusive`,
   and a truncated result carries an explicit `INCONCLUSIVE` warning.
   `compare_area()` counts those separately from `identical` — an unfinished
   check is never totalled as a clean one.

### Safety

- **Read-only by construction.** `_run` is the only exec point and permits only
  `vsp query`, which has no write mode. Write-capable subcommands are refused by
  name. No code path here can change anything in any system.
- **Fails closed on an allowlist.** `SCOUT_SYSTEMS` names the systems Scout may
  touch; unset, every call refuses. This is stronger than blocking known
  production SIDs: production cannot be reached by forgetting to add it to a
  denylist, only by deliberately typing it into the allowlist. It also keeps
  site-specific names out of this public file.
- **Person-level tables refused by default** (`PA0*`, `ADRC`, `KNA1`, `LFA1`,
  `BUT000` …). They are not configuration, and a diff copies them somewhere new.
  `SCOUT_ALLOW_PERSONAL_DATA=1` lifts it deliberately — a default, not a lock.
- **Never waits on a browser.** See the caveat below.

### The browser caveat

When a cached SSO session goes stale, vsp attempts a silent refresh; if that
needs a human it opens a browser window and waits five minutes. Two things that
look like they would prevent this **do not**, both verified against vsp v2.54.0:

- `--sso-on-expiry error` is a *server-mode* flag. CLI subcommands reject it
  outright: `Error: unknown flag: --sso-on-expiry`.
- The `on_expiry` key in `.vsp.json` is ignored. A system with `on_expiry:
  "error"` set still opened a browser window.

So Scout cannot stop the window from opening. What it does instead is refuse to
wait: `SCOUT_TIMEOUT` (default 120s) kills the child, and vsp's browser marker on
stderr becomes an error naming the fix. Mint sessions out of band with
`vsp sso refresh -s <sid>` and this never arises.

---

## Fixed: two tools recursed until the call wedged

`session_status` and `session_keepalive` never returned. A call sat for 30
minutes and was killed by the client as a hung server.

```python
from client import SessionExpired, SolmanError, session_status   # the helper

@mcp.tool()
def session_status() -> str:        # ...shadowed by the tool
    return _wrap(session_status)    # so this hands the tool to itself
```

The module-level `def` shadows the import, so `_wrap(session_status)` calls the
tool, which calls `_wrap(session_status)`, until the recursion limit. The failure
presents as a **hang, not an error**, which is why it survived: nothing logs, and
the tool simply never answers.

Fixed by importing the helper under an alias. The bug is inherited from v1;
v2 propagated it into `session_keepalive` as well. Both now return in seconds.

**Regression guard.** `test_no_mcp_tool_calls_itself` parses `server.py` and
fails if any `@mcp.tool` function references the name of any tool — catching both
direct self-recursion and the shadowed-import form. Verified to fire on the
pre-fix file and pass on the fixed one; a guard only tested against fixed code
proves nothing.

---

## Tests

`test_units.py`: 19 → **28**, all offline. The new ones cover the vsp output
parser (including the `N rows` footer and short-row padding), the person-data
refusal, the fail-closed allowlist, the exec whitelist, and catalog well-formedness.

# Claude Code skills for solman-fb

Two skills that carry the operational knowledge for driving this MCP server: how to write to
Focused Build without losing work, and how to audit a release.

| Skill | Use it for |
|---|---|
| `solman-focused-build` | Creating or editing requirements, work packages and work items. The order of operations, the writes that silently do not persist, the lifecycle gates. |
| `solman-release-audit` | Auditing a planned project end to end. Includes a runnable script and the twelve checks. |

## Install

Copy or link them into your user skills directory. Claude Code picks them up on the next
session; `/skills` lists what is loaded.

**Windows** (a junction keeps them in sync with `git pull`):

```bat
mklink /J "%USERPROFILE%\.claude\skills\solman-focused-build" "<repo>\skills\solman-focused-build"
mklink /J "%USERPROFILE%\.claude\skills\solman-release-audit"  "<repo>\skills\solman-release-audit"
```

**macOS / Linux:**

```bash
ln -s "<repo>/skills/solman-focused-build" ~/.claude/skills/solman-focused-build
ln -s "<repo>/skills/solman-release-audit"  ~/.claude/skills/solman-release-audit
```

To copy instead of link, `xcopy /E /I` or `cp -r` the folders — you then re-copy after each
pull.

The audit script finds the server modules by walking up three directories from itself, so it
works in place. Running it from elsewhere, set `SOLMAN_MCP_DIR` to the repo root.

## Scope

**These skills contain no site-specific values** — no hostnames, solution or planned-project
GUIDs, release names, process codes, object IDs or people. That is deliberate: this repository
is public, and the same exclusion that keeps the local site-notes file out of it applies
here.

Your organisation's conventions — which solution and planned project, title format, required
custom fields, the expected element scope, local composition rules — belong in a **separate
conventions skill kept inside your own network**, which these two reference but do not
contain. Keep it as a sibling skill with its own `SKILL.md` and share it internally.

## Adding to these

Both skills are written as measured behaviour, not documentation summary. If you add
something, say what you observed and on what date, and prefer "this returns 204 and does
nothing" over "this may not work". A skill that hedges gets ignored; a skill that states a
measured fact gets trusted.

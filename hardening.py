"""Connection hardening for the SolMan MCP: session self-healing, a read-only
kill switch, a local write journal, and cookie-file hygiene.

Everything here is deliberately dependency-free (stdlib only) and fails *open for
reads, closed for writes*: a problem with the journal or the lock must never stop
an audit, but must never let a write go through unrecorded either.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import config


class ReadOnlyBlocked(RuntimeError):
    """A write was attempted while SOLMAN_READONLY is set."""


# --------------------------------------------------------------------------
# Read-only kill switch
# --------------------------------------------------------------------------
# Function imports that only READ. Everything else routed through
# SolmanClient.function() is treated as a write, because several SALM 'get_*'
# imports actually execute a PPF action (documented in client.function()).
READ_ONLY_FUNCTIONS = frozenset({
    "getSoldocTree", "Get_Sub_Elements", "SYSTEM_LOGON_TYPES",
    "GetOneOrderMessageLog", "TRANSPORT_REQUEST_URL", "TRREQ_SHORT_DESCRIPTION",
    "get_WP_to_WI_Classif", "GetTransportRelatedChecks", "GetOpenJobTransportChecks",
})


def read_only() -> bool:
    return os.environ.get("SOLMAN_READONLY", "").strip().lower() in {"1", "true", "yes"}


def guard_write(op: str, target: str) -> None:
    """Raise if the server is in read-only mode. Call before any mutating request."""
    if read_only():
        raise ReadOnlyBlocked(
            f"{op} {target} blocked: SOLMAN_READONLY is set, so this server is running "
            "read-only. Unset it (and restart the session) to allow writes."
        )


# --------------------------------------------------------------------------
# Write journal
# --------------------------------------------------------------------------
JOURNAL = Path(os.environ.get("SOLMAN_JOURNAL",
                              str(config.STATE_DIR / "writes.jsonl")))
_MAX_FIELD = 200


def _summarize(body: object) -> object:
    """Shrink a payload for the journal: keep shape and short values, trim long text."""
    if isinstance(body, dict):
        out = {}
        for k, v in body.items():
            if isinstance(v, (list, tuple)):
                out[k] = f"<{len(v)} items>"
            elif isinstance(v, dict):
                out[k] = _summarize(v)
            elif isinstance(v, str) and len(v) > _MAX_FIELD:
                out[k] = v[:_MAX_FIELD] + f"...(+{len(v)-_MAX_FIELD} chars)"
            else:
                out[k] = v
        return out
    if isinstance(body, (list, tuple)):
        return f"<{len(body)} items>"
    return body


def journal(op: str, service: str, target: str, body: object = None,
            status: int | None = None, error: str | None = None) -> None:
    """Append one line describing a write. Never raises."""
    try:
        JOURNAL.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "user": os.environ.get("USERNAME") or os.environ.get("USER") or "",
            "host": config.SAP_HOST, "client": config.SAP_CLIENT,
            "op": op, "service": service.rsplit("/", 1)[-1], "target": target,
        }
        if status is not None:
            rec["status"] = status
        if error:
            rec["error"] = error[:400]
        if body is not None:
            rec["body"] = _summarize(body)
        with JOURNAL.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 - journalling must never break a call
        pass


def recent_writes(limit: int = 20) -> list[dict]:
    """Last N journal entries, newest last. For 'what did I just change?'."""
    try:
        lines = JOURNAL.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


# --------------------------------------------------------------------------
# Session self-healing
# --------------------------------------------------------------------------
_LOCK = Path(os.environ.get("SOLMAN_REFRESH_LOCK",
                            str(config.STATE_DIR / "refresh.lock")))
_LOCK_STALE_SECONDS = 300
_REFRESH_COOLDOWN = 60.0
_last_refresh_attempt = 0.0


def auto_refresh_enabled() -> bool:
    """On by default; set SOLMAN_AUTO_REFRESH=0 to require a manual refresh."""
    return os.environ.get("SOLMAN_AUTO_REFRESH", "1").strip().lower() not in {"0", "false", "no"}


def _acquire_lock() -> bool:
    """Single-writer lock so parallel calls cannot stampede the Edge profile."""
    try:
        _LOCK.parent.mkdir(parents=True, exist_ok=True)
        if _LOCK.exists():
            age = time.time() - _LOCK.stat().st_mtime
            if age < _LOCK_STALE_SECONDS:
                return False
            _LOCK.unlink(missing_ok=True)   # stale: a previous run died
        fd = os.open(str(_LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True
    except FileExistsError:
        return False
    except OSError:
        return False


def _release_lock() -> None:
    try:
        _LOCK.unlink(missing_ok=True)
    except OSError:
        pass


def refresh_session(timeout: int = 90) -> dict:
    """Re-mint the session cookie by running refresh_session.py headless.

    Returns {"refreshed": bool, "reason": str}. Never raises: the caller is
    already handling a SessionExpired and needs a verdict, not a second fault.

    Headless only. The persistent Edge profile keeps the IAS session warm, so a
    silent refresh normally succeeds; if it does not, that genuinely needs a
    human at a browser and the caller is told to run the script by hand.
    """
    global _last_refresh_attempt
    now = time.time()
    if now - _last_refresh_attempt < _REFRESH_COOLDOWN:
        return {"refreshed": False,
                "reason": f"a refresh was attempted {now - _last_refresh_attempt:.0f}s ago; "
                          "not retrying inside the cooldown"}
    if not _acquire_lock():
        return {"refreshed": False, "reason": "another refresh is already running"}
    _last_refresh_attempt = now
    script = Path(__file__).with_name("refresh_session.py")
    before = _cookie_mtime()
    attempts = []
    cleared = _clear_stale_profile_lock()
    if cleared:
        attempts.append("cleared stale profile lock: " + ", ".join(cleared))
    try:
        # Headless first (silent). Measured 16-Sep-2026: headless exited in 3s
        # with "gracefully close end" while a headed run against the same warm
        # IAS session completes silently every time — so fall back to headed with
        # a bounded window rather than giving up. A window flashing briefly is
        # the price of a run that does not die half way.
        for mode, extra in (("headless", ["--headless"]), ("headed", [])):
            proc = subprocess.run(
                [sys.executable, str(script), *extra, "--timeout", str(timeout)],
                cwd=str(script.parent), capture_output=True, text=True,
                timeout=timeout + 60,
            )
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()
            attempts.append(f"{mode}: exit {proc.returncode}" + (f" [{tail[-1][:120]}]" if tail else ""))
            if _cookie_mtime() > before:
                return {"refreshed": True, "mode": mode, "attempts": attempts,
                        "reason": "cookie rewritten"}
        return {"refreshed": False, "attempts": attempts,
                "reason": "neither headless nor headed refresh produced a new cookie; "
                          "run it by hand: python refresh_session.py --timeout 240"}
    except subprocess.TimeoutExpired:
        return {"refreshed": False, "attempts": attempts,
                "reason": f"refresh timed out after {timeout + 60}s"}
    except Exception as ex:  # noqa: BLE001
        return {"refreshed": False, "attempts": attempts, "reason": f"{type(ex).__name__}: {ex}"}
    finally:
        _release_lock()


def _profile_in_use() -> bool:
    """True if any Edge/Chromium process is running with the MCP's user-data-dir."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | "
             "Where-Object { $_.CommandLine -like '*" + str(config.EDGE_PROFILE).replace("\\", "*") + "*' }).Count"],
            capture_output=True, text=True, timeout=20)
        return int((out.stdout or "0").strip() or 0) > 0
    except Exception:  # noqa: BLE001 - if we cannot tell, do not delete anything
        return True


def _clear_stale_profile_lock() -> list[str]:
    """Remove Chromium's singleton lock files when no browser holds the profile.

    Measured 16-Sep-2026: after an Edge instance died, refresh_session.py failed in
    three seconds with "Failed to create a ProcessSingleton for your profile directory"
    on every attempt, headless and headed, with NO msedge process alive — a stale
    lockfile. Every refresh would have failed forever without this.
    """
    if os.name != "nt" or _profile_in_use():
        return []
    removed = []
    for name in ("lockfile", "SingletonLock", "SingletonCookie", "SingletonSocket"):
        p = Path(config.EDGE_PROFILE) / name
        try:
            if p.exists() or p.is_symlink():
                p.unlink()
                removed.append(name)
        except OSError:
            pass
    return removed


def _cookie_mtime() -> float:
    try:
        return config.COOKIE_FILE.stat().st_mtime
    except OSError:
        return 0.0


# --------------------------------------------------------------------------
# Cookie hygiene
# --------------------------------------------------------------------------
def cookie_health() -> dict:
    """Age and permissions of the session cookie file."""
    p = config.COOKIE_FILE
    try:
        st = p.stat()
    except OSError:
        return {"present": False, "path": str(p),
                "hint": "run: python refresh_session.py"}
    age = time.time() - st.st_mtime
    info = {"present": True, "path": str(p),
            "age_minutes": round(age / 60, 1),
            "mode": oct(st.st_mode & 0o777)}
    if os.name == "posix" and (st.st_mode & 0o077):
        info["warning"] = "cookie file is readable by other users — tighten to 0600"
    if os.name == "nt":
        info["acl"] = _windows_acl_summary(p)
    return info


def _windows_acl_summary(path: Path) -> str:
    """Who can read the cookie, per icacls. Best-effort, never raises.

    icacls puts the first trustee on the same line as the path and the rest on
    their own lines, so parse every "<trustee>:(<perms>)" occurrence rather than
    assuming one per line.
    """
    try:
        r = subprocess.run(["icacls", str(path)], capture_output=True, text=True, timeout=15)
        text = r.stdout.replace(str(path), " ")
        trustees = re.findall(r"([A-Za-z0-9 _.\\-]+):\(", text)
        trustees = [t.strip() for t in trustees if t.strip()]
        if not trustees:
            return "unknown (could not parse icacls output)"
        broad = [t for t in trustees
                 if any(w in t.upper() for w in ("EVERYONE", "AUTHENTICATED USERS",
                                                 "BUILTIN\\USERS", "INTERACTIVE", "JEDER"))]
        if broad:
            return "BROAD ACCESS: " + ", ".join(sorted(set(broad))) + " — run harden_cookie()"
        return "restricted to: " + ", ".join(sorted(set(trustees)))
    except Exception:  # noqa: BLE001
        return "unknown (icacls unavailable)"


def harden_cookie() -> dict:
    """Restrict the cookie file (and state dir) to the current user only."""
    p = config.COOKIE_FILE
    if not p.exists():
        return {"changed": False, "reason": "no cookie file"}
    try:
        if os.name == "nt":
            user = os.environ.get("USERNAME", "")
            domain = os.environ.get("USERDOMAIN", "")
            who = f"{domain}\\{user}" if domain and user else user
            if not who:
                return {"changed": False, "reason": "cannot determine current user"}
            # The directory grant MUST carry (OI)(CI) so children — the Edge profile
            # above all — inherit it. The first version granted the bare directory
            # only and removed inheritance: every child lost its ACEs, Edge could no
            # longer create its profile lock, and every session refresh failed with
            # "ProcessSingleton" for hours. Measured 16-Sep-2026.
            subprocess.run(["icacls", str(config.STATE_DIR), "/inheritance:r",
                            "/grant:r", f"{who}:(OI)(CI)F", "/T", "/Q"],
                           capture_output=True, text=True, timeout=60)
            subprocess.run(["icacls", str(p), "/inheritance:r", "/grant:r", f"{who}:(F)"],
                           capture_output=True, text=True, timeout=20)
            return {"changed": True, "granted_to": who, "acl": _windows_acl_summary(p),
                    "profile_accessible": os.access(str(config.EDGE_PROFILE), os.R_OK | os.W_OK)}
        os.chmod(p, 0o600)
        os.chmod(config.STATE_DIR, 0o700)
        return {"changed": True, "mode": "0600"}
    except Exception as ex:  # noqa: BLE001
        return {"changed": False, "reason": f"{type(ex).__name__}: {ex}"}

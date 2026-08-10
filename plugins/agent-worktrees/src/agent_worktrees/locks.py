#!/usr/bin/env python3
"""Provable-liveness lock files -- the shared primitive of the session-state
lattice (#4272).

The worktree picker wants to read every session-state layer's liveness as a
cheap **filesystem** check instead of a subprocess or socket poke: the Copilot
session lock (`inuse.<pid>.lock`, already authoritative), a per-worktree
**mux-lock** (`mux.<id>.lock`), and a per-session **bridge-lock**
(`bridge.<session>.lock`). This module is the common mechanism all three build
on -- a small JSON lock file whose liveness is *provable* so a crash (no clean
teardown) leaves a **detectably stale** lock rather than a false "alive".

Liveness proof: **PID + process start-time**. Presence of the file is not
liveness -- a reader confirms the recorded pid is still alive AND its process
start-time still matches the recorded one, which defeats PID reuse (a new,
unrelated process that happens to inherit the dead owner's pid has a different
start-time, so the lock reads stale). This needs no long-lived handle held open
by the owner, so it works for third-party owners (the mux server) as well as our
own processes -- the durable, broadly-applicable choice over an advisory
``flock`` a live owner must keep held.

Leaf module: no intra-package imports, so every layer (sessions, reclaim,
picker, doctor) can depend on it without a cycle. Everything is best-effort and
never raises on I/O or process-query failure; an unreadable/torn lock reads as
absent, and an unprovable owner (alive pid, unreadable start-time) is treated as
live (fail-open -- never hide a session that might be real).
"""
from __future__ import annotations

import json
import os
import platform
import tempfile
import time
from pathlib import Path

# Bumped only on a breaking change to the on-disk shape; readers tolerate an
# absent/unknown version (treat as legacy best-effort) so a rollout is safe.
LOCK_SCHEMA = 1


# ---------------------------------------------------------------------------
# Process liveness + identity (cross-platform, dependency-free)
# ---------------------------------------------------------------------------

def _pid_alive(pid: int) -> bool:
    """True when a process with ``pid`` currently exists. Best-effort."""
    if pid <= 0:
        return False
    if platform.system() == "Windows":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
            return True
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def pid_alive(pid: int) -> bool:
    """Public alias of :func:`_pid_alive` -- "does this pid exist right now?".

    Exposed for callers outside this module that need a **direct** liveness
    probe rather than a lock read (e.g. the launcher-shell reaper asking whether
    a candidate's parent terminal is still running). Deliberately fail-open: an
    unprovable pid reads as alive, so a caller using this as a safety veto
    over-spares rather than over-kills.
    """
    return _pid_alive(pid)


def process_start_time(pid: int) -> str | None:
    """A stable, per-process **start-time identity token** for ``pid``, or None.

    The token only needs to be *stable for the life of the process* and *differ*
    when the pid is later reused -- it is compared for equality, never
    interpreted as a wall-clock. Returns None when the process is gone or its
    start-time can't be read (caller treats None as "can't disprove liveness").

    * Windows -- the process creation ``FILETIME`` (100 ns ticks since 1601) via
      ``GetProcessTimes``.
    * POSIX -- field 22 (``starttime``, clock ticks since boot) of
      ``/proc/<pid>/stat``.
    """
    if pid <= 0:
        return None
    if platform.system() == "Windows":
        return _start_time_windows(pid)
    return _start_time_posix(pid)


def _start_time_windows(pid: int) -> str | None:
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    k32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    k32.GetProcessTimes.restype = wintypes.BOOL

    handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        creation = wintypes.FILETIME()
        exit_ = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        ok = k32.GetProcessTimes(
            handle, ctypes.byref(creation), ctypes.byref(exit_),
            ctypes.byref(kernel), ctypes.byref(user),
        )
        if not ok:
            return None
        ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        return str(ticks)
    except OSError:
        return None
    finally:
        k32.CloseHandle(handle)


def _start_time_posix(pid: int) -> str | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(errors="ignore")
    except OSError:
        return None
    # comm (field 2) may contain spaces/parens -- split on the LAST ')'.
    rparen = stat.rfind(")")
    if rparen == -1:
        return None
    rest = stat[rparen + 1:].split()
    # After comm, `rest` holds fields from state (field 3) onward, so starttime
    # (field 22) is rest[19]: state=0, ppid=1, ... starttime=19.
    if len(rest) < 20:
        return None
    token = rest[19]
    return token if token.isdigit() else None


# ---------------------------------------------------------------------------
# Lock file read / write / liveness / removal
# ---------------------------------------------------------------------------

def write_lock(
    path: Path | str, *, pid: int | None = None, extra: dict | None = None,
) -> bool:
    """Atomically write a provable-liveness lock file. Returns success.

    Records the owner ``pid`` (default: this process), its ``start_time`` token,
    a ``created_at`` epoch, and any ``extra`` fields (e.g. ``worktree_id``,
    ``session_id``). Written to a temp file in the same directory then
    ``os.replace``d, so a reader never sees a torn file. Best-effort: returns
    False (never raises) if the directory can't be created or the write fails.
    """
    p = Path(path)
    owner = os.getpid() if pid is None else pid
    payload: dict = {
        "schema": LOCK_SCHEMA,
        "pid": owner,
        "start_time": process_start_time(owner),
        "created_at": time.time(),
    }
    if extra:
        payload.update(extra)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            prefix=p.name + ".", suffix=".tmp", dir=str(p.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.replace(tmp, str(p))
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return False
        return True
    except OSError:
        return False


def read_lock(path: Path | str) -> dict | None:
    """Parse a lock file's JSON payload, or None when absent/torn/unreadable."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def lock_is_live(data: dict | None) -> bool:
    """Whether a parsed lock payload denotes a **currently live** owner.

    Live iff the recorded pid is still alive AND (its start-time is unknown, or
    still matches the recorded token). A pid-reuse -- an unrelated process now
    holding the dead owner's pid -- has a *different* start-time, so the lock
    reads **stale**. Fail-open on an unprovable-but-alive owner (recorded or
    current start-time unavailable): a live pid with no usable start-time is
    treated as live, so a real session is never hidden by a missing token.
    """
    if not isinstance(data, dict):
        return False
    pid = data.get("pid")
    if not isinstance(pid, int) or not _pid_alive(pid):
        return False
    recorded = data.get("start_time")
    if not recorded:
        return True  # no reuse guard recorded -- can't disprove, assume live
    current = process_start_time(pid)
    if current is None:
        return True  # alive pid, start-time unreadable -- can't disprove
    return str(recorded) == str(current)


def lock_live(path: Path | str) -> bool:
    """Convenience: read ``path`` and report whether its owner is live."""
    return lock_is_live(read_lock(path))


def remove_lock(path: Path | str) -> None:
    """Remove a lock file if present. Best-effort; never raises."""
    try:
        Path(path).unlink()
    except OSError:
        pass

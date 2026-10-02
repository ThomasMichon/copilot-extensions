"""Whether a Copilot session's process is running: live, dead, or unknown.

Decisions that act on a session being gone (taking over a worktree's head,
resuming its conversation in a new process) need proof, not a failed probe:
an unreadable lock directory, an access-denied process query, or a platform
without a process-identity probe is ``unknown``, never ``dead``.
"""

from __future__ import annotations

import errno
import os
import platform
from pathlib import Path
from typing import Literal

from . import sessions

Liveness = Literal["live", "dead", "unknown"]

_ERROR_INVALID_PARAMETER = 87  # OpenProcess on a PID that doesn't exist


def copilot_pid_state(pid: int) -> bool | None:
    """True: a Copilot process holds ``pid``; False: provably not (no such
    process, or another program reused the PID); None: can't tell here."""
    if platform.system() == "Windows":
        import ctypes
        from ctypes import wintypes

        kernel32 = sessions._get_kernel32()
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False if ctypes.get_last_error() == _ERROR_INVALID_PARAMETER else None
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buf))
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return None
            return "copilot" in Path(buf.value).name.lower()
        finally:
            kernel32.CloseHandle(handle)
    if Path("/proc/self/cmdline").exists():
        try:
            return b"copilot" in Path(f"/proc/{pid}/cmdline").read_bytes()
        except FileNotFoundError:
            return False
        except OSError:
            return None
    # No process-identity probe (e.g. macOS): only "no such process" is proof.
    try:
        os.kill(pid, 0)
    except OSError as exc:
        return False if exc.errno == errno.ESRCH else None
    return None


def session_liveness(session_id: str | None) -> Liveness:
    """``live`` when a Copilot holds one of the session's ``inuse.<pid>.lock``
    files, ``dead`` when its conversation is on this machine and every lock
    is provably stale (or there are none), else ``unknown``."""
    if not session_id:
        return "unknown"
    try:
        sdir = sessions._session_state_dir() / session_id
        if not sdir.is_dir() or sessions._is_detached_session(sdir):
            return "unknown"
        # scandir, not glob: glob can swallow an unreadable directory and
        # return nothing, which would read as "no live lock".
        with os.scandir(sdir) as entries:
            locks = [Path(e.path) for e in entries
                     if e.name.startswith("inuse.") and e.name.endswith(".lock")]
    except OSError:
        return "unknown"
    unknown = False
    for lock in locks:
        try:
            pid = int(lock.stem.split(".")[1])
        except (IndexError, ValueError):
            unknown = True
            continue
        state = copilot_pid_state(pid)
        if state is True:
            return "live"
        if state is None:
            unknown = True
    return "unknown" if unknown else "dead"

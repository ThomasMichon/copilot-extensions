"""Whether a Copilot session's process is running: live, dead, or unknown.

Decisions that act on a session being gone (taking over a worktree's head,
resuming its conversation in a new process) need proof, not a failed probe:
an unreadable lock directory, an access-denied process query, or a platform
without a process-identity probe is ``unknown``, never ``dead``.
"""

from __future__ import annotations

import errno
import logging
import os
import platform
import stat
from pathlib import Path
from typing import Literal
from uuid import UUID

from . import locks, sessions

Liveness = Literal["live", "dead", "unknown"]
NATIVE_BINDING_PROOF = "inuse-hold-v1"
_MAX_BINDING_DESCRIPTORS = 1024

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
    except (OSError, ValueError) as exc:
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


def session_process_owns_hold(session_id: str, pid: int, start_time: str) -> bool:
    """Strict positive UUID/process proof, not a replacement death detector.

    Native Copilot holds ``inuse.<pid>.hold`` open for its session.
    Match the OS-observed file owner and process birth, not a stale pathname.
    Unsupported, inaccessible or over-budget observations remain unconfirmed.
    """
    if (
        not isinstance(session_id, str) or str(UUID(session_id)) != session_id
        or type(pid) is not int or pid <= 0
        or not isinstance(start_time, str) or not start_time
    ):
        raise ValueError("Invalid native session process identity")
    system = platform.system()
    if system not in {"Linux", "Windows"}:
        return False
    try:
        directory = sessions._session_state_dir() / session_id
        if not stat.S_ISDIR(directory.lstat().st_mode):
            return False
        hold = directory / f"inuse.{pid}.hold"
        expected = hold.lstat()
        if not stat.S_ISREG(expected.st_mode) or locks.process_start_time(pid) != start_time:
            return False
        identity = (expected.st_dev, expected.st_ino)
        if system == "Windows":
            from .windows_session_hold import owners

            confirmed = (pid, start_time) in owners(hold)
            fresh = hold.lstat()
            return (
                confirmed and stat.S_ISREG(fresh.st_mode)
                and (fresh.st_dev, fresh.st_ino) == identity
                and locks.process_start_time(pid) == start_time
            )
        with os.scandir(f"/proc/{pid}/fd") as descriptors:
            for index, descriptor in enumerate(descriptors):
                if index >= _MAX_BINDING_DESCRIPTORS:
                    logging.getLogger(__name__).warning(
                        "Native session ownership probe exceeded its descriptor budget."
                    )
                    return False
                try:
                    opened = descriptor.stat()
                except FileNotFoundError:
                    continue
                if (opened.st_dev, opened.st_ino) != identity:
                    continue
                # DirEntry.stat caches: re-query the descriptor itself to fence
                # closure/reuse while checking the native ownership pathname.
                fresh_descriptor = os.stat(descriptor.path)
                fresh_hold = hold.lstat()
                return (
                    stat.S_ISREG(fresh_hold.st_mode)
                    and (fresh_descriptor.st_dev, fresh_descriptor.st_ino) == identity
                    and (fresh_hold.st_dev, fresh_hold.st_ino) == identity
                    and locks.process_start_time(pid) == start_time
                )
    except FileNotFoundError:
        return False
    except OSError as exc:
        logging.getLogger(__name__).warning(
            "Native session ownership could not be corroborated: %s", exc,
        )
    return False

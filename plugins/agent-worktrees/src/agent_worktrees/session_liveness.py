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


def _darwin_copilot_pid_state(pid: int) -> bool | None:
    """Read one PID's bounded KERN_PROCARGS2 payload, excluding environment data."""
    import ctypes
    import posixpath
    import sys

    try:
        query = ctypes.CDLL(None, use_errno=True).sysctl
        query.argtypes = [
            ctypes.POINTER(ctypes.c_int), ctypes.c_uint, ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t,
        ]
        query.restype = ctypes.c_int
        mib = (ctypes.c_int * 3)(1, 49, pid)  # CTL_KERN, KERN_PROCARGS2, exact PID
        size = ctypes.c_size_t()
        if query(mib, 3, None, ctypes.byref(size), None, 0) != 0 or not 4 < size.value <= 1 << 20:
            return None
        buffer = ctypes.create_string_buffer(size.value)
        if query(mib, 3, buffer, ctypes.byref(size), None, 0) != 0 or not 4 < size.value <= len(buffer):
            return None
        payload = buffer.raw[:size.value]
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    argc = int.from_bytes(payload[:4], sys.byteorder, signed=True)
    if not 0 < argc <= 4096:
        return None
    executable, separator, remaining = payload[4:].partition(b"\0")
    if not executable or not separator:
        return None
    remaining = remaining.lstrip(b"\0")
    arguments: list[bytes] = []
    for _ in range(argc):
        argument, separator, remaining = remaining.partition(b"\0")
        if not separator:
            return None
        arguments.append(argument)
    executable_name = executable.rsplit(b"/", 1)[-1].lower()
    if executable_name == b"copilot":
        return True
    if executable_name not in {b"node", b"nodejs"}:
        return False
    if len(arguments) < 2 or not arguments[1] or arguments[1].startswith(b"-"):
        return None
    entrypoint = os.fsdecode(arguments[1])
    if not posixpath.isabs(entrypoint):
        return None
    try:
        resolved = posixpath.normpath(os.path.realpath(entrypoint, strict=True))
    except (OSError, ValueError):
        return None
    return resolved.split("/")[-3:] == ["@github", "copilot", "npm-loader.js"]


def copilot_pid_state(pid: int) -> bool | None:
    """True: a Copilot process holds ``pid``; False: provably not (no such
    process, or another program reused the PID); None: can't tell here."""
    if platform.system() == "Windows":
        import ctypes
        from ctypes import wintypes

        kernel32 = sessions._get_kernel32()
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False if getattr(ctypes, "get_last_error")() == _ERROR_INVALID_PARAMETER else None
        try:
            query_exit = kernel32.GetExitCodeProcess
            query_exit.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            query_exit.restype = wintypes.BOOL
            code = wintypes.DWORD()
            if not query_exit(handle, ctypes.byref(code)):
                return None
            if code.value != 259:  # STILL_ACTIVE
                return False
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(buf))
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return None
            return "copilot" in Path(buf.value).name.lower()
        finally:
            kernel32.CloseHandle(handle)
    if platform.system() == "Darwin":
        observed = _darwin_copilot_pid_state(pid)
        if observed is not None:
            return observed
    elif Path("/proc/self/cmdline").exists():
        try:
            return b"copilot" in Path(f"/proc/{pid}/cmdline").read_bytes()
        except FileNotFoundError:
            return False
        except OSError:
            return None
    # Unavailable process identity: only "no such process" is proof.
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


def session_process_owns_hold(session_id: str, pid: int, start_time: str) -> bool:
    """Strict positive UUID/process proof, not a replacement death detector.

    Linux/WSL native Copilot holds ``inuse.<pid>.hold`` open for its session.
    Match the actual open-file inode and process birth, not a stale pathname.
    Unsupported, inaccessible or over-budget observations remain unconfirmed.
    """
    return session_process_hold_state(session_id, pid, start_time) == "held"


def session_process_hold_state(
    session_id: str, pid: int, start_time: str,
) -> Literal["held", "absent", "unknown"]:
    """Keep inconclusive native file inspection distinct from proven absence."""
    if (
        not isinstance(session_id, str) or str(UUID(session_id)) != session_id
        or type(pid) is not int or pid <= 0
        or not isinstance(start_time, str) or not start_time
    ):
        raise ValueError("Invalid native session process identity")
    if platform.system() != "Linux":
        return "unknown"
    try:
        directory = sessions._session_state_dir() / session_id
        if not stat.S_ISDIR(directory.lstat().st_mode):
            return "unknown"
        hold = directory / f"inuse.{pid}.hold"
        expected = hold.lstat()
        if not stat.S_ISREG(expected.st_mode) or locks.process_start_time(pid) != start_time:
            return "unknown"
        identity = (expected.st_dev, expected.st_ino)
        with os.scandir(f"/proc/{pid}/fd") as descriptors:
            for index, descriptor in enumerate(descriptors):
                if index >= _MAX_BINDING_DESCRIPTORS:
                    logging.getLogger(__name__).warning(
                        "Native session ownership probe exceeded its descriptor budget."
                    )
                    return "unknown"
                try:
                    opened = descriptor.stat()
                except FileNotFoundError:
                    continue
                if (opened.st_dev, opened.st_ino) != identity:
                    continue
                # DirEntry.stat caches: re-query the descriptor itself to fence
                # closure/reuse while checking the native ownership pathname.
                try:
                    fresh_descriptor = os.stat(descriptor.path)
                    fresh_hold = hold.lstat()
                except FileNotFoundError:
                    return "unknown"
                return "held" if (
                    stat.S_ISREG(fresh_hold.st_mode)
                    and (fresh_descriptor.st_dev, fresh_descriptor.st_ino) == identity
                    and (fresh_hold.st_dev, fresh_hold.st_ino) == identity
                    and locks.process_start_time(pid) == start_time
                ) else "unknown"
        return "absent"
    except FileNotFoundError:
        return "absent"
    except OSError as exc:
        logging.getLogger(__name__).warning(
            "Native session ownership could not be corroborated: %s", exc,
        )
    return "unknown"

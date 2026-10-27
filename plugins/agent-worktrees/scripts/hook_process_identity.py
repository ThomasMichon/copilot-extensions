"""Standalone OS birth tokens for hook ownership checks, without runtime imports.

The OS implementation matches agent_worktrees.locks; unavailable tokens never
establish ownership. This module must remain importable with isolated Python.
"""

from __future__ import annotations

import platform
from pathlib import Path


def process_start_time(pid: int) -> str | None:
    """A stable, per-process **start-time identity token** for ``pid``, or None.

    The token only needs to be *stable for the life of the process* and *differ*
    when the pid is later reused -- it is compared for equality, never
    interpreted as a wall-clock. Returns None when the process is gone or its
    start-time can't be read; callers must not establish ownership from None.

    * Windows -- the process creation ``FILETIME`` (100 ns ticks since 1601) via
      ``GetProcessTimes``.
    * macOS -- microsecond birth time from ``proc_pidinfo(PROC_PIDTBSDINFO)``.
    * Linux -- field 22 (``starttime``, clock ticks since boot) of
      ``/proc/<pid>/stat``.
    """
    if pid <= 0:
        return None
    if platform.system() == "Windows":
        return _start_time_windows(pid)
    if platform.system() == "Darwin":
        return _start_time_darwin(pid)
    return _start_time_posix(pid)


def _start_time_darwin(pid: int) -> str | None:
    import ctypes

    # Public proc_bsdinfo ABI from XNU bsd/sys/proc_info.h.
    class BsdInfo(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_uint32) for name in (
                "flags", "status", "exit_status", "pid", "parent_pid",
                "uid", "gid", "real_uid", "real_gid", "saved_uid", "saved_gid", "reserved",
            )
        ] + [
            ("command", ctypes.c_char * 16), ("name", ctypes.c_char * 32),
            ("files", ctypes.c_uint32), ("group", ctypes.c_uint32),
            ("job_count", ctypes.c_uint32), ("tty_device", ctypes.c_uint32),
            ("tty_group", ctypes.c_uint32), ("nice", ctypes.c_int32),
            ("start_seconds", ctypes.c_uint64), ("start_microseconds", ctypes.c_uint64),
        ]

    try:
        query = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True).proc_pidinfo
        query.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
        query.restype = ctypes.c_int
        info = BsdInfo()
        size = ctypes.sizeof(info)
        if query(pid, 3, 0, ctypes.byref(info), size) != size:
            return None
    except (OSError, AttributeError):
        return None
    if info.pid != pid or not info.start_seconds or info.start_microseconds >= 1_000_000:
        return None
    return f"{info.start_seconds}:{info.start_microseconds}"


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

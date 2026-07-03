"""OS helpers for the Session-Host spike: liveness + per-OS survival adapters.

Two survival mechanisms, one per platform, matching the effort's design:

* **POSIX** -- the Session Host is spawned with ``start_new_session=True`` so it
  leads its own session/process-group; a front's process-group teardown never
  reaches it, and because the *host* owns the child's pipes the child does not
  get EOF when the front dies.
* **Windows** -- the crux. agent-bridge arms a ``JOB_OBJECT_LIMIT_KILL_ON_JOB_
  CLOSE`` job on itself (``winjob.py``); any child it spawns dies when it exits.
  For the Session Host to *survive* the front, (a) the front's job must permit
  breakaway (``JOB_OBJECT_LIMIT_BREAKAWAY_OK``) and (b) the host must be spawned
  with ``CREATE_BREAKAWAY_FROM_JOB`` and then place itself in its *own*
  kill-on-close job. This module provides those primitives so the spike can
  prove the survival (and demonstrate the negative control where breakaway is
  not permitted).
"""

from __future__ import annotations

import os
import sys

IS_WIN = sys.platform == "win32"

# Windows creation flags
CREATE_NO_WINDOW = 0x08000000
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NEW_PROCESS_GROUP = 0x00000200

# Job object limit flags
_JOB_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JOB_LIMIT_BREAKAWAY_OK = 0x0800
_JobObjectExtendedLimitInformation = 9
_STILL_ACTIVE = 259


def pid_alive(pid: int | None) -> bool:
    """Cross-platform: is ``pid`` a currently-running process?"""
    if not pid:
        return False
    if IS_WIN:
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.restype = wintypes.HANDLE
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False
        try:
            code = wintypes.DWORD()
            k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            if not k.GetExitCodeProcess(h, ctypes.byref(code)):
                return False
            return code.value == _STILL_ACTIVE
        finally:
            k.CloseHandle(h)
    else:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True


def _make_extended_limit(limit_flags: int):
    import ctypes
    from ctypes import wintypes

    ulong_ptr = ctypes.c_size_t

    class _BasicLimit(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ulong_ptr),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
        )]

    class _ExtendedLimit(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimit),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    info = _ExtendedLimit()
    info.BasicLimitInformation.LimitFlags = limit_flags
    return info


def arm_self_job(*, kill_on_close: bool = True, breakaway_ok: bool = False) -> int | None:
    """Place THIS process in a fresh Job Object (Windows only).

    Models agent-bridge's ``winjob.setup_kill_on_close_job`` but lets the spike
    toggle ``JOB_OBJECT_LIMIT_BREAKAWAY_OK`` -- the flag that decides whether a
    child (the Session Host) is *allowed* to escape the front's job. Returns the
    job handle (int) held for the process lifetime, or None on failure/non-Win.
    """
    if not IS_WIN:
        return None
    import ctypes
    from ctypes import wintypes

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateJobObjectW.restype = wintypes.HANDLE
    k.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    k.GetCurrentProcess.restype = wintypes.HANDLE
    k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
    ]

    flags = 0
    if kill_on_close:
        flags |= _JOB_LIMIT_KILL_ON_JOB_CLOSE
    if breakaway_ok:
        flags |= _JOB_LIMIT_BREAKAWAY_OK

    job = k.CreateJobObjectW(None, None)
    if not job:
        return None
    info = _make_extended_limit(flags)
    if not k.SetInformationJobObject(
        job, _JobObjectExtendedLimitInformation,
        ctypes.byref(info), ctypes.sizeof(info),
    ):
        k.CloseHandle(job)
        return None
    if not k.AssignProcessToJobObject(job, k.GetCurrentProcess()):
        k.CloseHandle(job)
        return None
    # Deliberately leaked: the handle is held for the process lifetime.
    return int(job)


def child_creationflags(*, breakaway: bool) -> int:
    """Creation flags for spawning a child on Windows."""
    if not IS_WIN:
        return 0
    flags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
    if breakaway:
        flags |= CREATE_BREAKAWAY_FROM_JOB
    return flags

"""Windows Job Object so spawned agent children die with the daemon (#90).

The bridge spawns agent transports as separate processes (e.g. an
``agent-codespaces ssh --stdio`` tree: ``cmd.exe -> python -> ssh``). On a
*graceful* session stop the bridge already tree-kills them (``taskkill /T``).
But if the daemon dies **without** running teardown -- a crash, ``taskkill`` of
the daemon PID, or a botched ``copilot plugin update`` -- those children are
orphaned and keep running for days, pinning directories open (see #89/#90).

The fix: at daemon startup, place the daemon process in a Job Object configured
with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` and hold the job handle for the
process's lifetime. Children inherit the job automatically (we never set
``CREATE_BREAKAWAY_FROM_JOB``). When the daemon exits for *any* reason its last
handle to the job closes, and Windows terminates every process still in the job
-- so no orphans survive the daemon. Nested jobs are allowed on Windows 8+, so
this works even when a parent (e.g. Task Scheduler) already placed us in a job.

No-op on non-Windows; POSIX teardown relies on the existing process-group kill.
"""

from __future__ import annotations

import ctypes
import logging
import sys

log = logging.getLogger("agent-bridge")

# Kept open for the daemon's lifetime -- closing it would trigger the kill.
_job_handle: int | None = None

_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_JobObjectExtendedLimitInformation = 9


def _build_structs():
    from ctypes import wintypes

    ulong_ptr = ctypes.c_size_t  # ULONG_PTR is pointer-sized

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
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _ExtendedLimit(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimit),
            ("IoInfo", _IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    return _ExtendedLimit


def setup_kill_on_close_job() -> bool:
    """Place this process in a kill-on-close Job Object. Returns True on success.

    Idempotent and best-effort: a failure (e.g. an OS that disallows the
    assignment) is logged and ignored -- the daemon still runs, it just loses
    the orphan-prevention safety net.
    """
    global _job_handle
    if sys.platform != "win32" or _job_handle is not None:
        return False

    try:
        from ctypes import wintypes

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateJobObjectW.restype = wintypes.HANDLE
        k.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        k.GetCurrentProcess.restype = wintypes.HANDLE
        k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
        ]
        k.CloseHandle.argtypes = [wintypes.HANDLE]

        job = k.CreateJobObjectW(None, None)
        if not job:
            log.warning("CreateJobObject failed (err=%d)", ctypes.get_last_error())
            return False

        extended = _build_structs()()
        extended.BasicLimitInformation.LimitFlags = (
            _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        if not k.SetInformationJobObject(
            job, _JobObjectExtendedLimitInformation,
            ctypes.byref(extended), ctypes.sizeof(extended),
        ):
            log.warning(
                "SetInformationJobObject failed (err=%d)", ctypes.get_last_error()
            )
            k.CloseHandle(job)
            return False

        if not k.AssignProcessToJobObject(job, k.GetCurrentProcess()):
            log.warning(
                "AssignProcessToJobObject(self) failed (err=%d) -- "
                "orphan-prevention job not armed", ctypes.get_last_error(),
            )
            k.CloseHandle(job)
            return False

        _job_handle = job  # deliberately leaked: held for the process lifetime
        log.info(
            "Process job object armed -- spawned children die with the daemon"
        )
        return True
    except Exception:
        log.warning("Failed to set up kill-on-close job object", exc_info=True)
        return False

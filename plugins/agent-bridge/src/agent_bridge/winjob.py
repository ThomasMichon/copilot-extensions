"""Windows Job Object so spawned agent children die with the daemon (#90).

The bridge spawns agent transports as separate processes (e.g. an
``agent-codespaces ssh --stdio`` tree: ``cmd.exe -> python -> ssh``). On a
*graceful* session stop the bridge already tree-kills them (``taskkill /T``).
But if the daemon dies **without** running teardown -- a crash, ``taskkill`` of
the daemon PID, or a botched ``copilot plugin update`` -- those children are
orphaned and keep running for days, pinning directories open (see #89/#90).

The fix: at daemon startup, place the daemon process in a Job Object configured
with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` and hold the job handle for the
process's lifetime. Children inherit the job automatically. When the daemon
exits for *any* reason its last handle to the job closes, and Windows terminates
every process still in the job -- so no orphans survive the daemon. Nested jobs
are allowed on Windows 8+, so this works even when a parent (e.g. Task Scheduler)
already placed us in a job.

**Session-Host breakaway (effort agent-bridge-version-mux, #1759).** The daemon
job additionally carries ``JOB_OBJECT_LIMIT_BREAKAWAY_OK``. That flag is *inert*
on its own -- children still inherit the job and still die with the daemon --
but it is the precondition that lets a **Session Host** child be spawned with
``CREATE_BREAKAWAY_FROM_JOB`` so it can *escape* the daemon's kill-on-close job
and survive an agent-bridge restart (the Phase-0 spike proved that without
``BREAKAWAY_OK`` the escape is denied and the child dies with the front). The
Session Host then arms its *own* kill-on-close job so its child dies with the
*host*, not the front. Adding the flag here changes nothing for existing
children (none set ``CREATE_BREAKAWAY_FROM_JOB``); it only unlocks the survivor.

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
_JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x0800
_JobObjectExtendedLimitInformation = 9

# Spawn-time flag a caller uses to place a child *outside* the current job
# (used by the Session Host launcher to escape the front's kill-on-close job).
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def _kill_on_close_limit_flags(allow_breakaway: bool) -> int:
    """The Job Object ``LimitFlags`` for the daemon/host kill-on-close job.

    Factored out so the flag composition is unit-testable without arming a real
    job on the test process.
    """
    flags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if allow_breakaway:
        flags |= _JOB_OBJECT_LIMIT_BREAKAWAY_OK
    return flags


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


def setup_kill_on_close_job(allow_breakaway: bool = True) -> bool:
    """Place this process in a kill-on-close Job Object. Returns True on success.

    Idempotent and best-effort: a failure (e.g. an OS that disallows the
    assignment) is logged and ignored -- the daemon still runs, it just loses
    the orphan-prevention safety net.

    ``allow_breakaway`` (default True) adds ``JOB_OBJECT_LIMIT_BREAKAWAY_OK`` so a
    Session Host child may later escape this job via ``CREATE_BREAKAWAY_FROM_JOB``
    and survive an agent-bridge restart. The flag is inert for every existing
    child (none request breakaway), so it does not weaken orphan-prevention.
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

        limit_flags = _kill_on_close_limit_flags(allow_breakaway)
        extended = _build_structs()()
        extended.BasicLimitInformation.LimitFlags = limit_flags
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

"""Windows-headless / detached process-spawn helpers (shared, vendored per plugin).

A copilot-extensions runtime frequently runs **windowless**: under a hidden
Windows Scheduled Task launched via ``conhost --headless``, inside the
agent-bridge daemon, or from a session-start hook whose parent has no console of
its own. When such a windowless parent shells out to a *console* program -- a
``.cmd`` binstub that re-execs ``python.exe``, or ``git.exe`` / ``ssh.exe`` /
``pwsh.exe`` -- Windows allocates a fresh console **window** for the child, which
flashes on screen once per spawn.

These helpers standardize the fix across every plugin so the behavior (and the
exact creation flags) can't drift between copies:

* :func:`no_window_kwargs` -- ``CREATE_NO_WINDOW`` for a non-interactive child
  whose output is still captured over pipes (the common case: git/gh/az checks).
* :func:`detached_kwargs` -- fully detach a background child so it outlives its
  parent AND carries no console (so no window); optionally break away from a
  parent Job object.
* :func:`windowless_daemon_kwargs` -- keep a Windows daemon in a
  ``CREATE_NO_WINDOW`` host while optionally breaking away from an inherited
  Job object; use this when its own children must inherit the windowless host.
* :func:`windowless_python` -- select the safest available ``pythonw.exe`` for a
  detached Python daemon on Windows so neither a venv launcher nor its base
  interpreter can allocate a second console.
* :func:`windowless_python_env` -- preserve the original venv context when
  ``windowless_python`` targets a base install's ``pythonw.exe``.
* :func:`spawn_daemon` -- the one launch primitive for a long-lived daemon:
  windowless, escapes the caller's Job object / session, runs from an
  explicit durable ``cwd``, and appends its stdout/stderr to a log file.

Both are no-ops off Windows (``no_window_kwargs`` -> ``{}``; ``detached_kwargs``
-> ``start_new_session=True``), so call sites stay platform-agnostic::

    subprocess.run(cmd, capture_output=True, **no_window_kwargs())
    subprocess.Popen(
        [windowless_python(), "-m", "my_daemon"],
        **detached_kwargs(breakaway=True),
    )

Most consumers reference this canonical source via a `uv`-editable
pointer in dev, materialized into a real per-plugin copy at release; at
least one consumer (`agent-worktrees`, per its own self-contained
build-surface requirement) ships a real local copy in dev too -- see
this package's own ``README.md`` § Vendoring for the mechanism, and
``tools/check-vendored-libs-sync.py`` for the enforcement.
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import logging
import math
import os
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from typing import Any, BinaryIO

# Win32 process-creation flags. Read from ``subprocess`` when present (Windows)
# and fall back to the stable ABI literals so this module imports cleanly off
# Windows too. CREATE_BREAKAWAY_FROM_JOB has no ``subprocess`` alias.
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
_DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
_CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000
_CREATE_SUSPENDED = 0x00000004
_CONTAINED_TEST_ENV = "COPILOT_EXTENSIONS_TEST_CONTAINED"
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
# Permit (but do not require) a contained descendant to opt out of the
# kill-on-close job via its own CREATE_BREAKAWAY_FROM_JOB creation flag.
# Without this, Windows rejects that child spawn outright -- a real hazard
# for a command like `agent-worktrees update`, whose own daemon cutover
# (``status_monitor_cutover.py``, via ``windowless_daemon_kwargs(breakaway=
# True)``) deliberately spawns its successor with CREATE_BREAKAWAY_FROM_JOB
# so the new daemon outlives the updater. Ordinary descendants that never
# request breakaway stay contained and still die with the job as before.
_JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x0800
_JobObjectExtendedLimitInformation = 9
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001
_PROCESS_SUSPEND_RESUME = 0x0800
_ERROR_ACCESS_DENIED = 5

log = logging.getLogger("agent-procutil")

__all__ = [
    "contained_test_mode",
    "no_window_flags",
    "no_window_kwargs",
    "JobHandle",
    "spawn_in_kill_on_close_job",
    "spawn_sync_in_kill_on_close_job",
    "detached_kwargs",
    "windowless_daemon_kwargs",
    "windowless_python",
    "windowless_python_env",
    "spawn_daemon",
    "DaemonSpawnError",
]


def contained_test_mode() -> bool:
    """Whether the process is running beneath the repository test supervisor."""
    return os.environ.get(_CONTAINED_TEST_ENV) == "1"


def _is_windows() -> bool:
    return os.name == "nt"


class JobHandle:
    """Owned Windows Job Object handle.

    Keep an instance alive as long as its assigned child process should be
    tied to this owner. Closing it releases the handle; with
    ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` that also terminates any process
    still assigned to the job. Off Windows callers never receive one.
    """

    def __init__(self, handle: Any) -> None:
        self._handle = handle

    @property
    def handle(self) -> Any:
        return self._handle

    def close(self) -> None:
        handle = self._handle
        if not handle:
            return
        self._handle = None
        try:
            _kernel32().CloseHandle(handle)
        except Exception:
            log.debug("CloseHandle(job) failed", exc_info=True)

    def terminate_and_wait(self, timeout_seconds: float) -> None:
        """Terminate this owned Job, confirm zero active processes, then close.

        Native failures and timeout retain the handle so the caller can retry.
        An already-closed handle cannot attest that the Job is empty.
        """
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be finite and positive")
        handle = self._handle
        if not handle:
            raise RuntimeError("Job handle is closed; cannot confirm empty Job")
        kernel32 = _kernel32()
        _configure_job_teardown_api(kernel32)
        deadline = time.monotonic() + timeout_seconds
        if not kernel32.TerminateJobObject(handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())
        while True:
            accounting = _JobBasicAccountingInformation()
            returned = ctypes.c_uint32()
            if not kernel32.QueryInformationJobObject(
                handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting),
                ctypes.byref(returned),
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            if returned.value != ctypes.sizeof(accounting):
                raise RuntimeError("Job accounting returned an unexpected structure size")
            if accounting.ActiveProcesses == 0:
                if not kernel32.CloseHandle(handle):
                    raise ctypes.WinError(ctypes.get_last_error())
                self._handle = None
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Job still has active processes after termination timeout")
            time.sleep(min(0.05, remaining))

    def __enter__(self) -> "JobHandle":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()


def _kernel32() -> Any:
    return ctypes.WinDLL("kernel32", use_last_error=True)


class _JobBasicAccountingInformation(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_int64),
        ("TotalKernelTime", ctypes.c_int64),
        ("ThisPeriodTotalUserTime", ctypes.c_int64),
        ("ThisPeriodTotalKernelTime", ctypes.c_int64),
        ("TotalPageFaultCount", ctypes.c_uint32),
        ("TotalProcesses", ctypes.c_uint32),
        ("ActiveProcesses", ctypes.c_uint32),
        ("TotalTerminatedProcesses", ctypes.c_uint32),
    ]


def _configure_job_teardown_api(kernel32: Any) -> None:
    from ctypes import wintypes

    kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.QueryInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL


def _ntdll() -> Any:
    return ctypes.WinDLL("ntdll", use_last_error=True)


def _build_job_extended_limit_info() -> type[ctypes.Structure]:
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


def _configure_job_api(kernel32: Any) -> None:
    from ctypes import wintypes

    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def _configure_resume_api(kernel32: Any, ntdll: Any) -> None:
    from ctypes import wintypes

    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    ntdll.NtResumeProcess.restype = ctypes.c_long


def _log_job_failure(operation: str) -> None:
    error = ctypes.get_last_error()
    if error == _ERROR_ACCESS_DENIED:
        log.debug("%s failed with access denied; child job binding skipped", operation)
    else:
        log.debug("%s failed with Windows error %d; child job binding skipped", operation, error)


def _assign_suspended_to_kill_on_close_job(
    pid: int, *, allow_breakaway: bool = True,
) -> JobHandle | None:
    """Assign a newly-created suspended ``pid`` to this owner's kill-on-close Job.

    The returned :class:`JobHandle` owns the Job Object handle. Hold it for the
    child process lifetime; close it once ordinary cleanup has finished.
    Private to :func:`spawn_in_kill_on_close_job`: using a bare PID is safe only
    while the child is still suspended and cannot have exited or spawned
    descendants before assignment.
    """
    if not _is_windows():
        return None
    if pid <= 0:
        log.debug("invalid pid %r; child job binding skipped", pid)
        return None

    job = None
    process = None
    try:
        kernel32 = _kernel32()
        _configure_job_api(kernel32)

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            _log_job_failure("CreateJobObjectW")
            return None

        limit_info = _build_job_extended_limit_info()()
        limit_info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if allow_breakaway:
            limit_info.BasicLimitInformation.LimitFlags |= _JOB_OBJECT_LIMIT_BREAKAWAY_OK
        if not kernel32.SetInformationJobObject(
            job,
            _JobObjectExtendedLimitInformation,
            ctypes.byref(limit_info),
            ctypes.sizeof(limit_info),
        ):
            _log_job_failure("SetInformationJobObject")
            return None

        process = kernel32.OpenProcess(
            _PROCESS_SET_QUOTA | _PROCESS_TERMINATE,
            False,
            int(pid),
        )
        if not process:
            _log_job_failure("OpenProcess")
            return None

        if not kernel32.AssignProcessToJobObject(job, process):
            _log_job_failure("AssignProcessToJobObject")
            return None

        owned = JobHandle(job)
        job = None
        return owned
    except Exception:
        log.debug("failed to bind pid %s to a kill-on-close job", pid, exc_info=True)
        return None
    finally:
        try:
            if process:
                _kernel32().CloseHandle(process)
        except Exception:
            log.debug("CloseHandle(process) failed", exc_info=True)
        try:
            if job:
                _kernel32().CloseHandle(job)
        except Exception:
            log.debug("CloseHandle(job) failed", exc_info=True)


def _resume_suspended_process(pid: int) -> bool:
    if not _is_windows():
        return True
    process = None
    try:
        kernel32 = _kernel32()
        ntdll = _ntdll()
        _configure_resume_api(kernel32, ntdll)
        process = kernel32.OpenProcess(_PROCESS_SUSPEND_RESUME, False, int(pid))
        if not process:
            _log_job_failure("OpenProcess(PROCESS_SUSPEND_RESUME)")
            return False
        status = ntdll.NtResumeProcess(process)
        if status != 0:
            log.debug("NtResumeProcess failed with NTSTATUS %#x", status)
            return False
        return True
    except Exception:
        log.debug("failed to resume suspended pid %s", pid, exc_info=True)
        return False
    finally:
        try:
            if process:
                _kernel32().CloseHandle(process)
        except Exception:
            log.debug("CloseHandle(process) failed", exc_info=True)


async def spawn_in_kill_on_close_job(
    *args: str,
    **kwargs: Any,
) -> tuple[asyncio.subprocess.Process, JobHandle | None]:
    """Spawn a child already contained by a kill-on-close Job Object.

    On Windows the child is created with ``CREATE_SUSPENDED``, assigned to a
    kill-on-close job, and then resumed, with no ``await`` between creation and
    assignment: the child can neither run nor start descendants outside the job.
    One window remains by design: if the owner is hard-killed in the instant
    between ``CreateProcess`` returning and the assignment, the child stays
    suspended (it never runs, so it opens no connection). Closing that window
    would need a hand-rolled ``CreateProcessW`` with ``PROC_THREAD_ATTRIBUTE_JOB_LIST``,
    which ``asyncio``'s subprocess transport (pipes, overlapped I/O) can't use.
    If job creation or assignment fails, the child is still resumed and returned
    with ``None`` so existing cleanup paths remain in charge. Off Windows this is
    a plain ``asyncio.create_subprocess_exec`` call returning ``(process, None)``.
    """
    if not _is_windows():
        process = await asyncio.create_subprocess_exec(*args, **kwargs)
        return process, None

    spawn_kwargs = dict(kwargs)
    spawn_kwargs["creationflags"] = (
        int(spawn_kwargs.get("creationflags", 0)) | _CREATE_SUSPENDED
    )
    process = await asyncio.create_subprocess_exec(*args, **spawn_kwargs)
    job_handle: JobHandle | None = None
    try:
        pid = getattr(process, "pid", None)
        if not isinstance(pid, int):
            log.debug("spawned process has no integer pid; cannot assign owner job")
            try:
                process.kill()
            except ProcessLookupError:
                pass
            raise RuntimeError("spawned suspended process has no integer pid")
        job_handle = _assign_suspended_to_kill_on_close_job(pid)
        if not _resume_suspended_process(pid):
            log.debug("killing pid %s because suspended-start resume failed", pid)
            try:
                process.kill()
            except ProcessLookupError:
                pass
            if job_handle is not None:
                job_handle.close()
            raise RuntimeError(f"failed to resume suspended process {pid}")
    except BaseException:
        if job_handle is not None:
            job_handle.close()
        raise
    return process, job_handle


def spawn_sync_in_kill_on_close_job(
    argv: list[str],
    *,
    require_job: bool = False,
    allow_breakaway: bool = True,
    **kwargs: Any,
) -> tuple[subprocess.Popen, JobHandle | None]:
    """Synchronous (``subprocess.Popen``) counterpart to
    :func:`spawn_in_kill_on_close_job`, for callers that cannot use ``asyncio``.

    Mirrors the same containment contract: on Windows the child is created
    with ``CREATE_SUSPENDED``, assigned to a kill-on-close Job Object, and then
    resumed, with no gap between creation and assignment in which it (or a
    descendant it spawns) could escape the job. A caller that later needs to
    forcibly terminate the whole process tree -- e.g. after its own
    ``communicate(timeout=...)`` raises ``subprocess.TimeoutExpired`` -- should
    call ``job_handle.close()``, which (via
    ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``) kills every process still in the
    job, not just the immediate child. A plain ``proc.kill()`` on Windows only
    terminates that immediate child: any grandchildren it spawned (a `.cmd`
    shim re-execing `python.exe`, a `pwsh.exe` wrapper invoking `git`/`copilot`,
    etc.) survive, can keep the stdout/stderr pipes open, and leave a
    subsequent ``communicate()`` blocked indefinitely even though the direct
    child is already dead -- exactly the failure mode that left a real
    self-update sweep tick alive-but-stuck for over a day in practice.

    With ``require_job=True``, failed Windows Job assignment kills the still
    suspended child before it can launch descendants and raises explicitly.
    ``allow_breakaway=False`` prohibits descendants from escaping this Job and
    rejects an explicit CREATE_BREAKAWAY_FROM_JOB flag on the initial child.
    The default permits explicit breakaway, preserving legacy daemon semantics.
    Otherwise, if job creation or assignment fails, the child still runs (resumed) and is
    returned with ``job_handle=None`` so existing cleanup paths remain in
    charge -- containment is a best-effort hardening layer, not a hard
    dependency for the child to execute at all. Off Windows this is a plain
    ``subprocess.Popen`` call returning ``(process, None)``.
    """
    if type(allow_breakaway) is not bool:
        raise ValueError("allow_breakaway must be a boolean")
    if (
        not allow_breakaway
        and int(kwargs.get("creationflags", 0)) & _CREATE_BREAKAWAY_FROM_JOB
    ):
        raise ValueError("CREATE_BREAKAWAY_FROM_JOB conflicts with allow_breakaway=False")
    if not _is_windows():
        return subprocess.Popen(argv, **kwargs), None  # noqa: S603 - argv list, no shell

    spawn_kwargs = dict(kwargs)
    spawn_kwargs["creationflags"] = (
        int(spawn_kwargs.get("creationflags", 0)) | _CREATE_SUSPENDED
    )
    process = subprocess.Popen(argv, **spawn_kwargs)  # noqa: S603 - argv list, no shell
    job_handle: JobHandle | None = None
    try:
        pid = process.pid
        if allow_breakaway:
            job_handle = _assign_suspended_to_kill_on_close_job(pid)
        else:
            job_handle = _assign_suspended_to_kill_on_close_job(pid, allow_breakaway=False)
        if job_handle is None and (require_job or not allow_breakaway):
            process.kill()
            process.wait(timeout=5)
            raise RuntimeError("Windows descendant Job assignment failed before child resume")
        if not _resume_suspended_process(pid):
            if require_job or not allow_breakaway:
                process.kill()
                process.wait(timeout=5)
                raise RuntimeError(f"failed to resume suspended process {pid}")
            log.debug("killing pid %s because suspended-start resume failed", pid)
            try:
                process.kill()
            except (ProcessLookupError, OSError):
                pass
            if job_handle is not None:
                job_handle.close()
            raise RuntimeError(f"failed to resume suspended process {pid}")
    except BaseException:
        if job_handle is not None:
            job_handle.close()
        raise
    return process, job_handle


def _venv_home_pythonw(python: str) -> str | None:
    """Return the base ``pythonw.exe`` recorded by a venv's ``pyvenv.cfg``.

    Relocatable Windows venv launchers (for example from ``uv venv``) can ship a
    tiny local ``pythonw.exe`` trampoline that re-execs the base interpreter
    named by ``pyvenv.cfg``'s ``home = ...`` entry. When that base install also
    ships a genuine GUI-subsystem ``pythonw.exe``, prefer it over the local
    trampoline so the detached daemon never re-enters a console interpreter.
    """
    venv_root = os.path.dirname(os.path.dirname(python))
    cfg_path = os.path.join(venv_root, "pyvenv.cfg")
    try:
        with open(cfg_path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                key, _, value = line.partition("=")
                if key.strip() != "home":
                    continue
                home = value.strip()
                if not home:
                    return None
                base_pythonw = os.path.join(home, "pythonw.exe")
                return base_pythonw if os.path.isfile(base_pythonw) else None
    except OSError:
        return None
    return None


def no_window_flags() -> int:
    """The ``CREATE_NO_WINDOW`` creation-flag **int** on Windows, else ``0``.

    For call sites that pass ``creationflags=`` directly (rather than splatting
    kwargs)::

        subprocess.run(cmd, capture_output=True, creationflags=no_window_flags())

    Off Windows this is ``0`` (a harmless no-op creationflags value).
    """
    return _CREATE_NO_WINDOW if _is_windows() else 0


def no_window_kwargs() -> dict:
    """``subprocess`` kwargs that suppress a console window on Windows.

    Returns ``{"creationflags": CREATE_NO_WINDOW}`` on Windows and ``{}``
    elsewhere, so it splats into a ``subprocess.run`` / ``Popen`` call while
    still allowing captured stdout/stderr over pipes (unlike a full detach).
    """
    if _is_windows():
        return {"creationflags": _CREATE_NO_WINDOW}
    return {}


def windowless_python(executable: str | os.PathLike[str] | None = None) -> str:
    """Return the interpreter for a fully detached Python daemon.

    A Windows venv ``python.exe`` is a console-subsystem launcher. Even when it
    starts under ``DETACHED_PROCESS``, it can re-exec a base ``python.exe`` as a
    child, which allocates a fresh console that Windows Terminal may capture.
    Prefer the base install's own ``pythonw.exe`` recorded in ``pyvenv.cfg``
    when available; otherwise fall back to the local ``pythonw.exe`` sibling.
    Off Windows, or when no windowless interpreter exists, return the requested
    interpreter unchanged.
    """
    python = os.fspath(executable) if executable is not None else sys.executable
    if not _is_windows():
        return python
    base_pythonw = _venv_home_pythonw(python)
    if base_pythonw:
        return base_pythonw
    candidate = os.path.join(os.path.dirname(python), "pythonw.exe")
    return candidate if os.path.isfile(candidate) else python


def windowless_python_env(
    executable: str | os.PathLike[str] | None = None,
) -> dict[str, str]:
    """Extra child environment needed by :func:`windowless_python`.

    When ``windowless_python()`` skips a relocatable venv's local trampoline and
    targets the base install's real ``pythonw.exe``, CPython still needs the
    original venv launcher path in ``__PYVENV_LAUNCHER__`` so it keeps the venv
    ``sys.prefix`` / ``site-packages`` instead of falling back to the base
    install. Other cases need no environment changes.
    """
    python = os.fspath(executable) if executable is not None else sys.executable
    if not _is_windows():
        return {}
    if _venv_home_pythonw(python):
        return {"__PYVENV_LAUNCHER__": python}
    return {}


def detached_kwargs(*, breakaway: bool = False) -> dict:
    """``Popen`` kwargs that fully **detach** a background child from its parent.

    On Windows, ``DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`` cuts the child
    from the parent console and process group -- a detached process has no
    console, so no window ever appears. Pass ``breakaway=True`` to also add
    ``CREATE_BREAKAWAY_FROM_JOB`` so the child survives when the parent lives in
    a Job object that would otherwise kill its whole tree (the case for a
    daemon spawned from a session-start hook).

    On POSIX, ``start_new_session=True`` puts the child in its own session so a
    parent exit / signal never reaps it. When
    ``COPILOT_EXTENSIONS_TEST_CONTAINED=1``, Windows Job breakaway and POSIX
    session detachment are suppressed so the test runner retains descendant
    ownership.
    """
    if _is_windows():
        flags = _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP
        if breakaway and not contained_test_mode():
            flags |= _CREATE_BREAKAWAY_FROM_JOB
        return {"creationflags": flags}
    if contained_test_mode():
        return {}
    return {"start_new_session": True}


def windowless_daemon_kwargs(*, breakaway: bool = False) -> dict:
    """``Popen`` kwargs for a survivable daemon whose children stay windowless.

    Windows uses ``CREATE_NO_WINDOW`` rather than ``DETACHED_PROCESS`` because
    a console-subsystem child spawned by a detached process can allocate a new
    visible console. POSIX uses the same new-session behavior as
    :func:`detached_kwargs`. Contained tests suppress Job breakaway and POSIX
    session detachment.
    """
    if _is_windows():
        flags = _CREATE_NO_WINDOW
        if breakaway and not contained_test_mode():
            flags |= _CREATE_BREAKAWAY_FROM_JOB
        return {"creationflags": flags}
    if contained_test_mode():
        return {}
    return {"start_new_session": True}


_PAYLOAD_DIR_MARKER = "installed-plugins"

# The only creation flags a spawn_daemon caller may add: process priority
# classes and CREATE_DEFAULT_ERROR_MODE. Console, process-group, Job, session,
# debug and suspend flags belong to spawn_daemon's own windowless and lifetime
# contract (CREATE_NEW_CONSOLE, for instance, makes Windows ignore
# CREATE_NO_WINDOW, and CREATE_BREAKAWAY_FROM_JOB defeats ``breakaway=False``
# and contained test mode), so an allowlist rather than a denylist.
_DAEMON_EXTRA_CREATIONFLAGS = (
    0x00000020  # NORMAL_PRIORITY_CLASS
    | 0x00000040  # IDLE_PRIORITY_CLASS
    | 0x00000080  # HIGH_PRIORITY_CLASS
    | 0x00000100  # REALTIME_PRIORITY_CLASS
    | 0x00004000  # BELOW_NORMAL_PRIORITY_CLASS
    | 0x00008000  # ABOVE_NORMAL_PRIORITY_CLASS
    | 0x04000000  # CREATE_DEFAULT_ERROR_MODE
)


class DaemonSpawnError(OSError):
    """:func:`spawn_daemon` could not give the daemon its own lifetime."""


def _validated_daemon_cwd(cwd: str | os.PathLike[str] | None) -> str:
    """Reject a daemon cwd that is missing, relative, or inside a plugin payload.

    A long-lived daemon pins its cwd for its whole life. An inherited cwd (a
    worktree that is later finalized) or a payload directory (replaced on the
    next plugin update) breaks every relative-path child it spawns once that
    directory disappears, and on Windows it also blocks the directory's removal.
    """
    if cwd is None:
        raise ValueError("spawn_daemon requires an explicit cwd (a runtime root or home)")
    path = os.fspath(cwd)
    if not path or not os.path.isabs(path):
        raise ValueError(f"spawn_daemon cwd must be an absolute path, got {path!r}")
    if not os.path.isdir(path):
        raise ValueError(f"spawn_daemon cwd does not exist: {path}")
    # Check the resolved target too: a symlink or junction such as
    # ``<runtime>/current -> .../installed-plugins/...`` would otherwise pass.
    for candidate in (path, os.path.realpath(path)):
        if _is_inside_payload(candidate):
            raise ValueError(
                f"spawn_daemon cwd is inside a plugin payload ({path}"
                + (f" -> {candidate}" if candidate != path else "")
                + "); use the daemon's runtime root or home directory instead"
            )
    return path


def _is_inside_payload(path: str) -> bool:
    parts = {part.lower() for part in os.path.normpath(path).replace("\\", "/").split("/")}
    return _PAYLOAD_DIR_MARKER in parts


def _validated_daemon_log_path(
    log_path: str | os.PathLike[str] | None, name: str = "log_path"
) -> str:
    """Reject a log path that is missing, relative, or inside a plugin payload.

    The daemon holds its log open for its whole life, so a log inside a
    payload directory pins that directory exactly like a payload cwd would.
    """
    if log_path is None:
        raise ValueError(f"spawn_daemon requires a {name}")
    path = os.fspath(log_path)
    if not path or not os.path.isabs(path):
        # Never resolved against ``cwd``: a caller-relative path would silently
        # land somewhere else once the daemon's cwd differs from the caller's.
        raise ValueError(f"spawn_daemon {name} must be an absolute path, got {path!r}")
    for candidate in (path, os.path.realpath(path)):
        if _is_inside_payload(candidate):
            raise ValueError(
                f"spawn_daemon {name} is inside a plugin payload ({path}"
                + (f" -> {candidate}" if candidate != path else "")
                + "); keep the daemon log under its runtime root or home directory"
            )
    return path


def _validated_daemon_argv(argv: object) -> list[str]:
    # A bare string (or path) is itself a Sequence: iterating it would launch
    # its first character as the executable.
    if isinstance(argv, (str, bytes, bytearray, os.PathLike)):
        raise TypeError(
            "spawn_daemon argv must be a sequence of arguments, not a single "
            f"{type(argv).__name__}; pass [executable, *args]"
        )
    command = [os.fspath(part) for part in argv]  # type: ignore[union-attr]
    if not command:
        raise ValueError("spawn_daemon requires a non-empty argv")
    return command


def _validated_extra_creationflags(flags: object) -> int:
    if isinstance(flags, bool) or not isinstance(flags, int):
        raise TypeError(f"spawn_daemon extra_creationflags must be an int, got {flags!r}")
    owned = flags & ~_DAEMON_EXTRA_CREATIONFLAGS
    if owned:
        raise ValueError(
            f"spawn_daemon extra_creationflags {owned:#x} are not permitted: console, "
            "process-group, Job and session flags belong to spawn_daemon itself; "
            "only priority classes and CREATE_DEFAULT_ERROR_MODE may be added"
        )
    return flags


def _daemon_popen_kwargs(
    *, console_children: bool, breakaway: bool, extra_creationflags: int
) -> dict:
    kwargs = (
        windowless_daemon_kwargs(breakaway=breakaway)
        if console_children
        else detached_kwargs(breakaway=breakaway)
    )
    if _is_windows() and extra_creationflags:
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | extra_creationflags
    return kwargs


def _breakaway_denied(exc: OSError, kwargs: dict) -> bool:
    flags = kwargs.get("creationflags", 0)
    return bool(flags & _CREATE_BREAKAWAY_FROM_JOB) and (
        getattr(exc, "winerror", None) == _ERROR_ACCESS_DENIED
    )


_DAEMON_LOG_MODE = 0o600
_DAEMON_LOG_DIR_MODE = 0o700


def _open_daemon_log(log_file: str) -> BinaryIO:
    """Open the daemon log for binary append, private to its owner on POSIX.

    The log receives the daemon's raw stdout/stderr, which may include
    credential-bearing errors. On POSIX a new log is created ``0600`` (and a
    missing parent directory ``0700``); an existing log readable by group or
    others is tightened to ``0600`` before any child can write to it, failing
    closed if that is not permitted. Pre-existing parent directories are left
    alone. Windows has no POSIX modes: the log keeps the ACL it inherits.
    """
    os.makedirs(os.path.dirname(log_file), mode=_DAEMON_LOG_DIR_MODE, exist_ok=True)

    def opener(path: str, flags: int) -> int:
        # O_BINARY keeps the Windows CRT from translating newlines on this fd.
        return os.open(path, flags | getattr(os, "O_BINARY", 0), _DAEMON_LOG_MODE)

    log_fh = open(log_file, "ab", opener=opener)
    if os.name == "posix":
        try:
            fd = log_fh.fileno()
            if os.fstat(fd).st_mode & 0o077:
                os.fchmod(fd, _DAEMON_LOG_MODE)
        except BaseException:
            log_fh.close()
            raise
    return log_fh


def _header_field(value: object) -> str:
    """Escape control and line-break characters so a field stays on one line.

    Lone surrogates (undecodable POSIX path bytes) pass through here and are
    escaped by the ``backslashreplace`` encode in :func:`_daemon_log_header`.
    """
    text = str(value)
    if text.isprintable():
        return text
    return "".join(
        ch if ch.isprintable() or 0xD800 <= ord(ch) <= 0xDFFF
        else ch.encode("unicode_escape").decode("ascii")
        for ch in text
    )


def _daemon_log_header(stamp: str, cwd: str, executable: str, display: str | None) -> bytes:
    """The durable launch header: never the argv tail or the environment.

    Always exactly one line, and never fails to encode, whatever bytes a valid
    path holds.
    """
    header = (
        f"{stamp} spawn_daemon cwd={_header_field(cwd)} "
        f"executable={_header_field(executable)}"
    )
    if display:
        header += " display=" + _header_field(" ".join(str(display).split()))
    return (header + "\n").encode("utf-8", errors="backslashreplace")


def spawn_daemon(
    argv: Sequence[str | os.PathLike[str]],
    *,
    cwd: str | os.PathLike[str],
    log_path: str | os.PathLike[str],
    env: Mapping[str, str] | None = None,
    env_overrides: Mapping[str, str] | None = None,
    console_children: bool = False,
    breakaway: bool = True,
    contained_fallback: bool = False,
    extra_creationflags: int = 0,
    display: str | None = None,
    stderr_log_path: str | os.PathLike[str] | None = None,
) -> subprocess.Popen:
    """Launch a long-lived daemon that outlives its caller.

    The one shared launch primitive for resident daemons, built on
    :func:`detached_kwargs` / :func:`windowless_daemon_kwargs` so the creation
    flags never drift from the rest of this module:

    * **Windowless.** ``console_children=False`` (default) detaches the daemon
      from every console (``DETACHED_PROCESS``); pass ``True`` for a daemon that
      repeatedly spawns console-subsystem children, which then inherit its
      hidden ``CREATE_NO_WINDOW`` console instead of each allocating a window.
    * **Escapes the caller.** With ``breakaway=True`` (default) the Windows
      child requests ``CREATE_BREAKAWAY_FROM_JOB`` so a kill-on-close Job
      around the caller (an installer, an updater, a hook) does not take the
      daemon down with it; on POSIX the child gets its own session. A Job that
      forbids breakaway rejects the spawn with ``ERROR_ACCESS_DENIED``, which
      is surfaced as :class:`DaemonSpawnError` (an ``OSError``) and noted in
      the daemon log: a daemon that silently inherits the caller's lifetime
      would die when that Job closes. A caller whose daemon previously ran
      inside the caller's Job anyway, and for which that degraded lifetime is
      still better than no daemon, passes ``contained_fallback=True`` to start
      it inside the Job instead (logged as a warning and in the daemon log).
      That daemon then does **not** outlive the caller's Job; neither does one
      started with ``breakaway=False`` or in contained test mode.
    * **Explicit cwd.** ``cwd`` is required, must be absolute and existing,
      and must not be inside an ``installed-plugins`` payload, either
      lexically or once symlinks/junctions are resolved -- use the daemon's
      runtime root or home directory, never the caller's cwd. Because the
      daemon runs from ``cwd`` rather than the caller's directory, any path
      the caller passes in ``argv`` or the environment should be absolute.
    * **Logged.** stdin is ``DEVNULL``; stdout and stderr are appended to
      ``log_path`` (must be absolute and, like ``cwd``, outside a plugin
      payload, since the daemon holds it open; parent directories are
      created), after a one-line launch
      header. On POSIX the log is private to its owner: created ``0600``
      (a created parent directory ``0700``), and an existing log readable by
      group or others is tightened to ``0600`` before the child starts.
      The parent closes its copy of the log handle before returning.
      The header names the cwd and the executable (``argv[0]``) only, plus
      ``display`` when given: the remaining argv and the environment are never
      written, since either may carry a token, password or credential URL. A
      caller that wants more context in the log passes an already-redacted
      ``display`` label. A daemon whose operators read its stderr separately
      passes ``stderr_log_path`` (validated, created and tightened exactly like
      ``log_path``; the same path as ``log_path`` merges); the launch header
      and any breakaway note are then written to both logs.

    ``argv`` is a sequence of arguments; a bare ``str``/``bytes``/path raises
    :class:`TypeError` rather than being split into characters. ``env``
    defaults to a copy of ``os.environ``; ``env_overrides`` is layered on top.
    ``extra_creationflags`` may add only a priority class or
    ``CREATE_DEFAULT_ERROR_MODE`` to the Windows creation flags; any flag this
    primitive owns (console, process-group, Job or session) raises
    :class:`ValueError` on every platform. Contained test mode suppresses
    breakaway and the new session exactly as the underlying helpers do.
    Interpreter selection stays with the caller (:func:`windowless_python`
    and :func:`windowless_python_env`). Never waits for the child.
    """
    command = _validated_daemon_argv(argv)
    extra_creationflags = _validated_extra_creationflags(extra_creationflags)
    cwd_path = _validated_daemon_cwd(cwd)
    log_file = _validated_daemon_log_path(log_path)
    err_file = None
    if stderr_log_path is not None:
        err_file = _validated_daemon_log_path(stderr_log_path, "stderr_log_path")
        same = [os.path.normcase(os.path.normpath(p)) for p in (err_file, log_file)]
        if same[0] == same[1]:
            err_file = None

    child_env = dict(os.environ if env is None else env)
    if env_overrides:
        child_env.update(env_overrides)
    kwargs = _daemon_popen_kwargs(
        console_children=console_children,
        breakaway=breakaway,
        extra_creationflags=extra_creationflags,
    )
    with contextlib.ExitStack() as stack:
        log_fh = stack.enter_context(_open_daemon_log(log_file))
        err_fh = stack.enter_context(_open_daemon_log(err_file)) if err_file else None
        logs = [fh for fh in (log_fh, err_fh) if fh is not None]

        def write_logs(data: bytes) -> None:
            for fh in logs:
                fh.write(data)
                fh.flush()

        stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        write_logs(_daemon_log_header(stamp, cwd_path, command[0], display))
        popen_kwargs = {
            "cwd": cwd_path,
            "env": child_env,
            "stdin": subprocess.DEVNULL,
            "stdout": log_fh,
            "stderr": subprocess.STDOUT if err_fh is None else err_fh,
            "close_fds": True,
        }
        try:
            return subprocess.Popen(command, **popen_kwargs, **kwargs)  # noqa: S603
        except OSError as exc:
            if not _breakaway_denied(exc, kwargs):
                raise
            note = "spawn_daemon: the caller's Job object forbids breakaway"
            if not contained_fallback:
                write_logs(f"{stamp} {note}; daemon not started\n".encode())
                raise DaemonSpawnError(
                    f"{note}; the daemon would die with its caller "
                    f"({command[0]})"
                ) from exc
            note += "; starting the daemon inside it (contained_fallback)"
            log.warning("%s (%s)", note, command[0])
            write_logs(f"{stamp} {note}\n".encode())
            kwargs["creationflags"] &= ~_CREATE_BREAKAWAY_FROM_JOB
            return subprocess.Popen(command, **popen_kwargs, **kwargs)  # noqa: S603

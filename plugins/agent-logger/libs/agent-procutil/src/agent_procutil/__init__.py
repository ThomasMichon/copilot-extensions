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
import ctypes
import logging
import os
import subprocess
import sys
from typing import Any

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

    def __enter__(self) -> "JobHandle":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()


def _kernel32() -> Any:
    return ctypes.WinDLL("kernel32", use_last_error=True)


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


def _assign_suspended_to_kill_on_close_job(pid: int) -> JobHandle | None:
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
        limit_info.BasicLimitInformation.LimitFlags = (
            _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | _JOB_OBJECT_LIMIT_BREAKAWAY_OK
        )
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

    If job creation or assignment fails, the child still runs (resumed) and is
    returned with ``job_handle=None`` so existing cleanup paths remain in
    charge -- containment is a best-effort hardening layer, not a hard
    dependency for the child to execute at all. Off Windows this is a plain
    ``subprocess.Popen`` call returning ``(process, None)``.
    """
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
        job_handle = _assign_suspended_to_kill_on_close_job(pid)
        if not _resume_suspended_process(pid):
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

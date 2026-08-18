"""Parent-death watchdog + descendant reaping for the stdio bridge.

The bridge normally shuts down on stdin EOF (see :meth:`agent_mcp.bridge.Bridge.run`).
That signal is defeated whenever an intermediate launcher interposes between the
Copilot runtime and the Python bridge -- most notably the Windows ``agent-mcp.cmd``
shim, which the runtime spawns as ``cmd.exe`` with ``python -m agent_mcp bridge`` as
a *grandchild*. When the sub-agent ends the runtime terminates only the ``cmd.exe``
it directly spawned; the grandchild Python is not in a kill-on-close Job Object and
its inherited stdin never sees EOF, so the reader thread blocks forever and the
whole tree leaks (dotfiles#1562: 68 orphaned bridge processes observed on cloud1).

Two mechanisms close that gap:

* :func:`install_parent_death_watchdog` -- a daemon thread that polls the liveness
  of the launch-time parent and, when it goes away, drives the *same* graceful
  teardown as stdin EOF (plus a hard-exit backstop if teardown wedges). This is the
  portable "detect completion -> propagate termination" seam; it fires whether the
  ``cmd`` shim dies (Windows) or the runtime parent dies (POSIX).
* :func:`reap_descendants_on_exit` -- on Windows, assign this process to a Job
  Object with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` so every descendant (the
  upstream stdio child, plus any ``az``/``gh``/``git`` mint helpers) is killed when
  this process exits. A no-op on POSIX, where the graceful path already closes the
  upstream child and short-lived helpers finish on their own.

All behaviour is env-configurable:

* ``AGENT_MCP_PARENT_WATCHDOG``          -- ``0``/``false``/``off`` disables the watchdog.
* ``AGENT_MCP_PARENT_WATCHDOG_INTERVAL`` -- poll interval, seconds (default ``5``; ``<=0``
  disables).
* ``AGENT_MCP_PARENT_WATCHDOG_GRACE``    -- hard-exit backstop after signalling, seconds
  (default ``10``; ``0`` disables the backstop -- graceful teardown only).
* ``AGENT_MCP_REAP_DESCENDANTS``         -- ``0``/``false``/``off`` disables the Windows kill-job.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from collections.abc import Callable

log = logging.getLogger("agent-mcp.watchdog")

_IS_WINDOWS = sys.platform == "win32"

# --- env parsing ----------------------------------------------------------

_FALSEY = {"0", "false", "off", "no", ""}


def _env_flag(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in _FALSEY


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        log.warning("watchdog: %s=%r is not a number; using default %s", name, raw, default)
        return default


# --- parent-liveness probe ------------------------------------------------

if _IS_WINDOWS:  # pragma: no cover - platform-specific
    import ctypes

    _SYNCHRONIZE = 0x00100000
    _WAIT_TIMEOUT = 0x00000102
    _TH32CS_SNAPPROCESS = 0x00000002

    # How far up the ancestry to watch. The bridge must not outlive whatever
    # launched it, so we watch the immediate parent *and* several ancestors: on
    # Windows the runtime kills the top shim (cmd.exe) it spawned, but an
    # interposed trampoline (a uv/venv ``python.exe`` launcher, or the cmd shim
    # itself) can sit between that shim and this process and keep *its* handle
    # alive, hiding the shim's death from an immediate-parent-only check
    # (dotfiles#1562). Walking the chain fires as soon as any recorded ancestor
    # dies. We stop early at the first ancestor we cannot open (a different
    # session / a system process), which self-bounds the walk to our own chain.
    _MAX_ANCESTORS = 5

    class _PROCESSENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_uint32),
            ("cntUsage", ctypes.c_uint32),
            ("th32ProcessID", ctypes.c_uint32),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", ctypes.c_uint32),
            ("cntThreads", ctypes.c_uint32),
            ("th32ParentProcessID", ctypes.c_uint32),
            ("pcPriClassBase", ctypes.c_int32),
            ("dwFlags", ctypes.c_uint32),
            ("szExeFile", ctypes.c_char * 260),
        ]

    def _kernel32():
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        # Configure prototypes so handles round-trip as pointer-sized values
        # (without argtypes, ctypes truncates 64-bit HANDLEs to a C int).
        k.OpenProcess.restype = ctypes.c_void_p
        k.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        k.WaitForSingleObject.restype = ctypes.c_uint32
        k.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        k.CloseHandle.restype = ctypes.c_int
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        k.CreateToolhelp32Snapshot.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        k.Process32First.restype = ctypes.c_int
        k.Process32First.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        k.Process32Next.restype = ctypes.c_int
        k.Process32Next.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        k.CreateJobObjectW.restype = ctypes.c_void_p
        k.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        k.SetInformationJobObject.restype = ctypes.c_int
        k.SetInformationJobObject.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32
        ]
        k.AssignProcessToJobObject.restype = ctypes.c_int
        k.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        k.GetCurrentProcess.restype = ctypes.c_void_p
        k.GetCurrentProcess.argtypes = []
        return k

    def _parent_map(k) -> dict[int, int]:
        """Snapshot pid -> parent-pid for the whole system (toolhelp)."""
        snap = k.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        if not snap or snap == ctypes.c_void_p(-1).value:
            return {}
        parents: dict[int, int] = {}
        try:
            entry = _PROCESSENTRY32()
            entry.dwSize = ctypes.sizeof(_PROCESSENTRY32)
            ok = k.Process32First(snap, ctypes.byref(entry))
            while ok:
                parents[entry.th32ProcessID] = entry.th32ParentProcessID
                ok = k.Process32Next(snap, ctypes.byref(entry))
        finally:
            k.CloseHandle(snap)
        return parents

    def _default_probe() -> Callable[[], bool] | None:
        """A probe that returns True while the launch-time ancestor chain is intact.

        Records a stable handle to each ancestor (immediate parent upward, bounded
        by :data:`_MAX_ANCESTORS` and by the first ancestor we cannot open), taken
        while they are alive so PID reuse cannot alias a different process later.
        """
        k = _kernel32()
        parents = _parent_map(k)
        handles: list[tuple[int, int]] = []  # (pid, handle)
        pid = os.getppid()
        seen: set[int] = set()
        while pid and pid not in seen and len(handles) < _MAX_ANCESTORS:
            seen.add(pid)
            handle = k.OpenProcess(_SYNCHRONIZE, False, pid)
            if not handle:
                break  # different session / system process: chain boundary
            handles.append((pid, handle))
            pid = parents.get(pid, 0)
        if not handles:
            log.info("watchdog: no watchable ancestor (getppid=%s); watchdog disabled",
                     os.getppid())
            return None
        watched = [p for p, _ in handles]
        log.debug("watchdog: pid=%s watching ancestors %s", os.getpid(), watched)

        def alive() -> bool:
            for apid, handle in handles:
                rc = k.WaitForSingleObject(handle, 0)
                if rc != _WAIT_TIMEOUT:  # signaled (dead) or wait failed
                    log.debug("watchdog: ancestor pid %s gone (rc 0x%x)", apid, rc)
                    return False
            return True

        return alive

else:

    def _default_probe() -> Callable[[], bool] | None:
        """A probe that returns True while the launch-time parent is alive.

        On POSIX a dead parent reparents us (``getppid`` changes, typically to the
        init process or a subreaper), so a simple comparison is sufficient -- the
        POSIX binstub ``exec``s Python, so no intermediate launcher lingers.
        """
        parent_pid = os.getppid()

        def alive() -> bool:
            return os.getppid() == parent_pid

        return alive


# --- watchdog -------------------------------------------------------------

def install_parent_death_watchdog(
    on_death: Callable[[], None],
    *,
    probe: Callable[[], bool] | None = None,
    interval: float | None = None,
    grace: float | None = None,
) -> threading.Thread | None:
    """Start a daemon thread that invokes ``on_death`` once the parent is gone.

    ``on_death`` should trigger the bridge's normal graceful shutdown (the same
    path as stdin EOF). If ``grace`` is positive and the process is still alive
    that many seconds after ``on_death``, the watchdog force-exits via
    :func:`os._exit` so a wedged teardown can never leave the tree leaked.

    ``probe`` (returning True while the parent is alive) is injectable for tests;
    it defaults to the platform probe. Returns the watchdog thread, or ``None`` if
    the watchdog is disabled / could not be armed.
    """
    if not _env_flag("AGENT_MCP_PARENT_WATCHDOG", True):
        log.debug("watchdog: disabled by AGENT_MCP_PARENT_WATCHDOG")
        return None
    if interval is None:
        interval = _env_float("AGENT_MCP_PARENT_WATCHDOG_INTERVAL", 5.0)
    if interval <= 0:
        log.debug("watchdog: non-positive interval (%s); disabled", interval)
        return None
    if grace is None:
        grace = _env_float("AGENT_MCP_PARENT_WATCHDOG_GRACE", 10.0)

    if probe is None:
        probe = _default_probe()
    if probe is None:
        return None  # could not establish a parent handle

    def _watch() -> None:
        while True:
            time.sleep(interval)
            try:
                if probe():
                    continue
            except Exception as exc:  # transient probe failure -> assume alive
                log.warning("watchdog: parent probe failed (%s); assuming alive", exc)
                continue
            log.info("watchdog: launch parent gone; initiating bridge shutdown")
            try:
                on_death()
            except Exception as exc:
                log.error("watchdog: shutdown callback failed: %s", exc)
            if grace > 0:
                time.sleep(grace)
                log.warning("watchdog: graceful shutdown did not complete in %.1fs; "
                            "forcing exit", grace)
                os._exit(0)
            return

    thread = threading.Thread(target=_watch, name="agent-mcp-watchdog", daemon=True)
    thread.start()
    return thread


# --- descendant reaping (Windows kill-on-close Job Object) ----------------

# Hold the only handle to the kill-on-close job for the life of the process: when
# the process exits and this handle closes, the job reaps any surviving descendant.
_job_handle: object | None = None


def reap_descendants_on_exit() -> bool:
    """Ensure this process's descendants die when it exits.

    On Windows, assign the current process to a Job Object with
    ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``; inherited children (the upstream stdio
    MCP, ``az``/``gh``/``git`` mint helpers) then die with the bridge. On POSIX this
    is a no-op (returns ``False``): the graceful path closes the upstream child and
    helper subprocesses are short-lived.

    Non-fatal on failure -- the watchdog + transport teardown remain the primary
    mechanism. Returns True only when a kill-on-close job was armed.
    """
    global _job_handle
    if not _IS_WINDOWS:
        return False
    if not _env_flag("AGENT_MCP_REAP_DESCENDANTS", True):
        return False
    try:  # pragma: no cover - platform-specific
        import ctypes

        _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
        _JobObjectExtendedLimitInformation = 9

        class _BASIC_LIMIT(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32),
            ]

        class _IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class _EXTENDED_LIMIT(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BASIC_LIMIT),
                ("IoInfo", _IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        k = _kernel32()
        job = k.CreateJobObjectW(None, None)
        if not job:
            log.info("watchdog: CreateJobObject failed (err %s); descendant reaping off",
                     ctypes.get_last_error())
            return False
        info = _EXTENDED_LIMIT()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k.SetInformationJobObject(job, _JobObjectExtendedLimitInformation,
                                         ctypes.byref(info), ctypes.sizeof(info)):
            log.info("watchdog: SetInformationJobObject failed (err %s)",
                     ctypes.get_last_error())
            k.CloseHandle(job)
            return False
        if not k.AssignProcessToJobObject(job, k.GetCurrentProcess()):
            # Common on older Windows where we are already in a non-nestable job.
            log.info("watchdog: could not assign process to kill-on-close job "
                     "(err %s); relying on watchdog + transport teardown",
                     ctypes.get_last_error())
            k.CloseHandle(job)
            return False
        _job_handle = job  # keep the sole handle open until process exit
        log.info("watchdog: descendants will be reaped on exit (kill-on-close job)")
        return True
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("watchdog: job-object setup failed: %s", exc)
        return False

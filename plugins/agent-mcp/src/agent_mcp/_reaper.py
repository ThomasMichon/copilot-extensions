"""Reap a spawned upstream's ENTIRE process tree when the bridge closes or dies.

A stdio upstream is frequently a *launcher* -- ``npx``/``npx.cmd``, a ``.cmd``
shim, ``uv`` -- whose real MCP server runs as a **grandchild**. Terminating only
the direct child (the launcher) leaks the grandchildren, which is exactly the
``npx``-MCP process accumulation this exists to stop. :class:`TreeReaper` binds
the whole spawned tree to the bridge's lifetime, and is used **only** when the
bridge opts in with ``server.reap: tree`` (see :data:`agent_mcp.config.REAP_MODES`)
-- a server that fronts a self-managed singleton daemon (``server.reap: none``)
or that *is* the direct process (``child``, the default) must not be tree-killed.

* **Windows** -- a Job Object with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``. The
  child is assigned to the job; every descendant it spawns inherits the job
  (unless it explicitly breaks away). Closing the job handle -- explicitly in
  :meth:`close`, or implicitly when THIS process dies and the OS releases the
  handle -- terminates the whole job. This survives an *abrupt* kill of the
  bridge (no graceful shutdown required), which the historical ``terminate()``
  path did not.
* **POSIX** -- the child is spawned in its own session (``start_new_session``),
  so it heads a new process group; :meth:`close` signals the group (SIGTERM,
  then SIGKILL) so grandchildren die with it.

Off the opted-in path this module is inert: :meth:`spawn_kwargs` returns ``{}``
and :meth:`track`/:meth:`close` are no-ops until the caller decides to use it.
"""

from __future__ import annotations

import logging
import os
import signal

log = logging.getLogger("agent-mcp.reaper")

if os.name == "nt":  # pragma: no cover - exercised only on Windows
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JobObjectExtendedLimitInformation = 9
    _PROCESS_SET_QUOTA = 0x0100
    _PROCESS_TERMINATE = 0x0001

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    def _create_kill_on_close_job() -> int | None:
        job = _k32.CreateJobObjectW(None, None)
        if not job:
            log.debug("CreateJobObject failed (err=%d)", ctypes.get_last_error())
            return None
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = _k32.SetInformationJobObject(
            job,
            _JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            log.debug("SetInformationJobObject failed (err=%d)", ctypes.get_last_error())
            _k32.CloseHandle(job)
            return None
        return job

    def _assign_pid_to_job(job: int, pid: int) -> bool:
        h = _k32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid)
        if not h:
            log.debug("OpenProcess(%d) failed (err=%d)", pid, ctypes.get_last_error())
            return False
        try:
            ok = _k32.AssignProcessToJobObject(job, h)
            if not ok:
                log.debug(
                    "AssignProcessToJobObject failed (err=%d)", ctypes.get_last_error()
                )
            return bool(ok)
        finally:
            _k32.CloseHandle(h)


class TreeReaper:
    """Tie a spawned child's whole process tree to the bridge's lifetime.

    Usage::

        reaper = TreeReaper()
        proc = await create_subprocess_exec(*argv, **reaper.spawn_kwargs())
        reaper.track(proc.pid)
        ...
        reaper.close()  # kills the tree (or the OS does it when we die, on Windows)
    """

    def __init__(self) -> None:
        self._job: int | None = None
        self._pgid: int | None = None

    def spawn_kwargs(self) -> dict:
        """Extra ``create_subprocess_exec`` kwargs so the child is reap-able.

        POSIX: ``start_new_session`` puts the child in its own group so
        :meth:`close` can signal the whole group. Windows: nothing here -- the
        job is assigned post-spawn in :meth:`track` (assignment is inherited by
        later-spawned descendants).
        """
        if os.name == "nt":
            return {}
        return {"start_new_session": True}

    def track(self, pid: int) -> None:
        """Begin governing ``pid`` (and its future descendants)."""
        if os.name == "nt":
            self._job = _create_kill_on_close_job()
            if self._job is not None and not _assign_pid_to_job(self._job, pid):
                # Assignment failed -- drop the job so we don't hold a useless
                # handle whose close would kill nothing.
                _k32.CloseHandle(self._job)
                self._job = None
        else:
            try:
                self._pgid = os.getpgid(pid)
            except (ProcessLookupError, PermissionError):
                self._pgid = None

    def close(self) -> None:
        """Terminate the tracked tree. Idempotent; safe if nothing was tracked."""
        if os.name == "nt":
            if self._job is not None:
                # Closing the last handle to a kill-on-close job terminates every
                # process in it (the launcher + all grandchildren).
                _k32.CloseHandle(self._job)
                self._job = None
        else:
            if self._pgid is not None:
                for sig in (signal.SIGTERM, signal.SIGKILL):
                    try:
                        os.killpg(self._pgid, sig)
                    except ProcessLookupError:
                        break  # group already gone
                    except PermissionError:
                        break
                self._pgid = None

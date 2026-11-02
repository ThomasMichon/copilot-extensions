"""Platform-specific process isolation for SSH client trees."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess
import sys
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent_procutil import JobHandle

log = logging.getLogger("ssh-manager")
_TASKKILL_TIMEOUT = 3.0
TERMINATE_GRACE = 5.0
_KILL_WAIT = 2.0
PROCESS_CLEANUP_TIMEOUT = _TASKKILL_TIMEOUT + TERMINATE_GRACE + _KILL_WAIT


@dataclass
class _ProcessCleanup:
    process: asyncio.subprocess.Process
    close: Callable[[], Awaitable[None]]
    task: asyncio.Task | None = None


_CLEANUPS: dict[int, _ProcessCleanup] = {}
_OWNER_JOBS: dict[int, JobHandle] = {}
_WINDOWS_DESCRIPTOR_STATE = "c28fc6f98a2c44abbbd89d6a3037d0d9_POSIX_FD_STATE".casefold()


def close_process_job(process: asyncio.subprocess.Process) -> bool:
    job = _OWNER_JOBS.pop(id(process), None)
    if job is None:
        return False
    job.close()
    return True


def redirected_ssh_env(
    environment: Mapping[str, str] | None, *, windows: bool,
) -> Mapping[str, str] | None:
    """Ancestor descriptor metadata must not describe newly redirected handles."""
    current = os.environ if environment is None else environment
    if windows and any(key.casefold() == _WINDOWS_DESCRIPTOR_STATE for key in current):
        return {
            key: value for key, value in current.items()
            if key.casefold() != _WINDOWS_DESCRIPTOR_STATE
        }
    return environment


def register_process_cleanup(
    process: asyncio.subprocess.Process,
    close: Callable[[], Awaitable[None]],
) -> None:
    _CLEANUPS[id(process)] = _ProcessCleanup(process, close)


async def run_process_cleanup(
    process: asyncio.subprocess.Process, *, timeout: float | None = None,
) -> None:
    """Await the registered cleanup for ``process``, shielded from cancellation.

    ``timeout``, when given, bounds how long THIS caller waits -- it does not
    cancel the underlying cleanup (still ``shield``ed, so a kill already in
    flight runs to completion in the background rather than being abandoned
    mid-``taskkill``). Callers that need a hard cancellation guarantee should
    keep the default unbounded wait; a bounded, best-effort caller (e.g. an
    ambient background watcher whose sole owner is about to exit anyway)
    should pass a modest ceiling so a slow remote process-tree kill can't
    stall the whole caller indefinitely (observed on Windows: `taskkill /T
    /F` against a `gh cs ssh --stdio` child can take 100+ seconds under
    endpoint-protection scanning).
    """
    entry = _CLEANUPS.get(id(process))
    if entry is None:
        return
    if entry.task is None:
        entry.task = asyncio.create_task(entry.close())
    try:
        if timeout is None:
            await asyncio.shield(entry.task)
        else:
            await asyncio.wait_for(asyncio.shield(entry.task), timeout=timeout)
    except (TimeoutError, asyncio.TimeoutError):
        pass
    finally:
        if entry.task.done() and _CLEANUPS.get(id(process)) is entry:
            del _CLEANUPS[id(process)]


def _kill_process(proc: asyncio.subprocess.Process) -> None:
    try:
        proc.kill()
    except ProcessLookupError:
        pass


def ssh_subprocess_kwargs(**kwargs: Any) -> dict[str, Any]:
    """Return isolation kwargs for an SSH root process.

    Native OpenSSH can still surface a Default Terminal window when launched
    with ``CREATE_NO_WINDOW`` from a consoleless service. ``DETACHED_PROCESS``
    isolates the non-interactive root while preserving redirected stdio and its
    PID. Use ``proxy.create_ssh_subprocess`` to also own native proxy children.
    """
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    return kwargs


async def terminate_ssh_process_tree(
    proc: asyncio.subprocess.Process,
    *,
    grace: float = TERMINATE_GRACE,
) -> None:
    """Terminate an SSH root and the ProxyCommand descendants it spawned."""
    cleanup = asyncio.create_task(run_process_cleanup(proc))
    try:
        await _terminate_ssh_process_tree(proc, grace=grace)
    finally:
        _close_unreaped_transport(proc)
        await cleanup


def _close_unreaped_transport(proc: asyncio.subprocess.Process) -> None:
    """Close the pipes of a process whose exit was never observed.

    A kill whose wait ran out, or that was cancelled mid-``taskkill`` when the
    owning ``asyncio.run`` ended, leaves the subprocess transport open. The
    garbage collector then finalizes it after the loop has closed, and its
    ``__del__`` prints "Exception ignored ... Event loop is closed" at exit.
    Closing it here, while the loop still runs, releases it cleanly (the
    process is already being killed).
    """
    if getattr(proc, "returncode", 0) is not None:
        return
    close = getattr(getattr(proc, "_transport", None), "close", None)
    if close is None:
        return
    try:
        close()
    except Exception:  # noqa: BLE001 - best-effort release during teardown
        pass


async def _terminate_ssh_process_tree(
    proc: asyncio.subprocess.Process, *, grace: float,
) -> None:
    # Close the owned Job before a descendant's inherited pipes can block wait().
    owned_tree_closed = close_process_job(proc)
    if proc.returncode is not None:
        return
    pid = getattr(proc, "pid", None)
    if not isinstance(pid, int) or pid <= 0:
        _kill_process(proc)
        await proc.wait()
        return
    if sys.platform == "win32" and not owned_tree_closed:
        try:
            await asyncio.to_thread(
                subprocess.run,
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=_TASKKILL_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            log.warning("Process-tree termination timed out for pid %s; killing SSH root", pid)
            _kill_process(proc)
        except OSError:
            _kill_process(proc)
    elif sys.platform != "win32":
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except OSError:
            _kill_process(proc)
    try:
        await asyncio.wait_for(proc.wait(), timeout=grace)
        return
    except (TimeoutError, asyncio.TimeoutError):
        pass
    if sys.platform != "win32":
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except OSError:
            _kill_process(proc)
    else:
        _kill_process(proc)
    try:
        await asyncio.wait_for(proc.wait(), timeout=_KILL_WAIT)
    except (TimeoutError, asyncio.TimeoutError):
        log.warning("Process tree rooted at pid %s did not exit", pid)

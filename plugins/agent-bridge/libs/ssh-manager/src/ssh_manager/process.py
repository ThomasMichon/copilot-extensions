"""Platform-specific process isolation for SSH client trees."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class _ProcessCleanup:
    process: asyncio.subprocess.Process
    close: Callable[[], Awaitable[None]]
    task: asyncio.Task | None = None


_CLEANUPS: dict[int, _ProcessCleanup] = {}


def register_process_cleanup(
    process: asyncio.subprocess.Process,
    close: Callable[[], Awaitable[None]],
) -> None:
    _CLEANUPS[id(process)] = _ProcessCleanup(process, close)


async def run_process_cleanup(process: asyncio.subprocess.Process) -> None:
    entry = _CLEANUPS.get(id(process))
    if entry is None:
        return
    if entry.task is None:
        entry.task = asyncio.create_task(entry.close())
    try:
        await asyncio.shield(entry.task)
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
    grace: float = 5.0,
) -> None:
    """Terminate an SSH root and the ProxyCommand descendants it spawned."""
    try:
        await _terminate_ssh_process_tree(proc, grace=grace)
    finally:
        await run_process_cleanup(proc)


async def _terminate_ssh_process_tree(
    proc: asyncio.subprocess.Process, *, grace: float,
) -> None:
    if proc.returncode is not None:
        return
    pid = getattr(proc, "pid", None)
    if not isinstance(pid, int) or pid <= 0:
        _kill_process(proc)
        await proc.wait()
        return
    if sys.platform == "win32":
        try:
            await asyncio.to_thread(
                subprocess.run,
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except OSError:
            _kill_process(proc)
    else:
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
        await asyncio.wait_for(proc.wait(), timeout=2.0)
    except (TimeoutError, asyncio.TimeoutError):
        pass

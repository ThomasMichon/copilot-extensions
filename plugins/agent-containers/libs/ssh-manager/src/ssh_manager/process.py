"""Platform-specific process isolation for SSH client trees."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
from typing import Any


def _kill_process(proc: asyncio.subprocess.Process) -> None:
    try:
        proc.kill()
    except ProcessLookupError:
        pass


def ssh_subprocess_kwargs(**kwargs: Any) -> dict[str, Any]:
    """Return kwargs that keep an SSH process tree invisible and isolated.

    ``CREATE_NO_WINDOW`` suppresses the direct ``ssh.exe`` child, but an SSH
    ``ProxyCommand`` can still start console-subsystem descendants that invoke
    Windows Default Terminal. A hidden new console gives the whole tree a
    console to inherit without presenting a visible window.
    """
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
        kwargs["startupinfo"] = startupinfo
    else:
        kwargs["start_new_session"] = True
    return kwargs


async def terminate_ssh_process_tree(
    proc: asyncio.subprocess.Process,
    *,
    grace: float = 5.0,
) -> None:
    """Terminate an SSH root and the ProxyCommand descendants it spawned."""
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

"""Owned namespace-reader spawn receipts and unchanged execution deadlines."""

from __future__ import annotations

import ctypes
import logging
import subprocess
import sys
import time
from concurrent.futures import Future

from agent_procutil import no_window_flags
from agent_procutil.metrics import ProcessIdentity

log = logging.getLogger("agent-bridge")


def _windows_created_at(process: subprocess.Popen[str]) -> float:
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_times = kernel32.GetProcessTimes
    get_times.argtypes = [
        wintypes.HANDLE,
        *([ctypes.POINTER(wintypes.FILETIME)] * 4),
    ]
    get_times.restype = wintypes.BOOL
    created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
    if not get_times(
        int(process._handle),
        ctypes.byref(created),
        ctypes.byref(exited),
        ctypes.byref(kernel),
        ctypes.byref(user),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
    return (ticks - 116444736000000000) / 10000000.0


def capture_identity(process: subprocess.Popen[str]) -> ProcessIdentity:
    """Read identity before any wait/poll can reap this owned child."""
    try:
        if sys.platform == "win32":
            created_at = _windows_created_at(process)
        else:
            import psutil

            created_at = psutil.Process(process.pid).create_time()
    except Exception:
        log.warning("Owned namespace reader creation identity unavailable", exc_info=True)
        created_at = None
    return ProcessIdentity(process.pid, created_at)


class OwnedReaderCommand:
    """A caller-owned command; receipt publication precedes communicate/reap."""

    def __init__(self) -> None:
        self.receipt: Future[ProcessIdentity | None] = Future()
        self.receipt.set_running_or_notify_cancel()
        self.wall_seconds: float | None = None

    def run(self, cmd: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        started = time.monotonic()
        try:
            with subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=no_window_flags(),
            ) as process:
                deadline = time.monotonic() + timeout
                self.receipt.set_result(capture_identity(process))
                try:
                    stdout, stderr = process.communicate(
                        timeout=max(0.0, deadline - time.monotonic()),
                    )
                except subprocess.TimeoutExpired as error:
                    # Match subprocess.run's existing direct-child timeout cleanup.
                    process.kill()
                    if sys.platform == "win32":
                        error.stdout, error.stderr = process.communicate()
                    else:
                        process.wait()
                    raise
                except BaseException:
                    process.kill()
                    raise
                return subprocess.CompletedProcess(cmd, process.returncode, stdout, stderr)
        finally:
            if not self.receipt.done():
                self.receipt.set_result(None)
            self.wall_seconds = time.monotonic() - started

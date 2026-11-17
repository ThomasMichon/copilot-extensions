"""Deadline-bounded exit evidence from the same native termination handle."""

from __future__ import annotations

import logging
import math
import select

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
SYNCHRONIZE = 0x00100000

log = logging.getLogger("zdd")


def validate_timeout(timeout: float | None) -> None:
    if timeout is not None and (
        type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 <= timeout <= 30
    ):
        log.error("Native exit timeout must be finite and between zero and 30 seconds")
        raise ValueError("exit_timeout must be finite and between zero and 30 seconds")


def wait_posix(fd: int, timeout: float) -> bool:
    poller = select.poll()
    poller.register(fd, select.POLLIN)
    events = poller.poll(int(timeout * 1000))
    if any(flags & (select.POLLERR | select.POLLNVAL) for _, flags in events):
        raise OSError("native process exit wait failed")
    return any(flags & (select.POLLIN | select.POLLHUP) for _, flags in events)


def wait_windows(kernel32, handle, timeout: float) -> bool:
    from ctypes import wintypes

    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    result = kernel32.WaitForSingleObject(handle, int(timeout * 1000))
    if result not in (0, 0x102):
        raise OSError("native process exit wait failed")
    return result == 0


def pid_alive_windows(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid)
        if handle:
            try:
                return not wait_windows(kernel32, handle, 0)
            finally:
                kernel32.CloseHandle(handle)
        return ctypes.get_last_error() != 87  # ERROR_INVALID_PARAMETER proves no such PID.
    except (OSError, ctypes.ArgumentError):
        return True

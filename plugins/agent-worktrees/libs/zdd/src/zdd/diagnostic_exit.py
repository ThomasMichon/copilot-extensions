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
    return bool(select.select([fd], [], [], timeout)[0])


def wait_windows(kernel32, handle, timeout: float) -> bool:
    from ctypes import wintypes

    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    result = kernel32.WaitForSingleObject(handle, int(timeout * 1000))
    if result not in (0, 0x102):
        raise OSError("native process exit wait failed")
    return result == 0

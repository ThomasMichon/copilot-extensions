"""Non-signaling native exit evidence captured before graceful control."""

from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Callable

from . import diagnostic_exit, diagnostics


class ProcessExitReference:
    """A captured process reference, not installation or retirement authority.

    Use ``open_process`` before requesting graceful shutdown. The caller still
    validates exact service ownership and authorizes that control operation.
    Waiting never looks up a PID again and never signals a process.
    """

    def __init__(
        self,
        pid: int,
        start_time: str,
        wait: Callable[[float], bool],
        close: Callable[[], None],
    ) -> None:
        self.pid = pid
        self.start_time = start_time
        self._wait = wait
        self._close = close
        self._closed = False
        self._creating_pid = os.getpid()
        self._lock = threading.Lock()

    def _assert_process(self) -> None:
        if os.getpid() != self._creating_pid:
            raise RuntimeError("process exit reference belongs to another process")

    def wait(self, timeout: float) -> bool:
        """Confirm native exit within a finite zero-to-30-second budget."""
        self._assert_process()
        diagnostic_exit.validate_timeout(timeout)
        if timeout is None:
            raise ValueError("exit timeout must be finite")
        deadline = time.monotonic() + timeout
        if not self._lock.acquire(timeout=timeout):
            raise TimeoutError("process exit reference is busy")
        try:
            if self._closed:
                raise RuntimeError("process exit reference is closed")
            return self._wait(max(0.0, deadline - time.monotonic()))
        finally:
            self._lock.release()

    def close(self) -> None:
        self._assert_process()
        with self._lock:
            if not self._closed:
                self._close()
                self._closed = True

    def __enter__(self) -> ProcessExitReference:
        self._assert_process()
        with self._lock:
            if self._closed:
                raise RuntimeError("process exit reference is closed")
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def _open_linux(pid: int, token: str) -> ProcessExitReference:
    from .singleton_linux import _open_pidfd

    fd = _open_pidfd(pid)
    reference = ProcessExitReference(
        pid, token, lambda timeout: diagnostic_exit.wait_posix(fd, timeout),
        lambda: os.close(fd),
    )
    try:
        if diagnostics.process_start_time(pid) != token:
            raise ProcessLookupError("process identity does not match captured reference")
        if reference.wait(0):
            raise ProcessLookupError("captured process exited before identity validation")
        return reference
    except BaseException:
        reference.close()
        raise


def _open_windows(pid: int, token: str) -> ProcessExitReference:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(
        diagnostic_exit.PROCESS_QUERY_LIMITED_INFORMATION | diagnostic_exit.SYNCHRONIZE,
        False, pid,
    )
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())

    def close() -> None:
        if not kernel32.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())

    reference = ProcessExitReference(
        pid, token, lambda timeout: diagnostic_exit.wait_windows(kernel32, handle, timeout), close,
    )
    try:
        creation, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not kernel32.GetProcessTimes(
            handle, ctypes.byref(creation), ctypes.byref(exited),
            ctypes.byref(kernel), ctypes.byref(user),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        actual = str((creation.dwHighDateTime << 32) | creation.dwLowDateTime)
        if actual != token:
            raise ProcessLookupError("process identity does not match captured reference")
        if reference.wait(0):
            raise ProcessLookupError("captured process exited before identity validation")
        return reference
    except BaseException:
        reference.close()
        raise


def open_process(pid: int, expected_start_time: str) -> ProcessExitReference:
    """Capture one live identity or refuse; unsupported platforms never guess."""
    if (
        type(pid) is not int
        or not 0 < pid <= 0xFFFFFFFF
        or not isinstance(expected_start_time, str)
        or not 0 < len(expected_start_time) <= 64
        or not expected_start_time.isascii()
        or not expected_start_time.isdecimal()
    ):
        raise ValueError("process identity requires a positive PID and ASCII decimal start token")
    if sys.platform == "win32":
        return _open_windows(pid, expected_start_time)
    if sys.platform == "linux":
        return _open_linux(pid, expected_start_time)
    raise NotImplementedError("native process exit references require Windows or Linux")

"""Non-signaling native exit evidence captured before graceful control."""

from __future__ import annotations

import errno
import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

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
            # Clamped: `deadline - now` can exceed the request by float rounding
            # when the monotonic clock has not advanced (coarse on Windows).
            return self._wait(min(float(timeout), max(0.0, deadline - time.monotonic())))
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


def _open_linux(pid: int, token: str, *, allow_exited: bool = False) -> ProcessExitReference:
    from .singleton_linux import _open_pidfd

    fd = _open_pidfd(pid)
    reference = ProcessExitReference(
        pid, token, lambda timeout: diagnostic_exit.wait_posix(fd, timeout),
        lambda: os.close(fd),
    )
    try:
        if diagnostics.process_start_time(pid) != token:
            raise ProcessLookupError("process identity does not match captured reference")
        if reference.wait(0) and not allow_exited:
            raise ProcessLookupError("captured process exited before identity validation")
        return reference
    except BaseException:
        reference.close()
        raise


def _windows_process(pid: int) -> tuple:
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
        error = ctypes.get_last_error()
        if error == 87:
            raise ProcessLookupError(errno.ESRCH, "native process no longer exists")
        raise ctypes.WinError(error)

    def close() -> None:
        if not kernel32.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())

    try:
        creation, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if not kernel32.GetProcessTimes(
            handle, ctypes.byref(creation), ctypes.byref(exited),
            ctypes.byref(kernel), ctypes.byref(user),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        actual = str((creation.dwHighDateTime << 32) | creation.dwLowDateTime)
        return kernel32, handle, actual, close
    except BaseException:
        close()
        raise


def _open_windows(pid: int, token: str, *, allow_exited: bool = False) -> ProcessExitReference:
    kernel32, handle, actual, close = _windows_process(pid)
    reference = ProcessExitReference(
        pid, token, lambda timeout: diagnostic_exit.wait_windows(kernel32, handle, timeout), close,
    )
    try:
        if actual != token:
            raise ProcessLookupError("process identity does not match captured reference")
        if reference.wait(0) and not allow_exited:
            raise ProcessLookupError("captured process exited before identity validation")
        return reference
    except BaseException:
        reference.close()
        raise


def _validate_identity(pid: int, expected_start_time: str) -> None:
    if (
        type(pid) is not int
        or not 0 < pid <= 0xFFFFFFFF
        or not isinstance(expected_start_time, str)
        or not 0 < len(expected_start_time) <= 64
        or not expected_start_time.isascii()
        or not expected_start_time.isdecimal()
    ):
        raise ValueError("process identity requires a positive PID and ASCII decimal start token")


def open_process(
    pid: int, expected_start_time: str, *, allow_exited: bool = False,
) -> ProcessExitReference:
    """Capture one identity; allow an exited object only for inactive observation.

    Retirement still requires a live reference captured before control; the
    opt-in is never a replacement for that retained reference.
    """
    if type(allow_exited) is not bool:
        raise ValueError("allow_exited requires a boolean")
    _validate_identity(pid, expected_start_time)
    if sys.platform == "win32":
        if allow_exited:
            return _open_windows(pid, expected_start_time, allow_exited=True)
        return _open_windows(pid, expected_start_time)
    if sys.platform == "linux":
        if allow_exited:
            return _open_linux(pid, expected_start_time, allow_exited=True)
        return _open_linux(pid, expected_start_time)
    raise NotImplementedError("native process exit references require Windows or Linux")


def _observe_linux(pid: int, token: str) -> bool:
    from .singleton_linux import _open_pidfd

    try:
        fd = _open_pidfd(pid)
    except ProcessLookupError:
        return True
    try:
        if diagnostic_exit.wait_posix(fd, 0):
            return True
        try:
            with Path(f"/proc/{pid}/stat").open("rb") as handle:
                raw = handle.read(65_537)
        except FileNotFoundError:
            if diagnostic_exit.wait_posix(fd, 0):
                return True
            raise ProcessLookupError("live process reference has unavailable birth evidence") from None
        rparen = raw.rfind(b")")
        fields = raw[rparen + 1:].split() if rparen >= 0 else []
        if (
            len(raw) > 65_536 or not raw.startswith(f"{pid} (".encode("ascii"))
            or len(fields) < 20 or not fields[19].isdigit()
        ):
            raise ValueError("native process birth evidence is malformed")
        return fields[19].decode("ascii") != token or diagnostic_exit.wait_posix(fd, 0)
    finally:
        os.close(fd)


def _observe_windows(pid: int, token: str) -> bool:
    try:
        kernel32, handle, actual, close = _windows_process(pid)
    except ProcessLookupError:
        return True
    try:
        return actual != token or diagnostic_exit.wait_windows(kernel32, handle, 0)
    finally:
        close()


def observe_original_exit(pid: int, expected_start_time: str) -> bool:
    """Read-only crash recovery: prove the original identity is no longer live.

    Unlike ``open_process``, this can observe an already-exited or reused PID.
    Denied, malformed and unsupported native evidence raises, never means exit.
    It confers no control, signaling, writer, installation or retirement authority.
    """
    _validate_identity(pid, expected_start_time)
    if sys.platform == "win32":
        return _observe_windows(pid, expected_start_time)
    if sys.platform == "linux":
        return _observe_linux(pid, expected_start_time)
    raise NotImplementedError("native original-exit observation requires Windows or Linux")

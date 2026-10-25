"""Exact native retirement witness for the lane's single Bridge event worker."""

from __future__ import annotations

import errno
import os
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class _Witness:
    check: Callable[[], bool]
    release: Callable[[], None]


def _capture() -> _Witness:
    native = getattr(threading.current_thread(), "_handle", None)
    if native is not None and callable(getattr(native, "is_done", None)):
        return _Witness(native.is_done, lambda: None)
    return _capture_legacy()


def _capture_legacy() -> _Witness:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentThreadId.restype = wintypes.DWORD
        kernel.OpenThread.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenThread.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenThread(0x00100000, False, kernel.GetCurrentThreadId())
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())

        def check():
            result = kernel.WaitForSingleObject(handle, 0)
            if result not in (0, 258):
                raise ctypes.WinError(ctypes.get_last_error())
            return result == 0

        def release():
            if not kernel.CloseHandle(handle):
                raise ctypes.WinError(ctypes.get_last_error())

        return _Witness(check, release)
    if sys.platform.startswith("linux"):
        # The open proc descriptor stays bound to this task, not a reused TID.
        fd = os.open("/proc/thread-self/stat", os.O_RDONLY | os.O_CLOEXEC)

        def check():
            try:
                if not os.pread(fd, 4096, 0):
                    raise RuntimeError("Bridge worker native identity unavailable")
            except OSError as exc:
                if exc.errno in (errno.ESRCH, errno.ENOENT):
                    return True
                raise
            return False

        return _Witness(check, lambda: os.close(fd))
    if sys.platform == "darwin":
        import ctypes

        kernel = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        kernel.mach_thread_self.restype = ctypes.c_uint
        kernel.mach_port_type.argtypes = (
            ctypes.c_uint, ctypes.c_uint, ctypes.POINTER(ctypes.c_uint),
        )
        kernel.mach_port_deallocate.argtypes = (ctypes.c_uint, ctypes.c_uint)
        task = ctypes.c_uint.in_dll(kernel, "mach_task_self_").value
        port = kernel.mach_thread_self()

        def check():
            kind = ctypes.c_uint()
            if kernel.mach_port_type(task, port, ctypes.byref(kind)):
                raise RuntimeError("Bridge worker native port unavailable")
            return bool(kind.value & 0x00100000)  # Held dead-name right, never a reused port.

        def release():
            if kernel.mach_port_deallocate(task, port):
                raise RuntimeError("Bridge worker native port release failed")

        return _Witness(check, release)
    raise RuntimeError("Bridge worker native retirement is unsupported")


class BridgeEventWorker:
    """Retain one worker and its native witness even if Thread.join is poisoned."""

    def __init__(self) -> None:
        self.thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._witness: _Witness | None = None
        self._error: BaseException | None = None
        self._retired = False
        self._lock = threading.Lock()

    def run(self, target: Callable[[], None]) -> None:
        try:
            self._witness = _capture()
        except BaseException as exc:
            self._error = exc
        finally:
            self._ready.set()
        if self._error is None:
            target()

    def retired(self) -> bool:
        with self._lock:
            if self._retired:
                return True
            if not self._ready.is_set():
                return False
            if self._error is not None:
                raise self._error
            if self._witness is None:
                raise RuntimeError("Bridge worker retirement witness missing")
            if not self._witness.check():
                return False
            self._retired = True
            self._witness.release()
            return True

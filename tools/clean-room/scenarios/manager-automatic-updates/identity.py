"""Pinned process lifetime and bounded assertions for fixture-owned workers."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import select
import signal
import time
from typing import Callable, TypeVar

T = TypeVar("T")


def until(action: Callable[[], T], accept: Callable[[T], bool], *, timeout: float, label: str) -> T:
    deadline = time.monotonic() + timeout
    while True:
        value = action()
        if accept(value):
            return value
        if time.monotonic() >= deadline:
            raise TimeoutError(f"qualification deadline: {label}")
        time.sleep(0.1)


class ProcessPin:
    """A kernel handle, never a name/PID-only termination target."""

    def __init__(self, pid: int, *, terminate_control: bool = False) -> None:
        self.pid = pid
        self.terminate_control = terminate_control
        self.handle: int | None = None
        self.fd: int | None = None
        if os.name == "nt":
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.restype = ctypes.c_void_p
            kernel.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
            kernel.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
            kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
            kernel.TerminateProcess.argtypes = (ctypes.c_void_p, ctypes.c_uint)
            self.kernel = kernel
            access = 0x100000 | 0x1000 | (0x1 if terminate_control else 0)
            self.handle = kernel.OpenProcess(access, False, pid)
            if not self.handle:
                raise OSError(ctypes.get_last_error(), "cannot pin fixture worker process")
        elif hasattr(os, "pidfd_open"):
            self.fd = os.pidfd_open(pid)
        else:
            raise RuntimeError("qualification requires pidfd or a native Windows process handle")

    def exited(self) -> bool:
        if self.fd is not None:
            return bool(select.select([self.fd], [], [], 0)[0])
        if self.handle is not None:
            result = self.kernel.WaitForSingleObject(self.handle, 0)
            if result not in (0, 258):
                raise OSError(ctypes.get_last_error(), "cannot observe pinned fixture process")
            return result == 0
        raise RuntimeError("process pin is closed")

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        if self.handle is not None:
            self.kernel.CloseHandle(self.handle)
            self.handle = None

    def request_term(self) -> None:
        if self.fd is None or not hasattr(signal, "pidfd_send_signal"):
            raise RuntimeError("native signal qualification requires identity-pinned pidfd signaling")
        signal.pidfd_send_signal(self.fd, signal.SIGTERM)

    def stop_contained_control(self) -> None:
        if not self.terminate_control or self.handle is None:
            raise RuntimeError("force cleanup is restricted to the pinned fixture sleep control")
        if not self.exited() and not self.kernel.TerminateProcess(self.handle, 1):
            raise OSError(ctypes.get_last_error(), "pinned fixture control cleanup failed")


def read_event(path: Path) -> dict:
    import json

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}

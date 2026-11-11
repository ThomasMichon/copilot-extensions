"""Pinned process lifetime and bounded assertions for fixture-owned workers."""

from __future__ import annotations

import ctypes
from collections import deque
import math
import os
from pathlib import Path
import signal
import sys
import time
from typing import Callable, TypeVar

T = TypeVar("T")


def failure_details(error: BaseException) -> dict:
    """Keep bounded causal stacks without locals or source-file inspection."""
    messages = []
    seen = set()
    current: BaseException | None = error
    truncated = False
    message = ""
    for index in range(8):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        try:
            text = str(current)
        except BaseException:
            text = "<exception message unavailable>"
        truncated |= len(text) > 4096
        text = text[:4096]
        if index == 0:
            message = text
        frames: deque[str] = deque(maxlen=20)
        trace = current.__traceback__
        count = 0
        while trace is not None:
            frame = trace.tb_frame
            frames.append(
                f'  File "{frame.f_code.co_filename[:1024]}", '
                f"line {trace.tb_lineno}, in {frame.f_code.co_name[:256]}\n"
            )
            count += 1
            trace = trace.tb_next
        truncated |= count > 20
        messages.append("".join(frames) + f"{type(current).__name__}: {text}\n")
        cause = current.__cause__
        if cause is not None:
            messages.append("Direct cause of the preceding exception:\n")
        elif not current.__suppress_context__:
            cause = current.__context__
            if cause is not None:
                messages.append("Context of the preceding exception:\n")
        current = cause
    truncated |= current is not None
    stack = "".join(messages)
    return {
        "type": type(error).__name__, "message": message,
        "traceback": stack[:16384], "traceback_available": error.__traceback__ is not None,
        "truncated": truncated or len(stack) > 16384,
    }


def until(action: Callable[[], T], accept: Callable[[T], bool], *, timeout: float, label: str) -> T:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("qualification timeout must be finite and positive")
    deadline = time.monotonic() + timeout
    inherited = os.environ.get("APERTURE_TEST_DEADLINE")
    if inherited is not None:
        absolute = float(inherited)
        if not math.isfinite(absolute):
            raise ValueError("inherited qualification deadline must be finite")
        deadline = min(deadline, time.monotonic() + absolute - time.time())
    while True:
        if time.monotonic() >= deadline:
            raise TimeoutError(f"qualification deadline: {label}")
        value = action()
        if time.monotonic() >= deadline:
            raise TimeoutError(f"qualification deadline: {label}")
        accepted = accept(value)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"qualification deadline: {label}")
        if accepted:
            return value
        time.sleep(min(0.1, remaining))


class ProcessPin:
    """A kernel handle, never a name/PID-only termination target."""

    def __init__(
        self, pid: int, *, terminate_control: bool = False, expected_birth: str | None = None,
    ) -> None:
        self.pid = pid
        self.terminate_control = False
        self.identity_verified = False
        self.handle: int | None = None
        self.fd: int | None = None
        if os.name == "nt":
            if terminate_control and (
                not isinstance(expected_birth, str) or not expected_birth.startswith("windows:")
            ):
                raise RuntimeError("control pin requires its original creation identity")
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
            if expected_birth is not None:
                from agent_procutil._daemon_instance_identity import _windows_birth

                try:
                    if _windows_birth(self.handle) != expected_birth:
                        raise RuntimeError("fixture control process identity changed")
                except BaseException:
                    self.close()
                    raise
                self.identity_verified = True
                self.terminate_control = terminate_control
        elif sys.platform.startswith("linux"):
            from agent_procutil._daemon_instance_identity import birth_identity
            from agent_procutil._linux_pidfd import open_pidfd

            self.fd = open_pidfd(pid)
            if expected_birth is not None:
                try:
                    if birth_identity(pid) != expected_birth or self.exited():
                        raise RuntimeError("fixture worker process identity changed")
                except BaseException:
                    self.close()
                    raise
                self.identity_verified = True
        else:
            raise RuntimeError("qualification requires pidfd or a native Windows process handle")

    def exited(self) -> bool:
        if self.fd is not None:
            from agent_procutil._caller_owned import _pidfd_exited

            return _pidfd_exited(self.fd)
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
            if not self.kernel.CloseHandle(self.handle):
                raise OSError(ctypes.get_last_error(), "cannot close fixture process pin")
            self.handle = None

    def request_term(self) -> None:
        if self.fd is None or not self.identity_verified:
            raise RuntimeError("native signal qualification requires the original authenticated birth")
        from agent_procutil._linux_pidfd import send_pidfd_signal

        send_pidfd_signal(self.fd, signal.SIGTERM)

    def stop_contained_control(self) -> None:
        if not self.terminate_control or not self.identity_verified or self.handle is None:
            raise RuntimeError("force cleanup is restricted to the pinned fixture sleep control")
        if not self.exited() and not self.kernel.TerminateProcess(self.handle, 1):
            raise OSError(ctypes.get_last_error(), "pinned fixture control cleanup failed")


def read_event(path: Path) -> dict:
    import json

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}

"""Identity-pinned external retirement; application owners supply authority."""
from __future__ import annotations

import ctypes
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

from ._daemon_instance_identity import _windows_birth, birth_identity


class RetirementRefused(RuntimeError):
    """The original process cannot be pinned, paused or observed exiting."""


def _linux_threads_stopped(process: Path) -> bool:
    try:
        tasks = set((process / "task").iterdir())
        if not tasks:
            return False
        for task in tasks:
            stat = (task / "stat").read_text(encoding="ascii", errors="surrogateescape")
            if stat[stat.rindex(")") + 2:].split()[0] not in {"T", "t"}:
                return False
        return tasks == set((process / "task").iterdir())
    except FileNotFoundError:
        return False


class PinnedProcess:
    """Pin one original identity and pause it while its owner closes a tree.

    Closing a failed transaction resumes a still-live paused process. No process
    discovery, descendant ownership, PID-only signaling or group kill lives here.
    """

    def __init__(self, pid: int, birth: str):
        if type(pid) is not int or not 0 < pid < 2**32:
            raise ValueError("Invalid native process PID")
        if pid == os.getpid():
            raise RetirementRefused("External retirement cannot target its caller")
        self.pid = pid
        self.birth = birth
        self._process = None
        self._fd = None
        self._paused = False
        self._exited = False
        try:
            if os.name == "nt":
                from ctypes import wintypes
                from .daemon_broker import WindowsDaemonProcess, _kernel32

                kernel = _kernel32()
                kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                kernel.OpenProcess.restype = wintypes.HANDLE
                handle = kernel.OpenProcess(0x0800 | 0x1000 | 0x100000 | 1, False, pid)
                if not handle:
                    error = ctypes.get_last_error()
                    if error in (87, 1168):
                        raise ProcessLookupError(pid)
                    raise ctypes.WinError(error)
                self._process = WindowsDaemonProcess.from_handle(handle, pid, ["runtime-retirement"])
                if _windows_birth(self._process._handle) != birth:
                    raise RetirementRefused(f"Process identity changed (pid={pid})")
            elif sys.platform.startswith("linux"):
                from ._linux_pidfd import open_pidfd

                self._fd = open_pidfd(pid)
                if birth_identity(pid) != birth:
                    raise RetirementRefused(f"Process identity changed (pid={pid})")
            else:
                raise RetirementRefused("External pinned retirement requires Windows or Linux")
        except BaseException:
            self.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _windows_pause(self, resume: bool) -> None:
        from ctypes import wintypes

        native = ctypes.WinDLL("ntdll")
        operation = native.NtResumeProcess if resume else native.NtSuspendProcess
        operation.argtypes = [wintypes.HANDLE]
        operation.restype = ctypes.c_long
        if operation(self._process._handle) != 0 and not self.exited():
            raise RetirementRefused(f"Native process pause/resume failed (pid={self.pid})")

    def _signal(self, value: int) -> None:
        from ._linux_pidfd import send_pidfd_signal

        if self._fd is None:
            raise RetirementRefused("Process pin was released")
        send_pidfd_signal(self._fd, value)

    def exited(self, timeout: float = 0) -> bool:
        if self._exited:
            return True
        if self._process is not None:
            try:
                self._process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                return False
            self._exited = True
        elif self._fd is not None:
            observer = select.poll()
            observer.register(self._fd, select.POLLIN)
            self._exited = bool(observer.poll(max(0, int(timeout * 1000))))
        else:
            raise RetirementRefused("Process pin was released")
        return self._exited

    def pause(self, timeout: float = 2) -> None:
        if self.exited() or self._paused:
            return
        if os.name == "nt":
            self._windows_pause(False)
            self._paused = True
            return
        status = Path(f"/proc/{self.pid}/stat")
        before = status.read_text(encoding="ascii", errors="surrogateescape")
        if before[before.rindex(")") + 2:].split()[0] in {"T", "t"}:
            raise RetirementRefused(f"Process was already stopped (pid={self.pid})")
        self._signal(signal.SIGSTOP)
        self._paused = True
        deadline = time.monotonic() + timeout
        while not self.exited():
            if _linux_threads_stopped(status.parent):
                return
            if time.monotonic() >= deadline:
                raise RetirementRefused(f"Process pause was not observed (pid={self.pid})")
            time.sleep(.005)

    def terminate(self, timeout: float = 5) -> None:
        if self.exited():
            return
        if self._process is not None:
            self._process.terminate()
        else:
            self._signal(signal.SIGKILL)
        if not self.exited(timeout):
            raise RetirementRefused(f"Process exit was not confirmed (pid={self.pid})")

    def close(self) -> None:
        if self._paused and not self.exited():
            try:
                if os.name == "nt":
                    self._windows_pause(True)
                else:
                    self._signal(signal.SIGCONT)
            except (OSError, RetirementRefused):
                if not self.exited():
                    # Preserve the actual pin for the caller's cleanup decision.
                    error = RetirementRefused(f"Process resume is unconfirmed (pid={self.pid})")
                    error.process = self
                    raise error from None
            self._paused = False
        if self._process is not None:
            self._process.close()
            self._process = None
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None

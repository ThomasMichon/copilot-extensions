"""Caller-owned continuous binary stdin, without a blocking writer thread."""

from __future__ import annotations

import errno
from contextlib import ExitStack
import os
from pathlib import Path
import select
import subprocess
import sys
import time
from typing import Any


class CommandPipe:
    """Reuse native Job/private-session custody for one headless command.

    All operations belong to the calling worker. ``deadline`` can be shortened
    by its shutdown owner. Failed cleanup retains this object and its handles.
    """

    def __init__(self) -> None:
        self.deadline = float("inf")
        self.process: Any = None
        self.job: Any = None
        self._lifetime: int | None = None
        self._control: int | None = None
        self._events: int | None = None
        self._frames = bytearray()
        self._stdin: Any = None
        self.failure: BaseException | None = None
        self.closed = False

    def _remaining(self, deadline: float) -> float:
        remaining = min(deadline, self.deadline) - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("command pipe deadline")
        return remaining

    def start(self, argv: list[str], cwd: str, env: dict[str, str]) -> None:
        """Establish native custody and nonblocking stdin within two seconds."""
        deadline = min(time.monotonic() + 2, self.deadline)
        self._remaining(deadline)
        if os.name == "nt":
            from ._windows_caller_owned import spawn

            from .owned_process import OwnedCleanupError

            try:
                self.process, self.job = spawn(
                    argv, command_deadline=deadline, cleanup_deadline=deadline + 1.5,
                    stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    cwd=cwd, env=env, inherit_job_scopes=False, publish_job_scopes=False,
                )
            except OwnedCleanupError as error:
                self.failure = error
                self.process, self.job = error.process, error.job
                self._stdin = getattr(self.process, "stdin", None)
                raise
            self._stdin = self.process.stdin
            self._windows_nonblocking()
        elif sys.platform == "linux":
            self._start_linux(argv, cwd, env, deadline)
        else:
            raise RuntimeError("continuous command custody is unsupported")
        self._remaining(deadline)

    def _windows_nonblocking(self) -> None:
        import ctypes
        from ctypes import wintypes
        import msvcrt

        native: Any = ctypes
        kernel = native.WinDLL("kernel32", use_last_error=True)
        kernel.SetNamedPipeHandleState.argtypes = [
            wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
        ]
        kernel.SetNamedPipeHandleState.restype = wintypes.BOOL
        mode = wintypes.DWORD(1)  # PIPE_NOWAIT; never abandon a synchronous write.
        if not kernel.SetNamedPipeHandleState(
            getattr(msvcrt, "get_osfhandle")(self._stdin.fileno()), ctypes.byref(mode), None, None,
        ):
            raise native.WinError(native.get_last_error())

    def _start_linux(
        self, argv: list[str], cwd: str, env: dict[str, str], deadline: float,
    ) -> None:
        from ._caller_owned import _lifetime_lock, _lifetime_writers
        from .owned_process import require_caller_owned_support

        require_caller_owned_support()
        with ExitStack() as initial:
            def pipe() -> tuple[int, int]:
                reader, writer = os.pipe()
                initial.callback(os.close, reader)
                initial.callback(os.close, writer)
                return reader, writer

            lifetime, writer = pipe()
            events, reply = pipe()
            gate, control = pipe()
            initial.pop_all()
        descriptors = [lifetime, writer, events, reply, gate, control]
        try:
            with _lifetime_lock:
                _lifetime_writers.add(writer)
                self._lifetime = writer
            self._events, self._control = events, control
            bootstrap = (
                "import sys;sys.path.insert(0,sys.argv.pop(1));"
                "from agent_procutil._interactive_session import main;main(asynchronous=True)"
            )
            # headless-guard: allow private-session guardian with dedicated null/pipe stdio
            self.process = subprocess.Popen(
                [sys.executable, "-I", "-B", "-S", "-u", "-c", bootstrap,
                 str(Path(__file__).resolve().parents[1]),
                 str(lifetime), str(reply), str(gate), str(os.getpid()), "-1", *argv],
                cwd=cwd, env=env, pass_fds=(lifetime, reply, gate),
                start_new_session=True, stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, bufsize=0,
            )
            self._stdin = self.process.stdin
            for descriptor in (lifetime, reply, gate):
                os.close(descriptor)
                descriptors.remove(descriptor)
            getattr(os, "set_blocking")(events, False)
            getattr(os, "set_blocking")(self._stdin.fileno(), False)
            if self._frame(deadline) != b"ready":
                raise RuntimeError("command guardian unavailable")
            os.write(control, b"g")
            native = self._frame(deadline)
            if not native.startswith(b"native ") or not native[7:].isdigit():
                raise RuntimeError("command identity unavailable")
            os.write(control, b"g")
            if self._frame(deadline) != b"running":
                raise RuntimeError("command admission unavailable")
        except BaseException:
            for descriptor in descriptors:
                if descriptor not in (self._lifetime, self._control, self._events):
                    os.close(descriptor)
            raise

    def _frame(self, deadline: float) -> bytes:
        assert self._events is not None
        while b"\n" not in self._frames:
            if not select.select([self._events], [], [], self._remaining(deadline))[0]:
                raise TimeoutError("command guardian deadline")
            chunk = os.read(self._events, 256)
            if not chunk or len(self._frames) + len(chunk) > 256:
                raise RuntimeError("command guardian framing")
            self._frames.extend(chunk)
        frame, _, tail = self._frames.partition(b"\n")
        self._frames[:] = tail
        return bytes(frame)

    def write(self, record: bytes) -> None:
        """Complete one bounded record or fail its generation within one second."""
        deadline = time.monotonic() + 1
        remaining = memoryview(record)
        while remaining:
            self._remaining(deadline)
            try:
                written = os.write(self._stdin.fileno(), remaining[:4096])
                if written == 0:
                    time.sleep(min(.005, self._remaining(deadline)))
                    continue
                if written < 0:
                    raise BrokenPipeError("command pipe made no progress")
                remaining = remaining[written:]
            except OSError as error:
                # The Windows CRT reports a full PIPE_NOWAIT pipe as ENOSPC,
                # without preserving a winerror on the raised OSError.
                if error.errno not in (errno.EAGAIN, errno.EWOULDBLOCK) and not (
                    os.name == "nt" and error.errno == errno.ENOSPC
                ) and (
                    os.name != "nt" or getattr(error, "winerror", None) not in (232, 536)
                ):
                    raise
                time.sleep(min(.005, self._remaining(deadline)))

    def exited(self) -> bool:
        """Observe the original child without releasing POSIX identity custody."""
        if self.process is None or self.closed:
            return False
        if os.name == "nt":
            return self.process.poll() is not None
        from .owned_process import _observe_unreaped

        return _observe_unreaped(self.process) is not None

    def close(self, deadline: float) -> None:
        """Close input, permit 250 ms exit, then confirm owned-tree retirement."""
        if self.closed:
            return
        if self._stdin is not None:
            self._stdin.close()
            self._stdin = None
        if os.name == "nt":
            if self.process is not None:
                try:
                    self.process.wait(timeout=max(0, min(.25, deadline - time.monotonic())))
                except subprocess.TimeoutExpired:
                    pass
            if self.job is not None:
                from ._windows_caller_owned import _retire_job

                _retire_job(self.job, max(0, deadline - time.monotonic()))
                self.job = None
            if self.process is not None:
                self.process.wait(timeout=max(0, deadline - time.monotonic()))
                self.process.close()
        elif os.name != "nt":
            from ._caller_owned import _lifetime_lock, _lifetime_writers
            from ._interactive_groups import confirm_session

            grace = min(deadline, time.monotonic() + .25)
            while self.process is not None and not self.exited():
                remaining = grace - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(.005, remaining))
            with _lifetime_lock:
                if self._lifetime is not None:
                    _lifetime_writers.discard(self._lifetime)
                    os.close(self._lifetime)
                    self._lifetime = None
            if self.process is not None:
                confirm_session(self.process, timeout=max(0, deadline - time.monotonic()))
        for name in ("_control", "_events"):
            descriptor = getattr(self, name)
            if descriptor is not None:
                os.close(descriptor)
                setattr(self, name, None)
        if getattr(self.failure, "cleanup_resources", ()):
            raise RuntimeError("command startup cleanup is unconfirmed")
        self.closed = True

"""Asynchronous foreground custody through the existing private-session guardian."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from typing import Callable

from ._caller_owned import _lifetime_lock, _lifetime_writers, _pidfd_pid
from ._interactive_groups import confirm_session
from ._interactive_terminal import _BUFFER, _Terminal, _drain, _private_pair, _read_pty, _stdio
from ._linux_pidfd import open_pidfd, send_pidfd_signal
from .owned_process import OwnedCleanupError

_BOOTSTRAP = (
    "import sys;sys.path.insert(0,sys.argv.pop(1));"
    "from agent_procutil._interactive_session import main;main(asynchronous=True)"
)


def require_foreground_custody() -> None:
    """Refuse unsupported composition before creating a guardian or terminal."""
    if not sys.platform.startswith("linux"):
        raise RuntimeError("Async foreground custody requires Linux pidfd support")
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Async foreground custody requires the main thread")
    if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
        raise RuntimeError("Async foreground custody requires the default SIGCHLD disposition")
    descriptor = open_pidfd(os.getpid())
    try:
        if _pidfd_pid(descriptor) != os.getpid():
            raise RuntimeError("Native pidfd identity is unavailable")
        send_pidfd_signal(descriptor, 0)
    finally:
        os.close(descriptor)


async def _ready_io(readers, writers=(), *, timeout=None):
    loop = asyncio.get_running_loop()
    ready = loop.create_future()
    readable, writable = set(), set()

    def wake(descriptor, observed):
        observed.add(descriptor)
        if not ready.done():
            ready.set_result(None)

    try:
        for descriptor in readers:
            loop.add_reader(descriptor, wake, descriptor, readable)
        for descriptor in writers:
            loop.add_writer(descriptor, wake, descriptor, writable)
        try:
            await asyncio.wait_for(ready, timeout)
        except asyncio.TimeoutError:
            pass
        return list(readable), list(writable)
    finally:
        for descriptor in readers:
            loop.remove_reader(descriptor)
        for descriptor in writers:
            loop.remove_writer(descriptor)


class AsyncForegroundProcess:
    """Retain native identity and one cancellation-shielded whole-session cleanup.

    ``retirement_started`` is conservative: this guardian supplies a settlement
    receipt, not a nonwaiting whole-scope termination request. The property stays
    false until that receipt and independent whole-session confirmation.
    """

    def __init__(self) -> None:
        self.pid: int | None = None
        self.returncode: int | None = None
        self.retirement_started = False
        self._guardian: subprocess.Popen | None = None
        self._terminal = None
        self._master = self._events = self._control = self._writer = self._native_fd = None
        self._descriptors: list[int] = []
        self._frames = bytearray()
        self._output = bytearray()
        self._completion: asyncio.Task | None = None
        self._relay_task: asyncio.Task | None = None
        self._terminal_error: BaseException | None = None
        self._requested = self._admitted = self._scope_settled = self._receipt = False
        self._starting = True
        self._native_scope: object | None = None
        self._cleanup_completed = False

    @property
    def native_scope(self) -> object | None:
        """Opaque same-owner identity, issued only after original native pinning.

        The retained guardian and validated pidfd remain owned here. This token
        has no serialized, post-loss, environment or boot identity mapping.
        """
        return self._native_scope

    def scope_settled(self, native_scope: object) -> bool | None:
        """Read completed cleanup for the original token without observing the OS.

        None means unknown, including missing/foreign identity and incomplete or
        failed cleanup. This never requests retirement or confirms a session.
        """
        if native_scope is not None and native_scope is self._native_scope:
            return True if self._cleanup_completed else None
        return None

    @classmethod
    async def start(
        cls, argv: list[str], *, cwd: str | None = None,
        env: dict[str, str] | None = None, before_exec: Callable[[], None] | None = None,
    ) -> AsyncForegroundProcess:
        if (
            not isinstance(argv, list) or not argv
            or any(not isinstance(argument, str) or "\0" in argument for argument in argv)
            or before_exec is not None and not callable(before_exec)
        ):
            raise ValueError("Foreground execution requires literal argv and callable admission")
        require_foreground_custody()
        owner = cls()
        try:
            await owner._start(argv, cwd, env, before_exec)
        except BaseException:
            await owner.terminate()
            raise
        owner._starting = False
        owner._ensure_completion()
        return owner

    async def _start(self, argv, cwd, env, before_exec):
        stdio = _stdio()
        self._terminal = _Terminal(stdio)
        slave = -1
        if self._terminal.fd is not None:
            import termios

            self._master, slave = _private_pair(os.openpty())
            self._descriptors.extend((self._master, slave))
            termios.tcsetattr(slave, termios.TCSANOW, self._terminal.attributes)
            self._terminal.size(self._master)
            os.set_blocking(self._master, False)
        with _lifetime_lock:
            lifetime, self._writer = _private_pair(os.pipe())
            self._descriptors.extend((lifetime, self._writer))
            _lifetime_writers.add(self._writer)
        self._events, reply = _private_pair(os.pipe())
        self._descriptors.extend((self._events, reply))
        gate, self._control = _private_pair(os.pipe())
        self._descriptors.extend((gate, self._control))
        inherited = (lifetime, reply, gate, *([slave] if slave >= 0 else []))
        streams = [
            slave if value == "tty" else subprocess.DEVNULL if value == "absent" else None
            for value in stdio
        ]
        # headless-guard: allow owned foreground stdio or the caller's private terminal relay
        self._guardian = subprocess.Popen(
            [
                sys.executable, "-I", "-S", "-u", "-c", _BOOTSTRAP,
                str(Path(__file__).resolve().parents[1]),
                str(lifetime), str(reply), str(gate), str(os.getpid()), str(slave), *argv,
            ],
            cwd=cwd, env=env, pass_fds=inherited, start_new_session=True,
            stdin=streams[0], stdout=streams[1], stderr=streams[2],
        )
        for descriptor in inherited:
            self._close(descriptor)
        os.set_blocking(self._events, False)
        if await asyncio.wait_for(self._frame(), 5) != b"ready":
            raise RuntimeError("Foreground guardian readiness is unconfirmed")
        os.write(self._control, b"g")
        native = await asyncio.wait_for(self._frame(), 5)
        if not native.startswith(b"native ") or not native[7:].isdigit():
            raise RuntimeError("Foreground native identity is unconfirmed")
        self.pid = int(native[7:])
        if self.pid <= 1 or self.pid == self._guardian.pid:
            raise RuntimeError("Foreground native identity is invalid")
        self._native_fd = open_pidfd(self.pid)
        self._descriptors.append(self._native_fd)
        if _pidfd_pid(self._native_fd) != self.pid:
            raise RuntimeError("Foreground native identity changed before admission")
        self._native_scope = object()
        if before_exec is not None:
            before_exec()
        self._terminal.raw()
        self._relay_task = asyncio.create_task(self._relay())
        os.write(self._control, b"g")
        self._admitted = True
        if await asyncio.wait_for(self._frame(), 5) != b"running":
            raise RuntimeError("Foreground native execution is unconfirmed")

    async def _frame(self):
        while b"\n" not in self._frames:
            await _ready_io((self._events,))
            try:
                value = os.read(self._events, 256)
            except BlockingIOError:
                continue
            if not value:
                raise RuntimeError("Foreground guardian completion is unconfirmed")
            self._frames.extend(value)
            if len(self._frames) > 256:
                raise RuntimeError("Foreground guardian frame exceeded its bound")
        frame, _, tail = self._frames.partition(b"\n")
        self._frames[:] = tail
        if len(frame) > 64:
            raise RuntimeError("Foreground guardian frame exceeded its bound")
        return bytes(frame)

    async def _relay(self):
        if self._master is None:
            return
        incoming, open_master, resize_at = bytearray(), True, 0.0
        try:
            while open_master or self._output:
                if open_master and time.monotonic() >= resize_at:
                    self._terminal.size(self._master)
                    resize_at = time.monotonic() + .1
                readers = [self._master] if open_master and len(self._output) < _BUFFER else []
                if open_master and len(incoming) < _BUFFER:
                    readers.append(self._terminal.fd)
                writers = [self._master] if open_master and incoming else []
                if self._output:
                    writers.append(self._terminal.fd)
                readable, writable = await _ready_io(readers, writers, timeout=.1)
                if self._master in readable:
                    data = _read_pty(self._master, min(65536, _BUFFER - len(self._output)))
                    if data:
                        self._output.extend(data)
                    elif data == b"":
                        open_master = False
                if self._terminal.fd in readable:
                    try:
                        data = os.read(self._terminal.fd, min(65536, _BUFFER - len(incoming)))
                    except BlockingIOError:
                        data = None
                    if data == b"":
                        raise RuntimeError("Foreground terminal input closed")
                    if data:
                        incoming.extend(data)
                for descriptor, buffer in (
                    (self._master, incoming), (self._terminal.fd, self._output),
                ):
                    if descriptor in writable and buffer:
                        try:
                            count = os.write(descriptor, buffer[:65536])
                            del buffer[:count]
                        except BlockingIOError:
                            pass
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            self._terminal_error = error
            self._request_retirement()

    def _close(self, descriptor):
        os.close(descriptor)
        self._descriptors.remove(descriptor)

    def _request_retirement(self):
        self._requested = True
        if self._writer is not None:
            with _lifetime_lock:
                _lifetime_writers.discard(self._writer)
                self._close(self._writer)
                self._writer = None
        if self._control is not None:
            self._close(self._control)
            self._control = None

    def _ensure_completion(self):
        if self._completion is None:
            self._completion = asyncio.create_task(self._finish())
        return self._completion

    async def _finish(self):
        try:
            if self._guardian is not None:
                while not self._receipt:
                    frame = await self._frame()
                    if frame.startswith(b"exit "):
                        code = int(frame[5:])
                        if self.returncode is not None and self.returncode != code:
                            raise RuntimeError("Foreground native exit status changed")
                        self.returncode = code
                    elif frame == f"settled {self._guardian.pid}".encode("ascii"):
                        self._receipt = True
                    elif frame == b"stopped":
                        if not self._requested:
                            self._terminal.suspend()
                            os.write(self._control, b"c")
                    elif self._starting and (
                        frame in (b"ready", b"running") or frame.startswith(b"native ")
                    ):
                        continue
                    else:
                        raise RuntimeError("Foreground guardian settlement is unconfirmed")
                await asyncio.to_thread(confirm_session, self._guardian)
            self._scope_settled = True
            await self._stop_relay()
            if self._terminal is not None:
                await asyncio.to_thread(_drain, self._master, self._terminal, self._output)
            requested = self._requested
            self._dispose()
            self.retirement_started = requested and self._receipt
        except BaseException as error:
            try:
                self._request_retirement()
                await self._stop_relay()
            except BaseException as retirement_error:
                retirement_error.__cause__ = error
                error = retirement_error
            try:
                if self._terminal is not None:
                    self._terminal.restore()
            except BaseException as restore_error:
                restore_error.__cause__ = error
                error = restore_error
            raise OwnedCleanupError(
                self._guardian, cleanup_resources=(self,), cleanup_retry=self._retry_cleanup,
            ) from error
        if self._terminal_error is not None:
            raise self._terminal_error
        if self._admitted and self.returncode is None:
            raise RuntimeError("Foreground native exit status is unconfirmed")
        self._cleanup_completed = self._receipt and self._scope_settled
        return self.returncode

    async def _stop_relay(self):
        if self._relay_task is not None:
            self._relay_task.cancel()
            await asyncio.gather(self._relay_task, return_exceptions=True)

    def _dispose(self):
        self._request_retirement()
        if self._terminal is not None:
            self._terminal.restore()
            self._terminal.close()
            self._terminal = None
        for descriptor in tuple(self._descriptors):
            self._close(descriptor)

    def _retry_cleanup(self, deadline):
        if not self._scope_settled and self._guardian is not None:
            confirm_session(self._guardian, timeout=min(2, max(0, deadline - time.monotonic())))
            self._scope_settled = True
        if self._terminal is not None:
            _drain(self._master, self._terminal, self._output, deadline=deadline)
        self._dispose()
        self._cleanup_completed = self._receipt and self._scope_settled

    async def wait(self) -> int:
        return await asyncio.shield(self._ensure_completion())

    async def terminate(self) -> None:
        self._request_retirement()
        await asyncio.shield(self._ensure_completion())

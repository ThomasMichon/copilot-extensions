"""Synchronous, deadline-bounded private Windows named-pipe channels.

Use from a CLI or caller-owned worker thread, not an event-loop thread.
Listeners are single-use: their first-instance handle transfers to the channel.
No secret frames pass until a one-byte native authentication handshake finishes.
"""

from __future__ import annotations

import ctypes as c
import threading
import time
from dataclasses import dataclass
from typing import Any

from .private_protocol import Deadline, PeerIdentity, Refusal, deadline_lock
from .private_peer import PeerProcess
from .private_windows_native import (
    BROKEN_PIPE,
    DWORD,
    INVALID_HANDLE,
    IO_INCOMPLETE,
    IO_PENDING,
    NOT_FOUND,
    NO_DATA,
    OPERATION_ABORTED,
    OVERLAPPED,
    PIPE_CONNECTED,
    WAIT_TIMEOUT,
    Native,
    native,
    refuse,
    validate_path,
)


@dataclass
class _Operation:
    api: Native
    pipe: int | None
    overlapped: OVERLAPPED
    buffer: Any
    completed: bool = False

    def result(self) -> tuple[bool, int, int]:
        count = DWORD()
        ok = self.api.k.GetOverlappedResult(
            self.pipe,
            c.byref(self.overlapped),
            c.byref(count),
            False,
        )
        error = 0 if ok else self.api.error()
        return bool(ok), count.value, error


# A failed cancellation must not free memory still referenced by the kernel.
# Fail closed at a bounded retention cap; reap completed operations on next use.
_retained: list[_Operation] = []
_retained_lock = threading.Lock()
_RETENTION_CAP = 32
_slots = threading.BoundedSemaphore(_RETENTION_CAP)
_CLOSED_ERRORS = (BROKEN_PIPE, NO_DATA)
_TERMINAL = (0, OPERATION_ABORTED, *_CLOSED_ERRORS)


def _reap() -> None:
    with _retained_lock:
        for operation in tuple(_retained):
            if not operation.completed and operation.result()[2] not in _TERMINAL:
                continue
            operation.completed = True
            operation.api.close(operation.pipe)
            operation.pipe = None
            operation.api.close(operation.overlapped.hEvent)
            operation.overlapped.hEvent = None
            _retained.remove(operation)
            _slots.release()
        if len(_retained) >= _RETENTION_CAP:
            refuse(Refusal.UNAVAILABLE)


class WindowsChannel:
    """Authenticated channel retaining query/synchronize custody of its peer.

    Calls on one channel are serialized. Concurrent close waits for the active
    call's bounded deadline; cancellation is supplied through that Deadline.
    """

    def __init__(
        self,
        pipe: int,
        peer: PeerIdentity,
        process: int | None,
        *,
        api: Native | None = None,
    ) -> None:
        self.peer = peer
        self._api = native() if api is None else api
        self._pipe: int | None = pipe
        self._process: int | None = process
        self._lock = threading.RLock()

    def _check(self, deadline: Deadline) -> int:
        deadline.remaining()
        if self._pipe is None:
            refuse(Refusal.CLOSED)
        if self._process is not None:
            self._api.alive(self._process)
        return self._pipe

    def check_peer(self, deadline: Deadline) -> None:
        """Recheck original native peer custody immediately before a control."""
        with deadline_lock(self._lock, deadline):
            if self._process is None:
                refuse(Refusal.IDENTITY)
            self._check(deadline)

    def retain_peer(self, deadline: Deadline) -> PeerProcess:
        """Duplicate original native custody, never reopen a numeric PID."""
        with deadline_lock(self._lock, deadline):
            if self._process is None:
                refuse(Refusal.IDENTITY)
            self._check(deadline)
            reference = self._api.duplicate_process(self._process, self.peer.pid)
            try:
                self._check(deadline)
                return PeerProcess(reference, self.peer, api=self._api)
            except BaseException:
                self._api.close(reference)
                raise

    def _cancel(self, operation: _Operation) -> None:
        completed = False
        try:
            ok = self._api.k.CancelIoEx(operation.pipe, c.byref(operation.overlapped))
            failed = not ok and self._api.error() != NOT_FOUND
            error = operation.result()[2]
            if error in _TERMINAL:
                completed = True
                try:
                    self._api.close(operation.overlapped.hEvent)
                finally:
                    _slots.release()
                if failed:
                    refuse()
                return
            # Expiry has already consumed the request budget. Retain kernel
            # custody instead of granting cancellation a fresh timeout.
            refuse(Refusal.CANCELLATION_UNRESOLVED)
        finally:
            if not completed:
                with _retained_lock:
                    _retained.append(operation)
                self._pipe = None

    def _io(self, kind: str, buffer: Any, count: int, deadline: Deadline) -> int:
        pipe = self._check(deadline)
        _reap()
        if not _slots.acquire(blocking=False):
            refuse(Refusal.UNAVAILABLE)
        event = self._api.k.CreateEventW(None, True, False, None)
        if not event:
            _slots.release()
            refuse()
        operation = _Operation(self._api, pipe, OVERLAPPED(hEvent=event), buffer)
        issued = False
        completed = False
        try:
            deadline.remaining()
            # Custody starts before ctypes can return through a Python interrupt.
            issued = True
            if kind == "connect":
                ok = self._api.k.ConnectNamedPipe(pipe, c.byref(operation.overlapped))
            else:
                function = self._api.k.ReadFile if kind == "read" else self._api.k.WriteFile
                ok = function(pipe, buffer, count, None, c.byref(operation.overlapped))
            error = 0 if ok else self._api.error()
            if kind == "connect" and error == PIPE_CONNECTED:
                completed = True
                deadline.remaining()
                return 0
            if not ok and error != IO_PENDING:
                completed = True
                refuse(Refusal.CLOSED if error in _CLOSED_ERRORS else Refusal.IO)
            while True:
                deadline.remaining()
                ok, transferred, error = operation.result()
                if error in _TERMINAL:
                    completed = True
                    if not ok:
                        refuse(Refusal.CLOSED if error in _CLOSED_ERRORS else Refusal.IO)
                    deadline.remaining()
                    return transferred
                if error != IO_INCOMPLETE:
                    refuse()
                result = self._api.k.WaitForSingleObject(
                    event,
                    max(1, int(deadline.wait_slice() * 1000)),
                )
                if result not in (0, WAIT_TIMEOUT):
                    refuse()
        except BaseException:
            try:
                if issued and not completed:
                    event = None
                    self._cancel(operation)
            finally:
                # Partial frames/cancelled connects are never reusable.
                self.close()
            raise
        finally:
            if event is not None:
                try:
                    self._api.close(event)
                finally:
                    _slots.release()

    def read_exact(self, count: int, deadline: Deadline) -> bytes:
        """Read exactly count bytes, refusing incomplete or cancelled frames."""
        if type(count) is not int or count < 0:
            refuse(Refusal.SIZE)
        with deadline_lock(self._lock, deadline):
            try:
                self._check(deadline)
                chunks: list[bytes] = []
                remaining = count
                while remaining:
                    buffer = c.create_string_buffer(min(remaining, 65536))
                    size = self._io("read", buffer, len(buffer), deadline)
                    if not size:
                        refuse(Refusal.CLOSED)
                    chunks.append(buffer.raw[:size])
                    remaining -= size
                self._check(deadline)
                return b"".join(chunks)
            except BaseException:
                self.close()
                raise

    def write_all(self, data: bytes, deadline: Deadline) -> None:
        """Write all bytes with the same absolute deadline as authentication."""
        if not isinstance(data, bytes):
            refuse(Refusal.PROTOCOL)
        with deadline_lock(self._lock, deadline):
            try:
                self._check(deadline)
                offset = 0
                while offset < len(data):
                    chunk = data[offset : offset + 65536]
                    buffer = c.create_string_buffer(chunk)
                    size = self._io("write", buffer, len(chunk), deadline)
                    if not size:
                        refuse(Refusal.CLOSED)
                    offset += size
                self._check(deadline)
            except BaseException:
                self.close()
                raise

    def close(self) -> None:
        """Release owned handles; cleanup failures are fixed typed refusals."""
        with self._lock:
            pipe, process = self._pipe, self._process
            self._pipe = self._process = None
            try:
                self._api.close(pipe)
            finally:
                self._api.close(process)


def connect(pipe_path: str, expected: PeerIdentity, deadline: Deadline) -> WindowsChannel:
    """Connect locally and verify the server before sending authentication."""
    validate_path(pipe_path)
    if not isinstance(expected, PeerIdentity):
        refuse(Refusal.IDENTITY)
    deadline.remaining()
    api = native()
    _reap()
    while True:
        deadline.remaining()
        # Overlapped and identification-only SQOS, never impersonation/delegation.
        pipe = api.k.CreateFileW(
            pipe_path,
            0x80000000 | 0x40000000,
            0,
            None,
            3,
            0x40000000 | 0x100000 | 0x10000,
            None,
        )
        if pipe != INVALID_HANDLE:
            break
        if api.error() not in (2, 231):
            refuse(Refusal.UNAVAILABLE)
        time.sleep(deadline.wait_slice())
    channel: WindowsChannel | None = None
    try:
        deadline.remaining()
        peer, process = api.pin_peer(pipe, server=True, expected=expected)
        channel = WindowsChannel(pipe, peer, process, api=api)
        channel.write_all(b"\x00", deadline)
        if channel.read_exact(1, deadline) != b"\x01":
            refuse(Refusal.PROTOCOL)
        deadline.remaining()
        return channel
    except BaseException:
        if channel is None:
            api.close(pipe)
        else:
            channel.close()
        raise


class WindowsListener:
    """Single-use owner-only local listener rejecting occupied pipe names."""

    def __init__(self, pipe_path: str) -> None:
        validate_path(pipe_path)
        self._api = native()
        _reap()
        self._pipe: int | None = self._api.listener(pipe_path)
        self._lock = threading.RLock()

    def accept(
        self,
        deadline: Deadline,
        expected: PeerIdentity,
    ) -> WindowsChannel:
        """Authenticate PID, creation time, process SID and connected token SID."""
        if not isinstance(expected, PeerIdentity):
            refuse(Refusal.IDENTITY)
        with deadline_lock(self._lock, deadline):
            deadline.remaining()
            if self._pipe is None:
                refuse(Refusal.CLOSED)
            pipe, self._pipe = self._pipe, None
            # No public channel escapes until native authentication completes.
            channel = WindowsChannel(
                pipe,
                PeerIdentity(1, "0", "unverified"),
                None,
                api=self._api,
            )
            try:
                channel._io("connect", None, 0, deadline)
                peer, process = self._api.pin_peer(pipe, server=False, expected=expected)
                channel.peer, channel._process = peer, process
                if channel.read_exact(1, deadline) != b"\x00":
                    refuse(Refusal.PROTOCOL)
                if self._api.connected_sid(pipe) != peer.principal:
                    refuse(Refusal.PRINCIPAL)
                # Recheck the pipe PID and the held live process after impersonation.
                if self._api.peer_pid(pipe, server=False) != peer.pid:
                    refuse(Refusal.IDENTITY)
                self._api.alive(process)
                deadline.remaining()
                channel.write_all(b"\x01", deadline)
                return channel
            except BaseException:
                channel.close()
                raise

    def close(self) -> None:
        """Close a not-yet-accepted listener without affecting any peer."""
        with self._lock:
            pipe, self._pipe = self._pipe, None
            self._api.close(pipe)

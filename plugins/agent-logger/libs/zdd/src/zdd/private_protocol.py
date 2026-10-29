"""Bounded framing for authenticated, secrets-bearing local runtime channels.

Transport authentication belongs to the native channel. Frames are never
discovery or identity evidence and must not be logged, persisted or repr'd.
"""

from __future__ import annotations

import json
import math
import struct
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Iterator, Protocol

PROTOCOL_VERSION = 1
MAX_FRAME_BYTES = 1024 * 1024
_HEADER = struct.Struct("!BI")


class Refusal(str, Enum):
    """Fixed diagnostics safe to publish without captured exception values."""

    DEADLINE = "private-ipc-deadline"
    CANCELLED = "private-ipc-cancelled"
    UNAVAILABLE = "private-ipc-unavailable"
    IDENTITY = "private-ipc-identity-refused"
    PRINCIPAL = "private-ipc-principal-refused"
    CLOSED = "private-ipc-disconnected"
    PROTOCOL = "private-ipc-protocol-refused"
    SIZE = "private-ipc-frame-too-large"
    IO = "private-ipc-io-failed"
    CANCELLATION_UNRESOLVED = "private-ipc-cancellation-unresolved"
    AUTHORITY = "private-ipc-authority-refused"
    CAPACITY = "private-ipc-capacity-refused"


class PrivateIPCError(RuntimeError):
    """A redacted refusal; never wrap an OS error's credential-bearing text."""

    def __init__(self, reason: Refusal) -> None:
        self.reason = reason
        super().__init__(reason.value)


@dataclass(frozen=True)
class PeerIdentity:
    """Identity obtained from the channel's native peer, not a frame."""

    pid: int
    start_time: str
    principal: str

    def __post_init__(self) -> None:
        if (
            type(self.pid) is not int or self.pid <= 0
            or not isinstance(self.start_time, str) or not self.start_time.isdigit()
            or not isinstance(self.principal, str) or not self.principal
        ):
            raise PrivateIPCError(Refusal.IDENTITY)


class Deadline:
    """One monotonic budget shared by connect, authentication and all frames."""

    def __init__(
        self, timeout: float, *, cancelled: threading.Event | None = None,
    ) -> None:
        if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("private IPC timeout must be finite and positive")
        self._expires = time.monotonic() + timeout
        self._cancelled = cancelled

    def remaining(self) -> float:
        """Return time left or refuse cancellation/expiry without resetting it."""
        if self._cancelled is not None and self._cancelled.is_set():
            raise PrivateIPCError(Refusal.CANCELLED)
        remaining = self._expires - time.monotonic()
        if remaining <= 0:
            raise PrivateIPCError(Refusal.DEADLINE)
        return remaining

    def wait_slice(self) -> float:
        """Bound native waits so cancellation is observed within 50 ms."""
        return min(self.remaining(), 0.05)


class Channel(Protocol):
    """An authenticated byte channel retaining native peer process custody."""

    peer: PeerIdentity

    def read_exact(self, count: int, deadline: Deadline) -> bytes: ...
    def write_all(self, data: bytes, deadline: Deadline) -> None: ...
    def close(self) -> None: ...


class _Lock(Protocol):
    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool: ...
    def release(self) -> None: ...


@contextmanager
def deadline_lock(lock: _Lock, deadline: Deadline) -> Iterator[None]:
    """Serialize channel calls without waiting beyond their shared deadline."""
    while not lock.acquire(timeout=deadline.wait_slice()):
        deadline.remaining()
    try:
        deadline.remaining()
        yield
    finally:
        lock.release()


def _object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise PrivateIPCError(Refusal.PROTOCOL)
        result[key] = value
    return result


def _invalid_constant(value: str) -> object:
    raise PrivateIPCError(Refusal.PROTOCOL)


def write_frame(channel: Channel, value: dict[str, object], deadline: Deadline) -> None:
    """Send one versioned bounded JSON object without exposing its contents."""
    deadline.remaining()
    if type(value) is not dict:
        raise PrivateIPCError(Refusal.PROTOCOL)
    try:
        body = json.dumps(value, ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError, RecursionError):
        raise PrivateIPCError(Refusal.PROTOCOL) from None
    if len(body) > MAX_FRAME_BYTES:
        raise PrivateIPCError(Refusal.SIZE)
    deadline.remaining()
    channel.write_all(_HEADER.pack(PROTOCOL_VERSION, len(body)) + body, deadline)


def read_frame(channel: Channel, deadline: Deadline) -> dict[str, object]:
    """Read one complete object; malformed/oversized frames are refusals."""
    version, size = _HEADER.unpack(channel.read_exact(_HEADER.size, deadline))
    if version != PROTOCOL_VERSION:
        raise PrivateIPCError(Refusal.PROTOCOL)
    if size > MAX_FRAME_BYTES:
        raise PrivateIPCError(Refusal.SIZE)
    if size == 0:
        raise PrivateIPCError(Refusal.PROTOCOL)
    body = channel.read_exact(size, deadline)
    try:
        value = json.loads(
            body.decode("utf-8"), object_pairs_hook=_object,
            parse_constant=_invalid_constant,
        )
    except (UnicodeError, ValueError, RecursionError):
        raise PrivateIPCError(Refusal.PROTOCOL) from None
    if type(value) is not dict:
        raise PrivateIPCError(Refusal.PROTOCOL)
    deadline.remaining()
    return value

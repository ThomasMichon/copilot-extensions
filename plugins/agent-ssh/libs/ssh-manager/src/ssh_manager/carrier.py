"""Bounded framed protocol and lifecycle for persistent SSH stdio carriers."""

from __future__ import annotations

import asyncio
import contextlib
import json
import struct
import time
import uuid
from collections.abc import AsyncIterable, Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, BinaryIO, Protocol

PROTOCOL_VERSION = 1
MIN_PROTOCOL_VERSION = 1
DEFAULT_MAX_FRAME_SIZE = 1024 * 1024
DEFAULT_MAX_QUEUED_FRAMES = 128
DEFAULT_MAX_BUFFERED_BYTES = 4 * 1024 * 1024

_HEADER = struct.Struct(">I")


class EnvelopeType(str, Enum):
    """Protocol envelope kinds."""

    HELLO = "hello"
    REQUEST = "request"
    RESPONSE = "response"
    EVENT = "event"
    HEARTBEAT = "heartbeat"
    CANCEL = "cancel"
    ERROR = "error"


class CarrierError(RuntimeError):
    """Base carrier failure with an explicit reconnectability signal."""

    def __init__(self, message: str, *, reconnectable: bool = False) -> None:
        super().__init__(message)
        self.reconnectable = reconnectable


class CarrierProtocolError(CarrierError):
    """Malformed, oversized, or incompatible protocol data."""


class CarrierBackpressure(CarrierError):
    """A bounded output queue cannot accept more data."""


class CarrierUnavailable(CarrierError):
    """The SSH transport is unavailable and may be reconnected."""


class CarrierStale(CarrierUnavailable):
    """The peer stopped making heartbeat or subscription progress."""


class CarrierRemoteError(CarrierError):
    """A structured error returned by the remote carrier endpoint."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = dict(payload)
        self.code = str(payload.get("code") or "remote_error")
        super().__init__(
            str(payload.get("message") or "remote carrier error"),
            reconnectable=bool(payload.get("reconnectable", False)),
        )


@dataclass(frozen=True)
class Envelope:
    """One framed carrier envelope."""

    type: EnvelopeType
    payload: dict[str, Any] = field(default_factory=dict)
    request_id: str | None = None
    subscription_id: str | None = None
    replayable: bool = False
    position: str | int | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"type": self.type.value}
        if self.request_id is not None:
            data["request_id"] = self.request_id
        if self.subscription_id is not None:
            data["subscription_id"] = self.subscription_id
        if self.replayable:
            data["replayable"] = True
        if self.position is not None:
            data["position"] = self.position
        if self.payload:
            data["payload"] = self.payload
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Envelope:
        if not isinstance(data, dict):
            raise CarrierProtocolError("carrier envelope must be an object")
        try:
            kind = EnvelopeType(data["type"])
        except (KeyError, TypeError, ValueError) as exc:
            raise CarrierProtocolError("carrier envelope has an invalid type") from exc
        payload = data.get("payload", {})
        if not isinstance(payload, dict):
            raise CarrierProtocolError("carrier envelope payload must be an object")
        request_id = data.get("request_id")
        subscription_id = data.get("subscription_id")
        if request_id is not None and not isinstance(request_id, str):
            raise CarrierProtocolError("request_id must be a string")
        if subscription_id is not None and not isinstance(subscription_id, str):
            raise CarrierProtocolError("subscription_id must be a string")
        return cls(
            type=kind,
            payload=payload,
            request_id=request_id,
            subscription_id=subscription_id,
            replayable=bool(data.get("replayable", False)),
            position=data.get("position"),
        )


def hello_envelope(*, max_frame_size: int = DEFAULT_MAX_FRAME_SIZE) -> Envelope:
    """Build the version-negotiation hello."""
    return Envelope(
        EnvelopeType.HELLO,
        payload={
            "protocol_version": PROTOCOL_VERSION,
            "min_protocol_version": MIN_PROTOCOL_VERSION,
            "max_frame_size": int(max_frame_size),
        },
    )


def encode_envelope(
    envelope: Envelope,
    *,
    max_frame_size: int = DEFAULT_MAX_FRAME_SIZE,
) -> bytes:
    """Encode one envelope as a four-byte length prefix plus JSON bytes."""
    try:
        body = json.dumps(
            envelope.to_dict(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CarrierProtocolError("carrier envelope is not JSON serializable") from exc
    if not body or len(body) > max_frame_size:
        raise CarrierProtocolError(
            f"carrier frame size {len(body)} exceeds limit {max_frame_size}"
        )
    return _HEADER.pack(len(body)) + body


def decode_envelope(
    body: bytes,
    *,
    max_frame_size: int = DEFAULT_MAX_FRAME_SIZE,
) -> Envelope:
    """Decode a frame body after enforcing its bound."""
    if not body or len(body) > max_frame_size:
        raise CarrierProtocolError(
            f"carrier frame size {len(body)} exceeds limit {max_frame_size}"
        )
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CarrierProtocolError("carrier frame is not valid UTF-8 JSON") from exc
    return Envelope.from_dict(data)


async def read_envelope(
    reader: asyncio.StreamReader,
    *,
    max_frame_size: int = DEFAULT_MAX_FRAME_SIZE,
) -> Envelope:
    """Read one bounded length-prefixed envelope."""
    header = await reader.readexactly(_HEADER.size)
    (size,) = _HEADER.unpack(header)
    if size <= 0 or size > max_frame_size:
        raise CarrierProtocolError(
            f"carrier frame size {size} exceeds limit {max_frame_size}"
        )
    return decode_envelope(
        await reader.readexactly(size),
        max_frame_size=max_frame_size,
    )


def read_envelope_sync(
    reader: BinaryIO,
    *,
    max_frame_size: int = DEFAULT_MAX_FRAME_SIZE,
) -> Envelope | None:
    """Read one envelope from a blocking stream, returning ``None`` on clean EOF."""
    header = reader.read(_HEADER.size)
    if not header:
        return None
    while len(header) < _HEADER.size:
        chunk = reader.read(_HEADER.size - len(header))
        if not chunk:
            raise EOFError("carrier stdin ended inside a frame header")
        header += chunk
    (size,) = _HEADER.unpack(header)
    if size <= 0 or size > max_frame_size:
        raise CarrierProtocolError(
            f"carrier frame size {size} exceeds limit {max_frame_size}"
        )
    body = b""
    while len(body) < size:
        chunk = reader.read(size - len(body))
        if not chunk:
            raise EOFError("carrier stdin ended inside a frame")
        body += chunk
    return decode_envelope(body, max_frame_size=max_frame_size)


def write_envelope_sync(
    writer: BinaryIO,
    envelope: Envelope,
    *,
    max_frame_size: int = DEFAULT_MAX_FRAME_SIZE,
) -> None:
    """Write and flush one envelope to a blocking stream."""
    writer.write(encode_envelope(envelope, max_frame_size=max_frame_size))
    writer.flush()


def validate_hello(envelope: Envelope) -> int:
    """Validate a peer hello and return the negotiated protocol version."""
    if envelope.type is not EnvelopeType.HELLO:
        raise CarrierProtocolError("carrier peer did not send hello first")
    try:
        peer_current = int(envelope.payload["protocol_version"])
        peer_min = int(envelope.payload["min_protocol_version"])
    except (KeyError, TypeError, ValueError) as exc:
        raise CarrierProtocolError("carrier hello has invalid version fields") from exc
    peer_max_frame_size = negotiated_frame_size(envelope)
    low = max(MIN_PROTOCOL_VERSION, peer_min)
    high = min(PROTOCOL_VERSION, peer_current)
    if low > high:
        raise CarrierProtocolError(
            f"no compatible carrier protocol (local {MIN_PROTOCOL_VERSION}-"
            f"{PROTOCOL_VERSION}, peer {peer_min}-{peer_current})"
        )
    if peer_max_frame_size <= 0:
        raise CarrierProtocolError("carrier hello has invalid max_frame_size")
    return high


def negotiated_frame_size(
    envelope: Envelope,
    *,
    local_max_frame_size: int = DEFAULT_MAX_FRAME_SIZE,
) -> int:
    """Return the smaller valid local/peer outbound frame limit."""
    try:
        peer_max_frame_size = int(envelope.payload["max_frame_size"])
    except (KeyError, TypeError, ValueError) as exc:
        raise CarrierProtocolError(
            "carrier hello has invalid max_frame_size"
        ) from exc
    if peer_max_frame_size <= 0 or local_max_frame_size <= 0:
        raise CarrierProtocolError("carrier hello has invalid max_frame_size")
    return min(local_max_frame_size, peer_max_frame_size)


class _Process(Protocol):
    stdin: asyncio.StreamWriter | None
    stdout: asyncio.StreamReader | None
    returncode: int | None

    async def wait(self) -> int: ...


class _BoundedFrameQueue:
    def __init__(self, max_frames: int, max_bytes: int) -> None:
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=max_frames)
        self._max_bytes = max_bytes
        self._bytes = 0
        self._lock = asyncio.Lock()

    async def put(self, frame: bytes) -> None:
        async with self._lock:
            if self._queue.full() or self._bytes + len(frame) > self._max_bytes:
                raise CarrierBackpressure("carrier output queue is full")
            self._bytes += len(frame)
            self._queue.put_nowait(frame)

    async def get(self) -> bytes:
        frame = await self._queue.get()
        async with self._lock:
            self._bytes -= len(frame)
        return frame

    @property
    def frame_count(self) -> int:
        return self._queue.qsize()

    @property
    def byte_count(self) -> int:
        return self._bytes


class _BufferBudget:
    def __init__(self, maximum: int) -> None:
        self.maximum = maximum
        self.used = 0

    def reserve(self, size: int) -> bool:
        if size < 0 or self.used + size > self.maximum:
            return False
        self.used += size
        return True

    def release(self, size: int) -> None:
        self.used = max(0, self.used - size)


class CarrierSubscription:
    """A bounded replayable event stream carried over one SSH process."""

    def __init__(
        self,
        carrier: PersistentCarrier,
        subscription_id: str,
        payload: dict[str, Any],
        *,
        replayable: bool,
        queue_size: int,
        progress_timeout: float | None,
        position: str | int | None,
        retain_buffered_on_reconnect: bool,
    ) -> None:
        self._carrier = carrier
        self.subscription_id = subscription_id
        self._payload = dict(payload)
        self.replayable = replayable
        self._queue: asyncio.Queue[tuple[Envelope | CarrierError, int]] = asyncio.Queue(
            maxsize=queue_size
        )
        self.progress_timeout = progress_timeout
        self.last_progress = time.monotonic()
        self.position = position
        self.retain_buffered_on_reconnect = retain_buffered_on_reconnect
        self.closed = False
        self.initializing = True
        self._terminal_error: CarrierError | None = None

    def request_envelope(self) -> Envelope:
        return Envelope(
            EnvelopeType.REQUEST,
            payload=self._payload,
            subscription_id=self.subscription_id,
            replayable=self.replayable,
            position=self.position,
        )

    async def get(self) -> Envelope:
        if self.closed and self._queue.empty():
            raise self._terminal_error or CarrierUnavailable(
                "carrier subscription closed"
            )
        item, size = await self._queue.get()
        self._carrier._buffer_budget.release(size)
        if isinstance(item, CarrierError):
            raise item
        if item.position is not None:
            self.position = item.position
        self.last_progress = time.monotonic()
        return item

    def _offer(self, item: Envelope | CarrierError, size: int) -> bool:
        if self.closed or self._queue.full():
            return False
        if not self._carrier._buffer_budget.reserve(size):
            return False
        self._queue.put_nowait((item, size))
        self.last_progress = time.monotonic()
        if (
            self.retain_buffered_on_reconnect
            and isinstance(item, Envelope)
            and item.position is not None
        ):
            self.position = item.position
        return True

    def _terminate(self, error: CarrierError) -> None:
        self._terminal_error = error
        self._clear_buffer()
        self._queue.put_nowait((error, 0))
        self.closed = True

    def _clear_buffer(self) -> None:
        while True:
            try:
                _item, size = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self._carrier._buffer_budget.release(size)

    async def close(self) -> None:
        await self._carrier.close_subscription(self.subscription_id)


class CarrierLease:
    """Logical-client ownership of a shared persistent carrier."""

    def __init__(self, carrier: PersistentCarrier) -> None:
        self.carrier = carrier
        self._released = False

    async def release(self) -> None:
        if not self._released:
            self._released = True
            await self.carrier.release_client()

    async def __aenter__(self) -> PersistentCarrier:
        return self.carrier

    async def __aexit__(self, *_exc: object) -> None:
        await self.release()


class PersistentCarrier:
    """One reconnecting framed stdio carrier for an SSH connection identity."""

    def __init__(
        self,
        connection_identity: str,
        remote_command: str,
        opener: Callable[[], Awaitable[_Process]],
        closer: Callable[[_Process], Awaitable[None]],
        *,
        idle_timeout: float = 60.0,
        heartbeat_interval: float = 15.0,
        stale_timeout: float = 45.0,
        handshake_timeout: float = 10.0,
        reconnect_initial: float = 0.5,
        reconnect_max: float = 15.0,
        max_frame_size: int = DEFAULT_MAX_FRAME_SIZE,
        max_queued_frames: int = DEFAULT_MAX_QUEUED_FRAMES,
        max_buffered_bytes: int = DEFAULT_MAX_BUFFERED_BYTES,
        on_retired: Callable[[str, PersistentCarrier], None] | None = None,
    ) -> None:
        self.connection_identity = connection_identity
        self.remote_command = remote_command
        self._opener = opener
        self._closer = closer
        self._idle_timeout = idle_timeout
        self._heartbeat_interval = heartbeat_interval
        self._stale_timeout = stale_timeout
        self._handshake_timeout = handshake_timeout
        self._reconnect_initial = reconnect_initial
        self._reconnect_max = reconnect_max
        self._max_frame_size = max_frame_size
        self._outbound_max_frame_size = max_frame_size
        self._max_queued_frames = max_queued_frames
        self._max_buffered_bytes = max_buffered_bytes
        self._on_retired = on_retired
        self._connect_lock = asyncio.Lock()
        self._failure_lock = asyncio.Lock()
        self._process: _Process | None = None
        self._writer_queue: _BoundedFrameQueue | None = None
        self._tasks: set[asyncio.Task[Any]] = set()
        self._reconnect_task: asyncio.Task[None] | None = None
        self._idle_task: asyncio.Task[None] | None = None
        self._pending: dict[str, asyncio.Future[Envelope]] = {}
        self._subscriptions: dict[str, CarrierSubscription] = {}
        self._buffer_budget = _BufferBudget(max_buffered_bytes)
        self._logical_clients = 0
        self._closed = False
        self._state = "idle"
        self._last_received = 0.0
        self._last_error = ""
        self._next_connect_at = 0.0
        self._backoff = reconnect_initial
        self._reconnect_count = 0
        self._transport_epoch = 0

    async def acquire(self) -> CarrierLease:
        if self._closed:
            raise CarrierUnavailable("carrier has retired")
        self._logical_clients += 1
        if self._idle_task:
            self._idle_task.cancel()
            self._idle_task = None
        try:
            await self.ensure_started()
        except asyncio.CancelledError:
            self._logical_clients -= 1
            self._schedule_idle_retirement()
            raise
        except Exception:
            self._logical_clients -= 1
            self._schedule_idle_retirement()
            raise
        return CarrierLease(self)

    async def release_client(self) -> None:
        self._logical_clients = max(0, self._logical_clients - 1)
        self._schedule_idle_retirement()

    async def ensure_started(self) -> None:
        """Start or reconnect the transport, honoring bounded retry backoff."""
        if self._closed:
            raise CarrierUnavailable("carrier has retired")
        if (
            self._process is not None
            and self._process.returncode is None
            and self._state != "connecting"
        ):
            return
        async with self._connect_lock:
            if (
                self._process is not None
                and self._process.returncode is None
                and self._state != "connecting"
            ):
                return
            while True:
                delay = self._next_connect_at - time.monotonic()
                if delay > 0:
                    await asyncio.sleep(delay)
                self._state = "connecting"
                attempt_epoch = self._transport_epoch
                process: _Process | None = None
                try:
                    process = await self._opener()
                    if process.stdin is None or process.stdout is None:
                        raise CarrierUnavailable(
                            "carrier process is missing stdio pipes"
                        )
                    frame = encode_envelope(
                        hello_envelope(max_frame_size=self._max_frame_size),
                        max_frame_size=self._max_frame_size,
                    )
                    process.stdin.write(frame)
                    await process.stdin.drain()
                    peer_hello = await asyncio.wait_for(
                        read_envelope(
                            process.stdout,
                            max_frame_size=self._max_frame_size,
                        ),
                        timeout=self._handshake_timeout,
                    )
                    validate_hello(peer_hello)
                    outbound_max_frame_size = negotiated_frame_size(
                        peer_hello,
                        local_max_frame_size=self._max_frame_size,
                    )
                except asyncio.CancelledError:
                    if process is not None:
                        await self._closer(process)
                    raise
                except Exception as exc:
                    if process is not None:
                        await self._closer(process)
                    if (
                        not self._closed
                        and attempt_epoch != self._transport_epoch
                    ):
                        continue
                    self._record_connect_failure(exc)
                    raise CarrierUnavailable(
                        f"carrier handshake failed: {type(exc).__name__}",
                        reconnectable=True,
                    ) from exc

                if self._closed:
                    await self._closer(process)
                    raise CarrierUnavailable("carrier has retired")
                if attempt_epoch != self._transport_epoch:
                    await self._closer(process)
                    continue
                self._transport_epoch += 1
                active_epoch = self._transport_epoch
                self._process = process
                self._outbound_max_frame_size = outbound_max_frame_size
                queue = _BoundedFrameQueue(
                    self._max_queued_frames, self._max_buffered_bytes
                )
                self._writer_queue = queue
                self._last_received = time.monotonic()
                self._start_task(
                    self._reader_loop(process, active_epoch),
                    "ssh-carrier-reader",
                )
                try:
                    await self._restore_subscriptions(process)
                except Exception as exc:
                    await self._transport_failed(
                        exc,
                        expected_epoch=active_epoch,
                    )
                    raise CarrierUnavailable(
                        "carrier subscription restoration failed",
                        reconnectable=True,
                    ) from exc
                if active_epoch != self._transport_epoch:
                    raise CarrierUnavailable(
                        "carrier transport was lost during restoration",
                        reconnectable=True,
                    )
                self._state = "healthy"
                self._last_error = ""
                self._next_connect_at = 0.0
                self._backoff = self._reconnect_initial
                self._start_task(
                    self._writer_loop(process, queue, active_epoch),
                    "ssh-carrier-writer",
                )
                self._start_task(
                    self._heartbeat_loop(active_epoch),
                    "ssh-carrier-heartbeat",
                )
                self._start_task(
                    self._stale_loop(active_epoch),
                    "ssh-carrier-stale",
                )
                return

    def _record_connect_failure(self, exc: BaseException) -> None:
        self._state = "degraded"
        self._last_error = type(exc).__name__
        self._next_connect_at = time.monotonic() + self._backoff
        self._backoff = min(self._backoff * 2, self._reconnect_max)
        self._reconnect_count += 1

    def _start_task(self, coro: Awaitable[Any], name: str) -> None:
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _send(self, envelope: Envelope) -> None:
        await self.ensure_started()
        queue = self._writer_queue
        if queue is None:
            raise CarrierUnavailable("carrier output is unavailable", reconnectable=True)
        await queue.put(
            encode_envelope(
                envelope,
                max_frame_size=self._outbound_max_frame_size,
            )
        )

    async def request(
        self,
        payload: dict[str, Any],
        *,
        timeout: float | None = 30.0,
        request_id: str | None = None,
    ) -> Envelope:
        """Send one correlated request without exposing its payload to diagnostics."""
        request_id = request_id or uuid.uuid4().hex
        if request_id in self._pending:
            raise CarrierProtocolError("duplicate carrier request_id")
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        submitted = False
        try:
            await self._send(
                Envelope(
                    EnvelopeType.REQUEST,
                    payload=dict(payload),
                    request_id=request_id,
                )
            )
            submitted = True
            if timeout is None:
                return await future
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.CancelledError:
            if submitted:
                await self._send_cancel(request_id=request_id)
            raise
        except (TimeoutError, asyncio.TimeoutError):
            if submitted:
                await self._send_cancel(request_id=request_id)
            raise
        finally:
            self._pending.pop(request_id, None)
            self._schedule_idle_retirement()

    async def subscribe(
        self,
        payload: dict[str, Any],
        *,
        subscription_id: str | None = None,
        replayable: bool = True,
        queue_size: int = 32,
        progress_timeout: float | None = None,
        position: str | int | None = None,
        retain_buffered_on_reconnect: bool = True,
    ) -> CarrierSubscription:
        """Register a bounded subscription-shaped request.

        Replayable subscriptions are re-sent after reconnect with their latest
        received durable ``position``. The operation itself is interpreted by
        the remote Agent Bridge in a later integration phase.
        """
        await self.ensure_started()
        subscription_id = subscription_id or uuid.uuid4().hex
        if subscription_id in self._subscriptions:
            raise CarrierProtocolError("duplicate carrier subscription_id")
        subscription = CarrierSubscription(
            self,
            subscription_id,
            payload,
            replayable=replayable,
            queue_size=queue_size,
            progress_timeout=progress_timeout,
            position=position,
            retain_buffered_on_reconnect=retain_buffered_on_reconnect,
        )
        self._subscriptions[subscription_id] = subscription
        subscription.initializing = False
        try:
            queue = self._writer_queue
            if queue is None:
                await self.ensure_started()
            else:
                await queue.put(
                    encode_envelope(
                        subscription.request_envelope(),
                        max_frame_size=self._outbound_max_frame_size,
                    )
                )
        except asyncio.CancelledError:
            self._subscriptions.pop(subscription_id, None)
            subscription._terminate(
                CarrierUnavailable("carrier subscription start cancelled")
            )
            await self._send_cancel(subscription_id=subscription_id)
            self._schedule_idle_retirement()
            raise
        except Exception:
            self._subscriptions.pop(subscription_id, None)
            subscription._terminate(
                CarrierUnavailable("carrier subscription start failed")
            )
            self._schedule_idle_retirement()
            raise
        return subscription

    async def close_subscription(self, subscription_id: str) -> None:
        subscription = self._subscriptions.pop(subscription_id, None)
        if subscription is None:
            return
        subscription._terminate(CarrierUnavailable("carrier subscription closed"))
        await self._send_cancel(subscription_id=subscription_id)
        self._schedule_idle_retirement()

    async def _send_cancel(
        self,
        *,
        request_id: str | None = None,
        subscription_id: str | None = None,
    ) -> None:
        with contextlib.suppress(
            CarrierError,
            TimeoutError,
            asyncio.TimeoutError,
        ):
            await asyncio.wait_for(
                self._send(
                    Envelope(
                        EnvelopeType.CANCEL,
                        request_id=request_id,
                        subscription_id=subscription_id,
                    )
                ),
                timeout=1.0,
            )

    async def _restore_subscriptions(self, process: _Process) -> None:
        if process.stdin is None:
            raise CarrierUnavailable("carrier process is missing stdin")
        for subscription in list(self._subscriptions.values()):
            if (
                not subscription.closed
                and not subscription.initializing
                and subscription.replayable
            ):
                process.stdin.write(
                    encode_envelope(
                        subscription.request_envelope(),
                        max_frame_size=self._outbound_max_frame_size,
                    )
                )
                await process.stdin.drain()

    async def _writer_loop(
        self,
        process: _Process,
        queue: _BoundedFrameQueue,
        transport_epoch: int,
    ) -> None:
        try:
            while True:
                if (
                    transport_epoch != self._transport_epoch
                    or process.stdin is None
                ):
                    return
                frame = await queue.get()
                process.stdin.write(frame)
                await process.stdin.drain()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._transport_failed(exc, expected_epoch=transport_epoch)

    async def _reader_loop(
        self,
        process: _Process,
        transport_epoch: int,
    ) -> None:
        try:
            while True:
                if (
                    transport_epoch != self._transport_epoch
                    or process.stdout is None
                ):
                    return
                envelope = await read_envelope(
                    process.stdout, max_frame_size=self._max_frame_size
                )
                self._last_received = time.monotonic()
                await self._dispatch(envelope)
        except asyncio.CancelledError:
            raise
        except (EOFError, asyncio.IncompleteReadError):
            await self._transport_failed(
                CarrierUnavailable("carrier transport closed", reconnectable=True),
                expected_epoch=transport_epoch,
            )
        except Exception as exc:
            await self._transport_failed(exc, expected_epoch=transport_epoch)

    async def _dispatch(self, envelope: Envelope) -> None:
        if envelope.type is EnvelopeType.HELLO:
            raise CarrierProtocolError("duplicate carrier hello")
        if envelope.type is EnvelopeType.HEARTBEAT:
            return
        if envelope.subscription_id:
            subscription = self._subscriptions.get(envelope.subscription_id)
            if subscription is not None:
                item: Envelope | CarrierError = envelope
                size = 0
                if envelope.type is EnvelopeType.ERROR:
                    item = CarrierRemoteError(envelope.payload)
                    self._subscriptions.pop(envelope.subscription_id, None)
                    subscription._terminate(item)
                    self._schedule_idle_retirement()
                    return
                else:
                    size = len(
                        encode_envelope(
                            envelope,
                            max_frame_size=self._max_frame_size,
                        )
                    )
                if not subscription._offer(item, size):
                    self._subscriptions.pop(envelope.subscription_id, None)
                    subscription._terminate(
                        CarrierBackpressure(
                            "carrier subscription output queue is full"
                        )
                    )
                    self._state = "degraded"
                    self._last_error = "subscription backpressure"
                    self._start_task(
                        self._send_cancel(
                            subscription_id=envelope.subscription_id
                        ),
                        "ssh-carrier-cancel-subscription",
                    )
                    self._schedule_idle_retirement()
                return
        if envelope.request_id:
            future = self._pending.get(envelope.request_id)
            if future is not None and not future.done():
                if envelope.type is EnvelopeType.ERROR:
                    future.set_exception(CarrierRemoteError(envelope.payload))
                elif envelope.type is EnvelopeType.RESPONSE:
                    future.set_result(envelope)
                else:
                    future.set_exception(
                        CarrierProtocolError("unexpected correlated envelope type")
                    )

    async def _heartbeat_loop(self, transport_epoch: int) -> None:
        try:
            while True:
                await asyncio.sleep(self._heartbeat_interval)
                if transport_epoch != self._transport_epoch:
                    return
                await self._send(
                    Envelope(
                        EnvelopeType.HEARTBEAT,
                        payload={"monotonic": round(time.monotonic(), 3)},
                    )
                )
        except asyncio.CancelledError:
            raise
        except CarrierBackpressure:
            await self._transport_failed(
                CarrierStale("carrier heartbeat output is blocked", reconnectable=True),
                expected_epoch=transport_epoch,
            )
        except CarrierError:
            return

    async def _stale_loop(self, transport_epoch: int) -> None:
        try:
            while True:
                await asyncio.sleep(min(self._heartbeat_interval, self._stale_timeout))
                if transport_epoch != self._transport_epoch:
                    return
                now = time.monotonic()
                if now - self._last_received > self._stale_timeout:
                    await self._transport_failed(
                        CarrierStale("carrier heartbeat expired", reconnectable=True),
                        expected_epoch=transport_epoch,
                    )
                    return
                for subscription_id, subscription in list(
                    self._subscriptions.items()
                ):
                    deadline = subscription.progress_timeout
                    if deadline and now - subscription.last_progress > deadline:
                        self._subscriptions.pop(subscription_id, None)
                        error = CarrierStale(
                            "carrier subscription progress expired",
                            reconnectable=True,
                        )
                        subscription._terminate(error)
                        self._state = "degraded"
                        self._last_error = "subscription progress expired"
                        await self._send_cancel(
                            subscription_id=subscription_id
                        )
                        self._schedule_idle_retirement()
        except asyncio.CancelledError:
            raise

    async def _transport_failed(
        self,
        exc: BaseException,
        *,
        expected_epoch: int | None = None,
    ) -> None:
        async with self._failure_lock:
            if (
                expected_epoch is not None
                and expected_epoch != self._transport_epoch
            ):
                return
            self._transport_epoch += 1
            process = self._process
            if process is None:
                return
            self._process = None
            self._writer_queue = None
            self._outbound_max_frame_size = self._max_frame_size
            self._record_connect_failure(exc)
            current = asyncio.current_task()
            for task in list(self._tasks):
                if task is not current:
                    task.cancel()
            for future in list(self._pending.values()):
                if not future.done():
                    future.set_exception(
                        CarrierUnavailable(
                            "carrier transport was lost", reconnectable=True
                        )
                    )
            for subscription_id, subscription in list(
                self._subscriptions.items()
            ):
                if subscription.replayable:
                    if not subscription.retain_buffered_on_reconnect:
                        subscription._clear_buffer()
                    continue
                self._subscriptions.pop(subscription_id, None)
                subscription._terminate(
                    CarrierUnavailable(
                        "non-replayable subscription transport was lost",
                        reconnectable=True,
                    )
                )
            await self._closer(process)
            if (
                not self._closed
                and (self._logical_clients or self._subscriptions)
                and (self._reconnect_task is None or self._reconnect_task.done())
            ):
                self._reconnect_task = asyncio.create_task(
                    self._reconnect_loop(), name="ssh-carrier-reconnect"
                )
            else:
                self._schedule_idle_retirement()

    async def invalidate_transport(self, reason: str) -> None:
        """Reconnect a live logical carrier after its owning SSH path changes."""
        await self._transport_failed(
            CarrierUnavailable(reason, reconnectable=True)
        )

    @property
    def retired(self) -> bool:
        """Whether this carrier can no longer accept logical clients."""
        return self._closed

    async def _reconnect_loop(self) -> None:
        while not self._closed and (self._logical_clients or self._subscriptions):
            try:
                await self.ensure_started()
                return
            except CarrierError:
                continue

    def _schedule_idle_retirement(self) -> None:
        if (
            self._closed
            or self._logical_clients
            or self._pending
            or self._subscriptions
            or self._idle_task is not None
        ):
            return
        self._idle_task = asyncio.create_task(
            self._retire_when_idle(), name="ssh-carrier-idle"
        )

    async def _retire_when_idle(self) -> None:
        try:
            await asyncio.sleep(self._idle_timeout)
            if not self._logical_clients and not self._pending and not self._subscriptions:
                await self.close()
        except asyncio.CancelledError:
            raise
        finally:
            self._idle_task = None

    async def close(self) -> None:
        """Retire the carrier, closing stdin first and then reaping its SSH tree."""
        if self._closed:
            return
        self._closed = True
        async with self._connect_lock:
            self._state = "closed"
            current = asyncio.current_task()
            if self._idle_task and self._idle_task is not current:
                self._idle_task.cancel()
            if self._reconnect_task and self._reconnect_task is not current:
                self._reconnect_task.cancel()
            for task in list(self._tasks):
                if task is not current:
                    task.cancel()
            for future in list(self._pending.values()):
                if not future.done():
                    future.set_exception(CarrierUnavailable("carrier retired"))
            self._pending.clear()
            for subscription in self._subscriptions.values():
                subscription._terminate(CarrierUnavailable("carrier retired"))
            self._subscriptions.clear()
            process, self._process = self._process, None
            self._writer_queue = None
            self._outbound_max_frame_size = self._max_frame_size
            if process is not None:
                await self._closer(process)
        if self._on_retired is not None:
            self._on_retired(self.connection_identity, self)

    def diagnostics(self) -> dict[str, Any]:
        """Return counts and health only; no command, payload, token, or SSH data."""
        queue = self._writer_queue
        return {
            "state": self._state,
            "logical_clients": self._logical_clients,
            "active_requests": len(self._pending),
            "active_subscriptions": len(self._subscriptions),
            "queued_frames": queue.frame_count if queue else 0,
            "queued_bytes": queue.byte_count if queue else 0,
            "buffered_event_bytes": self._buffer_budget.used,
            "reconnect_count": self._reconnect_count,
            "last_error": self._last_error or None,
        }


RequestHandler = Callable[
    [
        Envelope,
    ],
    Awaitable[Envelope | list[Envelope] | AsyncIterable[Envelope] | None],
]


class StdioCarrierServer:
    """Small bounded remote endpoint that exits when its stdin reaches EOF."""

    def __init__(
        self,
        reader: BinaryIO,
        writer: BinaryIO,
        *,
        handler: RequestHandler | None = None,
        heartbeat_interval: float = 15.0,
        max_frame_size: int = DEFAULT_MAX_FRAME_SIZE,
        max_queued_frames: int = DEFAULT_MAX_QUEUED_FRAMES,
        max_buffered_bytes: int = DEFAULT_MAX_BUFFERED_BYTES,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._handler = handler or self._unsupported
        self._heartbeat_interval = heartbeat_interval
        self._max_frame_size = max_frame_size
        self._outbound_max_frame_size = max_frame_size
        self._queue = _BoundedFrameQueue(max_queued_frames, max_buffered_bytes)
        self._write_lock = asyncio.Lock()
        self._requests: dict[str, asyncio.Task[None]] = {}
        self._closed = False

    async def _unsupported(self, envelope: Envelope) -> Envelope:
        return Envelope(
            EnvelopeType.ERROR,
            request_id=envelope.request_id,
            subscription_id=envelope.subscription_id,
            payload={
                "code": "unsupported_operation",
                "message": "carrier operation is not available",
            },
        )

    async def _read(self) -> Envelope | None:
        return await asyncio.to_thread(
            read_envelope_sync,
            self._reader,
            max_frame_size=self._max_frame_size,
        )

    async def _writer_loop(self) -> None:
        while True:
            frame = await self._queue.get()
            async with self._write_lock:
                await asyncio.to_thread(self._writer.write, frame)
                await asyncio.to_thread(self._writer.flush)

    async def _queue_envelope(self, envelope: Envelope) -> None:
        await self._queue.put(
            encode_envelope(
                envelope,
                max_frame_size=self._outbound_max_frame_size,
            )
        )

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_interval)
            try:
                await self._queue_envelope(Envelope(EnvelopeType.HEARTBEAT))
            except CarrierBackpressure:
                return

    @staticmethod
    def _request_key(envelope: Envelope) -> str | None:
        return envelope.request_id or envelope.subscription_id

    @staticmethod
    def _correlate(response: Envelope, request: Envelope) -> Envelope:
        if response.request_id is not None or response.subscription_id is not None:
            return response
        return Envelope(
            response.type,
            payload=response.payload,
            request_id=request.request_id,
            subscription_id=request.subscription_id,
            replayable=response.replayable,
            position=response.position,
        )

    async def _serve_request(self, envelope: Envelope) -> None:
        key = self._request_key(envelope)
        try:
            result = await self._handler(envelope)
            if result is None:
                return
            if isinstance(result, AsyncIterable):
                async for response in result:
                    await self._queue_envelope(
                        self._correlate(response, envelope)
                    )
            else:
                for response in result if isinstance(result, list) else [result]:
                    await self._queue_envelope(
                        self._correlate(response, envelope)
                    )
        except asyncio.CancelledError:
            await self._queue_envelope(
                Envelope(
                    EnvelopeType.ERROR,
                    request_id=envelope.request_id,
                    subscription_id=envelope.subscription_id,
                    payload={
                        "code": "cancelled",
                        "message": "carrier operation was cancelled",
                    },
                )
            )
            raise
        except CarrierBackpressure:
            return
        except Exception:
            await self._queue_envelope(
                Envelope(
                    EnvelopeType.ERROR,
                    request_id=envelope.request_id,
                    subscription_id=envelope.subscription_id,
                    payload={
                        "code": "operation_failed",
                        "message": "carrier operation failed",
                    },
                )
            )
        finally:
            if key and self._requests.get(key) is asyncio.current_task():
                self._requests.pop(key, None)

    async def run(self) -> None:
        """Negotiate, serve frames, and return promptly on clean stdin EOF."""
        writer_task = asyncio.create_task(
            self._writer_loop(), name="ssh-carrier-server-writer"
        )
        heartbeat_task: asyncio.Task[None] | None = None
        try:
            await self._queue_envelope(
                hello_envelope(max_frame_size=self._max_frame_size)
            )
            peer = await self._read()
            if peer is None:
                return
            validate_hello(peer)
            self._outbound_max_frame_size = negotiated_frame_size(
                peer,
                local_max_frame_size=self._max_frame_size,
            )
            heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(), name="ssh-carrier-server-heartbeat"
            )
            while True:
                envelope = await self._read()
                if envelope is None:
                    return
                if envelope.type is EnvelopeType.HEARTBEAT:
                    await self._queue_envelope(Envelope(EnvelopeType.HEARTBEAT))
                    continue
                if envelope.type is EnvelopeType.CANCEL:
                    key = self._request_key(envelope)
                    task = self._requests.get(key or "")
                    if task is not None:
                        task.cancel()
                    continue
                if envelope.type is not EnvelopeType.REQUEST:
                    await self._queue_envelope(
                        Envelope(
                            EnvelopeType.ERROR,
                            request_id=envelope.request_id,
                            subscription_id=envelope.subscription_id,
                            payload={
                                "code": "unexpected_envelope",
                                "message": "expected request, cancel, or heartbeat",
                            },
                        )
                    )
                    continue
                key = self._request_key(envelope)
                if not key or key in self._requests:
                    await self._queue_envelope(
                        Envelope(
                            EnvelopeType.ERROR,
                            request_id=envelope.request_id,
                            subscription_id=envelope.subscription_id,
                            payload={
                                "code": "invalid_correlation",
                                "message": "request needs a unique correlation id",
                            },
                        )
                    )
                    continue
                task = asyncio.create_task(
                    self._serve_request(envelope),
                    name="ssh-carrier-server-request",
                )
                self._requests[key] = task
        finally:
            self._closed = True
            request_tasks = list(self._requests.values())
            for task in request_tasks:
                task.cancel()
            for task in request_tasks:
                with contextlib.suppress(asyncio.CancelledError, CarrierBackpressure):
                    await task
            if heartbeat_task is not None:
                heartbeat_task.cancel()
            writer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await writer_task

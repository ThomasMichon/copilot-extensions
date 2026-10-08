"""Lifecycle mixin for `PersistentCarrier`: connect, retire, diagnose.

Owns process-handshake/reconnect bookkeeping, logical-client acquire/
release and idle-retirement, and the `close()`/`diagnostics()` surface.
Composed into `PersistentCarrier` alongside `_CarrierRequestMixin` and
`_CarrierTransportMixin` -- see `carrier_persistent.py`.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from .carrier_protocol import (
    DEFAULT_MAX_BUFFERED_BYTES,
    DEFAULT_MAX_FRAME_SIZE,
    DEFAULT_MAX_QUEUED_FRAMES,
    CarrierUnavailable,
    Envelope,
    _Process,
    encode_envelope,
    hello_envelope,
    negotiated_frame_size,
    read_envelope,
    validate_hello,
)
from .carrier_subscriptions import (
    CarrierLease,
    CarrierSubscription,
    _BoundedFrameQueue,
    _BufferBudget,
)

if TYPE_CHECKING:
    from .carrier_persistent import PersistentCarrier


class _CarrierLifecycleMixin:
    """Connection lifecycle: start, reconnect backoff, retire, diagnose."""

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


    async def invalidate_transport(self, reason: str) -> None:
        """Reconnect a live logical carrier after its owning SSH path changes."""
        await self._transport_failed(
            CarrierUnavailable(reason, reconnectable=True)
        )


    @property
    def retired(self) -> bool:
        """Whether this carrier can no longer accept logical clients."""
        return self._closed


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

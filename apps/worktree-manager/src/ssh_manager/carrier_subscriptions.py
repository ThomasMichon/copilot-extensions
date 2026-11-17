"""Subscription and backpressure primitives for the persistent carrier.

Bounded frame/byte accounting (`_BoundedFrameQueue`, `_BufferBudget`) plus
the two small handles callers interact with directly: `CarrierSubscription`
(a bounded replayable event stream) and `CarrierLease` (logical-client
ownership of a shared `PersistentCarrier`).
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from .carrier_protocol import (
    CarrierBackpressure,
    CarrierError,
    CarrierUnavailable,
    Envelope,
    EnvelopeType,
)

if TYPE_CHECKING:
    from .carrier_persistent import PersistentCarrier


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



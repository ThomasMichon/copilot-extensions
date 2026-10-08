"""Bounded stdio carrier server: the remote (child-process) endpoint.

`StdioCarrierServer` speaks the same envelope protocol as
`PersistentCarrier` (see `carrier_protocol.py`) from the other side of
the pipe -- it negotiates a hello, serves correlated requests/
subscriptions through a caller-supplied `RequestHandler`, and exits
promptly on clean stdin EOF.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterable, Awaitable, Callable
from typing import BinaryIO

from .carrier_protocol import (
    DEFAULT_MAX_BUFFERED_BYTES,
    DEFAULT_MAX_FRAME_SIZE,
    DEFAULT_MAX_QUEUED_FRAMES,
    CarrierBackpressure,
    Envelope,
    EnvelopeType,
    encode_envelope,
    hello_envelope,
    negotiated_frame_size,
    read_envelope_sync,
    validate_hello,
)
from .carrier_subscriptions import _BoundedFrameQueue

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

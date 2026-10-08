"""Transport I/O mixin for `PersistentCarrier`: frames in, frames out.

Owns the reader/writer loops, heartbeat/staleness monitoring, envelope
dispatch to pending requests and subscriptions, and transport-failure
handling. Composed into `PersistentCarrier` alongside
`_CarrierLifecycleMixin` and `_CarrierRequestMixin` -- see
`carrier_persistent.py`.
"""

from __future__ import annotations

import asyncio
import time

from .carrier_protocol import (
    CarrierBackpressure,
    CarrierError,
    CarrierProtocolError,
    CarrierRemoteError,
    CarrierStale,
    CarrierUnavailable,
    Envelope,
    EnvelopeType,
    _Process,
    encode_envelope,
    read_envelope,
)
from .carrier_subscriptions import _BoundedFrameQueue


class _CarrierTransportMixin:
    """Steady-state frame I/O: writer/reader loops, heartbeat, dispatch."""

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


    async def _reconnect_loop(self) -> None:
        while not self._closed and (self._logical_clients or self._subscriptions):
            try:
                await self.ensure_started()
                return
            except CarrierError:
                continue


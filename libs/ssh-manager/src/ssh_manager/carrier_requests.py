"""Request/subscription mixin for `PersistentCarrier`.

Owns correlated request/response, subscription registration and
restoration-after-reconnect, and best-effort cancel notification.
Composed into `PersistentCarrier` alongside `_CarrierLifecycleMixin`
and `_CarrierTransportMixin` -- see `carrier_persistent.py`.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
import uuid
from typing import TYPE_CHECKING, Any, cast

from .carrier_protocol import (
    CarrierError,
    CarrierProtocolError,
    CarrierUnavailable,
    Envelope,
    EnvelopeType,
    _Process,
    encode_envelope,
)
from .carrier_subscriptions import CarrierSubscription, _BoundedFrameQueue

if TYPE_CHECKING:
    from .carrier_persistent import PersistentCarrier


class _CarrierRequestMixin:
    """Correlated requests and subscription lifecycle over the transport."""

    _outbound_max_frame_size: int
    _pending: dict[str, asyncio.Future[Envelope]]
    _request_epochs: dict[str, int]
    _transport_epoch: int
    _writer_queue: _BoundedFrameQueue | None
    _subscriptions: dict[str, CarrierSubscription]
    ensure_started: Callable[[], Awaitable[None]]
    _schedule_idle_retirement: Callable[[], None]
    _send: Callable[[Envelope], Awaitable[None]]

    def encode_request(self, payload: dict[str, Any], *, request_id: str) -> bytes:
        """Encode without queueing, using the current negotiated outbound limit."""
        return encode_envelope(
            Envelope(EnvelopeType.REQUEST, payload=dict(payload), request_id=request_id),
            max_frame_size=self._outbound_max_frame_size,
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
        await self.ensure_started()
        if request_id in self._pending:
            raise CarrierProtocolError("duplicate carrier request_id")
        future: asyncio.Future[Envelope] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        self._request_epochs[request_id] = self._transport_epoch
        submitted = False
        try:
            queue = self._writer_queue
            if queue is None:
                raise CarrierUnavailable("carrier output is unavailable", reconnectable=True)
            await queue.put(
                self.encode_request(payload, request_id=request_id)
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
            self._request_epochs.pop(request_id, None)
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
            cast("PersistentCarrier", self),
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

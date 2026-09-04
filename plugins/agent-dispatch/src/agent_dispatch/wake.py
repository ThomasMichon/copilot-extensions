"""Durable wake-outbox drain loop."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from .events import EventBus
from .queue import (
    DEFAULT_WAKE_DELIVERY_LEASE_SECONDS,
    TaskError,
    TaskQueue,
    WakeOperation,
)

DeliverWake = Callable[[str, str, str, str | None, str], bool]
WakeActive = Callable[[], bool]
log = logging.getLogger(__name__)


def _next_wait_interval(
    *, has_pending: bool, retry_interval: float, idle_interval: float
) -> float:
    """Use the short cadence only while a delayed retry remains pending."""
    return retry_interval if has_pending else idle_interval


def _default_deliver(
    owner: str,
    owner_session_id: str,
    task_id: str,
    message: str | None,
    idempotency_key: str,
) -> bool:
    from . import bridge

    return bridge.resume_steered_owner(
        owner,
        task_id,
        message,
        owner_session_id=owner_session_id,
        idempotency_key=idempotency_key,
    )


def _event(wake: WakeOperation) -> dict:
    return {
        "type": "task.wake",
        "task_id": wake.task_id,
        "owner": wake.owner,
        "wake_id": wake.id,
        "wake_status": wake.status,
        "attempts": wake.attempts,
        "not_before": wake.not_before,
        "last_error": wake.last_error,
    }


async def drain_wake_outbox(
    queue: TaskQueue,
    bus: EventBus,
    *,
    interval: float = 0.25,
    deliver: DeliverWake = _default_deliver,
    max_attempts: int = 8,
    retry_base: float = 1.0,
    is_active: WakeActive | None = None,
    delivery_lease: float = DEFAULT_WAKE_DELIVERY_LEASE_SECONDS,
    idle_interval: float | None = None,
    wake_signal: asyncio.Event | None = None,
) -> None:
    """Drain durable wake operations until cancelled.

    Only the active routing owner drains. Expired ``delivering`` rows are
    recovered continuously so an aborted cutover cannot strand one. Each retry
    uses the outbox row id as the downstream idempotency key; task generation,
    owner-session identity, status, and latest-wake identity fence stale work.
    """
    idle_interval = interval if idle_interval is None else idle_interval

    async def _wait(delay: float) -> None:
        if wake_signal is None:
            await asyncio.sleep(delay)
            return
        try:
            await asyncio.wait_for(wake_signal.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass
        wake_signal.clear()

    while True:
        if is_active is not None:
            try:
                active = await asyncio.to_thread(is_active)
            except Exception:
                log.warning("wake active-route check failed", exc_info=True)
                active = False
            if not active:
                await _wait(idle_interval)
                continue
        await asyncio.to_thread(
            queue.recover_inflight_wakes, lease_seconds=delivery_lease
        )
        wake = await asyncio.to_thread(
            queue.claim_due_wake, lease_seconds=delivery_lease
        )
        if wake is None:
            has_pending = await asyncio.to_thread(queue.has_pending_wakes)
            await _wait(
                _next_wait_interval(
                    has_pending=has_pending,
                    retry_interval=interval,
                    idle_interval=idle_interval,
                )
            )
            continue
        try:
            delivered = await asyncio.to_thread(
                deliver,
                wake.owner,
                wake.owner_session_id or "",
                wake.task_id,
                wake.message,
                wake.id,
            )
            delivery_error = None if delivered else "bridge delivery unavailable"
        except Exception as exc:
            log.warning("wake delivery raised for %s", wake.id, exc_info=True)
            delivered = False
            delivery_error = f"wake delivery error: {type(exc).__name__}"
        try:
            result = await asyncio.to_thread(
                queue.finish_wake,
                wake.id,
                wake.delivery_token or "",
                delivered=delivered,
                error=delivery_error,
                max_attempts=max_attempts,
                retry_base=retry_base,
            )
        except TaskError:
            log.info("wake delivery ownership changed for %s", wake.id)
            continue
        bus.publish(_event(result))

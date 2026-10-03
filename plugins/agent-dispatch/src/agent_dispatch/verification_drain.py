"""Durable background drain for submitted-verification requests."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from .events import EventBus
from .queue import TaskError, TaskQueue
from .producers.evaluator import VERIFICATION_REQUEST_DELIVERY_LEASE
from .verification import evaluate_submitted_task

log = logging.getLogger(__name__)

WakeActive = Callable[[], bool]


def _scheduled_wait_interval(
    *,
    now: float,
    next_not_before: float | None,
    retry_interval: float,
    idle_interval: float,
) -> float:
    if next_not_before is None:
        return idle_interval
    if next_not_before <= now:
        return retry_interval
    return min(idle_interval, max(retry_interval, next_not_before - now))


async def drain_verification_requests(
    queue: TaskQueue,
    bus: EventBus,
    *,
    interval: float = 0.25,
    max_attempts: int = 8,
    retry_base: float = 1.0,
    delivery_lease: float = VERIFICATION_REQUEST_DELIVERY_LEASE,
    is_active: WakeActive | None = None,
    signal: asyncio.Queue[None] | None = None,
    idle_interval: float | None = None,
    stop_event: asyncio.Event | None = None,
) -> None:
    """Drain pending submitted-verification requests until cancelled.

    ``stop_event``, when provided, lets a caller request a *cooperative*
    stop instead of ``Task.cancel()``. Every synchronous DB call in this
    loop runs via ``asyncio.to_thread``, and cancelling the task that is
    currently awaiting one of those calls only cancels the *awaiting*
    coroutine -- the underlying OS thread keeps running the real
    (synchronous) call to completion regardless, racing against whatever the
    caller does next (e.g. a test fixture tearing down the temp directory
    the SQLite db lives in). Setting ``stop_event`` and awaiting this
    coroutine to completion lets the loop finish its current ``to_thread``
    call, notice the stop request at its next checkpoint, and return on its
    own -- so by the time the await returns, no background thread is still
    touching the queue. ``task.cancel()`` remains a legitimate last resort
    for a hung/unresponsive loop.
    """
    idle_interval = interval if idle_interval is None else idle_interval

    def _stopping() -> bool:
        return stop_event is not None and stop_event.is_set()

    async def _wait(delay: float) -> None:
        if _stopping():
            return
        if signal is None and stop_event is None:
            await asyncio.sleep(delay)
            return
        waiters = [asyncio.ensure_future(asyncio.sleep(delay))]
        if signal is not None:
            waiters.append(asyncio.ensure_future(signal.get()))
        if stop_event is not None:
            waiters.append(asyncio.ensure_future(stop_event.wait()))
        try:
            await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for waiter in waiters:
                if not waiter.done():
                    waiter.cancel()

    while True:
        if _stopping():
            return
        if is_active is not None:
            try:
                active = await asyncio.to_thread(is_active)
            except Exception:
                log.warning("verification active-route check failed", exc_info=True)
                active = False
            if not active:
                await _wait(interval)
                continue
        if _stopping():
            return
        await asyncio.to_thread(
            queue.recover_inflight_verification_requests,
            lease_seconds=delivery_lease,
        )
        if _stopping():
            return
        request = await asyncio.to_thread(
            queue.claim_due_verification_request,
            lease_seconds=delivery_lease,
        )
        if request is None:
            now_value = await asyncio.to_thread(queue._now, None)
            next_not_before = await asyncio.to_thread(
                queue.next_pending_verification_not_before
            )
            await _wait(
                _scheduled_wait_interval(
                    now=now_value,
                    next_not_before=next_not_before,
                    retry_interval=interval,
                    idle_interval=idle_interval,
                )
            )
            continue
        try:
            report = await asyncio.to_thread(
                evaluate_submitted_task,
                queue,
                request.task_id,
                bus=bus,
                trigger=request.trigger,
            )
            reason = str(report.get("reason") or "")
            delivered = bool(
                report.get("applied") is not None
                or reason == "submitted verification evaluated"
            )
            error = None if delivered else reason or "verification did not reach a terminal report"
        except TaskError as exc:
            current = await asyncio.to_thread(queue.get, request.task_id)
            delivered = not bool(
                current is not None
                and current.status == "submitted"
                and current.require_verification
                and current.generation == request.generation
                and current.evaluator_ref
            )
            log.info(
                "verification request %s %s: %s",
                request.id,
                "became stale" if delivered else "will retry",
                exc,
            )
            error = str(exc)
        except Exception as exc:  # noqa: BLE001 -- report and retry boundedly
            log.warning("verification request %s raised", request.id, exc_info=True)
            delivered = False
            error = f"verification error: {type(exc).__name__}"
        try:
            await asyncio.to_thread(
                queue.finish_verification_request,
                request.id,
                request.delivery_token or "",
                delivered=delivered,
                error=error,
                max_attempts=max_attempts,
                retry_base=retry_base,
            )
        except TaskError:
            log.info("verification request ownership changed for %s", request.id)

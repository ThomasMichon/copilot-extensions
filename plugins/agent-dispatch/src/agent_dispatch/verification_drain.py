"""Durable background drain for submitted-verification requests."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from .events import EventBus
from .queue import TaskError, TaskQueue
from .producers.evaluator import MAX_SCRIPT_EVALUATOR_TIMEOUT
from .verification import evaluate_submitted_task

log = logging.getLogger(__name__)

WakeActive = Callable[[], bool]


async def drain_verification_requests(
    queue: TaskQueue,
    bus: EventBus,
    *,
    interval: float = 0.25,
    max_attempts: int = 8,
    retry_base: float = 1.0,
    delivery_lease: float = MAX_SCRIPT_EVALUATOR_TIMEOUT + 60.0,
    is_active: WakeActive | None = None,
    signal: asyncio.Queue[None] | None = None,
) -> None:
    """Drain pending submitted-verification requests until cancelled."""

    async def _wait(delay: float) -> None:
        if signal is None:
            await asyncio.sleep(delay)
            return
        try:
            await asyncio.wait_for(signal.get(), timeout=delay)
        except asyncio.TimeoutError:
            pass

    while True:
        if is_active is not None:
            try:
                active = await asyncio.to_thread(is_active)
            except Exception:
                log.warning("verification active-route check failed", exc_info=True)
                active = False
            if not active:
                await _wait(interval)
                continue
        await asyncio.to_thread(
            queue.recover_inflight_verification_requests,
            lease_seconds=delivery_lease,
        )
        request = await asyncio.to_thread(
            queue.claim_due_verification_request,
            lease_seconds=delivery_lease,
        )
        if request is None:
            has_pending = await asyncio.to_thread(queue.has_pending_verification_requests)
            await _wait(interval if has_pending else interval)
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
            log.info("verification request %s became stale: %s", request.id, exc)
            delivered = True
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

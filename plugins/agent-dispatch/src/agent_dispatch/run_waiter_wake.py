"""Durable delivery loop for detached-waiter worktree wakes."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from .queue import TaskError, TaskQueue

DeliverWake = Callable[[str, str, str, str, str, str | None], bool]
ReleaseClaim = Callable[[str, str | None, str, str | None], object | None]
WakeActive = Callable[[], bool]
log = logging.getLogger(__name__)


def _worktree_id(worktree: str) -> str:
    _machine, sep, tail = worktree.partition("/")
    return tail if sep and tail else worktree


def _default_deliver(
    owner: str,
    task_id: str,
    message: str,
    wake_id: str,
    sender: str,
    owner_session_id: str | None,
) -> bool:
    from . import bridge

    return bridge.resume_steered_owner(
        owner,
        task_id,
        message,
        owner_session_id=owner_session_id,
        idempotency_key=wake_id,
    )


async def drain_run_waiter_wakes(
    queue: TaskQueue,
    *,
    interval: float = 0.25,
    deliver: DeliverWake = _default_deliver,
    release_claim: ReleaseClaim | None = None,
    max_attempts: int = 8,
    retry_base: float = 1.0,
    is_active: WakeActive | None = None,
    delivery_lease: float = 60.0,
) -> None:
    """Drain pending detached-waiter wake deliveries until cancelled."""
    while True:
        if is_active is not None:
            try:
                active = await asyncio.to_thread(is_active)
            except Exception:
                log.warning("run-waiter wake active-route check failed", exc_info=True)
                active = False
            if not active:
                await asyncio.sleep(interval)
                continue
        await asyncio.to_thread(
            queue.recover_inflight_run_waiter_wakes,
            lease_seconds=delivery_lease,
        )
        wake = await asyncio.to_thread(
            queue.claim_due_run_waiter_wake,
            lease_seconds=delivery_lease,
        )
        if wake is None:
            has_pending = await asyncio.to_thread(queue.has_pending_run_waiter_wakes)
            await asyncio.sleep(interval if has_pending else max(interval, 5.0))
            continue
        try:
            delivered = await asyncio.to_thread(
                deliver,
                wake.owner,
                wake.task_id,
                wake.message,
                wake.id,
                wake.sender,
                wake.owner_session_id,
            )
            delivery_error = None if delivered else "bridge delivery unavailable"
        except Exception as exc:
            log.warning("run-waiter wake delivery raised for %s", wake.id, exc_info=True)
            delivered = False
            delivery_error = f"wake delivery error: {type(exc).__name__}"
        if delivered and release_claim is not None:
            try:
                    task = await asyncio.to_thread(queue.get, wake.task_id)
                    released = await asyncio.to_thread(
                        release_claim,
                        wake.task_id,
                        wake.waiter_host,
                        _worktree_id(wake.resume_worktree),
                        None if task is None else task.repo,
                    )
            except Exception:
                log.warning(
                    "run-waiter wake claim release failed for %s",
                    wake.id,
                    exc_info=True,
                )
                delivered = False
                delivery_error = "claim release failed"
            else:
                if released is None:
                    delivered = False
                    delivery_error = "claim release failed"
        try:
            await asyncio.to_thread(
                queue.finish_run_waiter_wake,
                wake.id,
                wake.delivery_token or "",
                delivered=delivered,
                error=delivery_error,
                max_attempts=max_attempts,
                retry_base=retry_base,
            )
        except TaskError:
            log.info("run-waiter wake delivery ownership changed for %s", wake.id)
            continue

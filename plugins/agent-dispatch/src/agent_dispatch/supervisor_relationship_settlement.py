"""Settle a completed task's exact retained worktree relationship before release."""

from __future__ import annotations

import logging
from typing import Any

from .queue import Status

log = logging.getLogger(__name__)


def settle_completed_relationship(supervisor: Any, reservation: dict, task: dict) -> bool:
    """Close the reservation's original relationship once its task is ``COMPLETED``.

    Only the coordinator's existing reconcile route revisits the original ground
    owner; this never supplies a receipt. Returns ``False`` while the claim is
    retained so the caller keeps the reservation and retries next cycle instead
    of concluding a workspace whose task claim is still outstanding.
    """
    event_id = reservation.get("task_relationship_event_id")
    if type(event_id) is not int or task.get("status") != Status.COMPLETED:
        return True
    try:
        settled = supervisor.client.reconcile_task_relationship(task["id"], event_id=event_id)
    except Exception:
        log.exception(
            "completed task %s relationship %s retained; reservation %s kept for retry",
            task.get("id"),
            event_id,
            reservation.get("key"),
        )
        return False
    state = settled.get("state") if isinstance(settled, dict) else None
    if state in {"closed", "absent"}:
        return True
    log.warning(
        "completed task %s relationship %s reported %r; reservation %s kept for retry",
        task.get("id"),
        event_id,
        state,
        reservation.get("key"),
    )
    return False

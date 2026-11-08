"""Settle a completed task's exact retained worktree relationship before release."""

from __future__ import annotations

import json
import logging
from typing import Any

from .client import DispatchError
from .queue import SpawnState, Status
from .supervisor_conclusion import _CONCLUSION_HELD, _CONCLUSION_PENDING

#: The Worktrees conclusion reason for a workspace preserved by a live claim.
_CLAIM_BLOCKED_REASON = "outstanding-obligations"
#: Bound the per-cycle held-cleanup revisits like other conclusion passes.
_REARM_PER_CYCLE = 10

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


def _claim_blocked_rearm(payload: dict) -> dict | None:
    """Return a pending retry payload when only a live claim preserved cleanup."""
    component = payload.get("worktree_cleanup")
    if isinstance(component, dict):
        outcome = component.get("outcome")
        if (
            component.get("state") != _CONCLUSION_HELD
            or not isinstance(outcome, dict)
            or outcome.get("reason") != _CLAIM_BLOCKED_REASON
        ):
            return None
        return {
            **payload,
            "action": "pending",
            "worktree_cleanup": {
                **component,
                "state": _CONCLUSION_PENDING,
                "attempts": 0,
                "next_attempt_at": 0,
            },
        }
    if payload.get("reason") != _CLAIM_BLOCKED_REASON:
        return None
    return {
        **payload,
        "action": "pending",
        "reason": "relationship-settled",
        "attempts": 0,
        "next_attempt_at": 0,
    }


def rearm_claim_held_cleanup(supervisor: Any) -> int:
    """Re-pend retired cleanup that Worktrees preserved only for a live task claim.

    Once the task is ``COMPLETED`` and its exact relationship closes, the
    preserved workspace becomes concludable; the ordinary cleanup retry then
    concludes it. Other held reasons stay held. Returns the count re-armed.
    """
    rearmed = 0
    held = supervisor._pool_reservations(
        state=f"{SpawnState.FAILED},{SpawnState.SETTLED}",
        conclusion_state=_CONCLUSION_HELD,
    )
    for res in held:
        if rearmed >= _REARM_PER_CYCLE:
            break
        if type(res.get("task_relationship_event_id")) is not int:
            continue
        pending = _claim_blocked_rearm(supervisor._conclusion_retry_payload(res))
        if pending is None:
            continue
        try:
            task = supervisor.client.get(res["task_id"])
        except DispatchError:
            continue
        if task.get("status") != Status.COMPLETED or not supervisor._matches_pool(task):
            continue
        if not settle_completed_relationship(supervisor, res, task):
            continue
        try:
            supervisor._conclusion_client.record_spawn_conclusion(
                res["key"],
                conclusion_state=_CONCLUSION_PENDING,
                conclusion_detail=json.dumps(pending, sort_keys=True, separators=(",", ":")),
            )
        except DispatchError:
            log.exception("could not re-arm claim-held cleanup for %s", res["key"])
            continue
        rearmed += 1
    return rearmed

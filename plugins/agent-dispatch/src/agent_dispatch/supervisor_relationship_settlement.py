"""Settle a completed task's exact retained worktree relationship before release."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .client import DispatchError
from .queue import SpawnState, Status
from .queue_spawn_conclusion import CLAIM_HELD_REARM_CAPABILITY, claim_blocked_rearm_payload
from .supervisor_conclusion import _CONCLUSION_HELD

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


def _rearm_supported(supervisor: Any) -> bool:
    """Check the coordinator advertises the fenced re-arm before settling anything.

    An older or rolled-back coordinator lacks the route; cleanup then stays
    held (never lost) and remains recoverable through control-authorized
    ``recover-held-cleanup`` until the coordinator is upgraded.
    """
    try:
        health = supervisor.client.health()
    except (DispatchError, httpx.TransportError):
        return False
    if isinstance(health, dict) and health.get(CLAIM_HELD_REARM_CAPABILITY) == 1:
        return True
    log.warning(
        "coordinator does not advertise %s; claim-held cleanup stays held "
        "(recover through recover-held-cleanup or upgrade the coordinator)",
        CLAIM_HELD_REARM_CAPABILITY,
    )
    return False


def rearm_claim_held_cleanup(supervisor: Any) -> int:
    """Re-pend retired cleanup that Worktrees preserved only for a live task claim.

    Once the task is ``COMPLETED`` and its exact relationship closes, the
    preserved workspace becomes concludable; the ordinary cleanup retry then
    concludes it. Other held reasons stay held. Each cycle revisits at most
    ``_REARM_PER_CYCLE`` candidates, counting failures, starting after the
    previous cycle's last candidate so a failing prefix cannot starve the rest.
    Returns the count re-armed.
    """
    held = supervisor._pool_reservations(
        state=f"{SpawnState.FAILED},{SpawnState.SETTLED}",
        conclusion_state=_CONCLUSION_HELD,
    )
    candidates = []
    for res in held:
        if type(res.get("task_relationship_event_id")) is not int:
            continue
        if claim_blocked_rearm_payload(supervisor._conclusion_retry_payload(res)) is not None:
            candidates.append((str(res["key"]), res))
    if not candidates or not _rearm_supported(supervisor):
        return 0
    candidates.sort(key=lambda item: item[0])
    cursor = getattr(supervisor, "_claim_held_rearm_cursor", "")
    split = next((i for i, item in enumerate(candidates) if item[0] > cursor), 0)
    rearmed = 0
    for key, res in (candidates[split:] + candidates[:split])[:_REARM_PER_CYCLE]:
        supervisor._claim_held_rearm_cursor = key
        try:
            task = supervisor.client.get(res["task_id"])
        except (DispatchError, httpx.TransportError):
            continue
        if task.get("status") != Status.COMPLETED or not supervisor._matches_pool(task):
            continue
        if not settle_completed_relationship(supervisor, res, task):
            continue
        try:
            supervisor._conclusion_client.rearm_claim_held_conclusion(
                key, expected_detail=res["conclusion_detail"],
            )
        except (DispatchError, httpx.TransportError):
            log.exception("could not re-arm claim-held cleanup for %s", key)
            continue
        rearmed += 1
    return rearmed

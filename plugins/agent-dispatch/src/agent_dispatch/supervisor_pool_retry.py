"""Reconcile original retry completion after acceptance, completion or exhaustion."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

from .action_controls import action_inhibited
from .client_errors import DispatchError
from . import pool_startup_delivery
from .queue_records import SpawnState, Status

if TYPE_CHECKING:
    from .supervisor import Supervisor

log = logging.getLogger("agent-dispatch.supervisor")


def reconcile_retry_completion(
    owner: Supervisor, reservation: dict, task: dict, *, observe_only: bool = False,
) -> bool | None:
    """Observe only; None means unrelated, False retains an unresolved caller."""
    if (
        reservation.get("driver") != "cli" or reservation.get("startup_complete") is not False
        or task.get("status") not in {Status.STARTED, Status.SUBMITTED, Status.COMPLETED, Status.QUEUED}
        or task.get("status") == Status.QUEUED and not reservation.get("release_requested")
    ):
        return None
    try:
        snapshot = owner.client.pool_startup_snapshot(task["id"])
        if snapshot is None or snapshot["budget"].get("retry_invocation") is None:
            return None
        if observe_only:
            return False
        budget, current, assigned = snapshot["budget"], snapshot["reservation"], snapshot["task"]
        invocation = budget["retry_invocation"]
        if (
            current["key"] != reservation["key"] or current["startup_revision"] != reservation["startup_revision"]
            or current["startup_complete"] is not False or current.get("conversation_retired")
            or current.get("reserved_by") != owner.supervisor_id
            or assigned.get("hold_reason") is not None or assigned.get("awaiting_steer")
            or assigned["generation"] != task["generation"]
            or current.get("cooling_token") is not None or current.get("startup_fenced")
            or current["state"] not in {SpawnState.SPAWNED, SpawnState.RELEASING}
            or action_inhibited("worker-admission", logger=log)
        ):
            return False
        governance = owner._recheck_governance("pre-mutation:pool-retry-completion")
        if governance is not None and governance.get("status") != "ready":
            return False
        kind, detail = pool_startup_delivery.deliver_retry(invocation, observe_only=True)
        if kind == "startup_unknown":
            log.info("original pool retry completion remains unknown for %s", current["key"])
            return False
        owner.client.complete_pool_startup(
            task["id"], current["key"], generation=budget["generation"],
            startup_revision=current["startup_revision"], native_id=snapshot["native_id"],
            worktree=current["worktree"], machine=current["execution_machine"],
            reserved_by=owner.supervisor_id, outcome_kind=kind, detail=detail,
        )
        return True
    except (DispatchError, httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        log.warning("original pool retry completion is unavailable for %s; no send replayed",
                    reservation.get("key"), exc_info=True)
        return False

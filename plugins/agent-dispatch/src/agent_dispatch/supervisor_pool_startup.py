"""Consume durable allocator startup decisions through existing supervision."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

import httpx

from .action_controls import action_inhibited
from .client_errors import DispatchError
from .queue_records import SpawnState, Status
from .tracking import LIVE

if TYPE_CHECKING:
    from .supervisor import Supervisor

log = logging.getLogger("agent-dispatch.supervisor")


def supervise_pool_startup(
    owner: Supervisor, reservation: dict, task: dict, *,
    now: float | None = None, observe_only: bool = False,
) -> bool | None:
    """Return None for legacy work, otherwise retain this startup's custody.

    False means a managed/unknown candidate was retained without a successful
    action. True means a negotiated decision or notification completed, never
    that the worker accepted the task.
    """
    from .supervisor_pool_retry import reconcile_retry_completion
    completion = reconcile_retry_completion(owner, reservation, task, observe_only=observe_only)
    if completion is not None:
        return completion
    if (
        reservation.get("driver") != "cli"
        or task.get("status") != Status.CLAIMED or task.get("owner") is not None
    ):
        return None
    try:
        snapshot = owner.client.pool_startup_snapshot(task["id"])
        if snapshot is None:
            return None
        budget, current, assigned = (
            snapshot["budget"], snapshot["reservation"], snapshot["task"],
        )
        if (
            current["key"] != reservation["key"]
            or current["startup_revision"] != reservation["startup_revision"]
            or assigned["generation"] != task["generation"]
            or budget["generation"] != assigned["generation"]
            or assigned["status"] != Status.CLAIMED or assigned["owner"] is not None
            or assigned.get("hold_reason") is not None or assigned.get("awaiting_steer")
            or budget["exhausted_at"] is not None or budget["startup_deadline"] is None
            or current["state"] not in {SpawnState.RESERVING, SpawnState.SPAWNED}
            or current.get("driver") != "cli"
            or current.get("release_requested") or current.get("startup_fenced")
            or current.get("conversation_retired") or current.get("cooling_token") is not None
            or current.get("conclusion_state") == "pending"
        ):
            return False
        if observe_only or current.get("reserved_by") != owner.supervisor_id:
            return False
        if action_inhibited("worker-admission", logger=log):
            return False
        governance = owner._recheck_governance("pre-mutation:pool-startup")
        if governance is not None and governance.get("status") != "ready":
            log.warning("pool startup retained for %s: installation governance is not ready", current["key"])
            return False
        ts = time.time() if now is None else now
        arguments = {
            "generation": budget["generation"], "startup_revision": current["startup_revision"],
        }
        deadline = budget["decision_deadline"]
        if deadline is None:
            deadline = budget["startup_deadline"]
        completed_failure = (
            current["startup_complete"] is True
            and budget["outcome_revision"] == current["startup_revision"]
            and budget["outcome_kind"] in {"startup_failed", "startup_rejected"}
        )
        exhausted_attempts = (
            completed_failure and budget["reachable_at"] is None
            and budget["startup_attempts"] - budget["episode_start_attempt"] >= 3
        )
        if ts >= deadline or exhausted_attempts:
            owner.client.exhaust_pool_startup(
                task["id"], current["key"], **arguments,
                reason="deadline" if ts >= deadline else "attempts",
            )
            log.info("pool startup exhausted for %s; retained capacity requires retirement", current["key"])
            return True
        invocation = budget.get("retry_invocation")
        pending_retry = invocation is not None and current["startup_complete"] is not True
        if not (completed_failure or pending_retry) and (
            budget["outcome_kind"] is not None or current["startup_complete"] is not True
        ):
            return False
        if not (completed_failure or pending_retry) and (
            budget["reachable_at"] is not None and ts < budget["next_nudge_at"]
        ):
            return False
        native, worktree, machine = (
            snapshot["native_id"], current.get("worktree"), current.get("execution_machine"),
        )
        if not all(isinstance(value, str) and value.strip() for value in (native, worktree, machine)):
            log.info("pool startup retained for %s: exact native/workspace/host is unavailable", current["key"])
            return False
        if pending_retry:
            from .pool_startup_delivery import deliver_retry
            kind, detail = deliver_retry(invocation, observe_only=True)
            if kind == "startup_unknown":
                log.info("pool retry remains unknown for %s; no invocation replayed", current["key"])
                return False
            owner.client.complete_pool_startup(
                task["id"], current["key"], native_id=native, worktree=worktree, machine=machine,
                reserved_by=owner.supervisor_id, outcome_kind=kind, detail=detail, **arguments,
            )
            return True
        try:
            verdict = owner.verdict_fn(worktree, machine, native)
            session = owner.liveness_fn(worktree, machine) if verdict == LIVE else None
        except Exception:
            log.warning("pool startup probe unavailable for %s; native remains unknown", current["key"],
                        exc_info=True)
            return False
        if not isinstance(session, dict) or (
            session.get("session_id") != native or session.get("worktree_id") != worktree
        ):
            return False
        if completed_failure:
            from .pool_startup_delivery import deliver_retry
            pending = owner.client.retry_pool_startup(
                task["id"], current["key"], native_id=native, worktree=worktree,
                machine=machine, reserved_by=owner.supervisor_id, **arguments,
            )
            invocation = pending["invocation"]
            kind, detail = deliver_retry(invocation, observe_only=False)
            owner.client.complete_pool_startup(
                task["id"], current["key"], native_id=native, worktree=worktree, machine=machine,
                reserved_by=owner.supervisor_id, outcome_kind=kind, detail=detail,
                generation=budget["generation"], startup_revision=invocation["startup_revision"],
            )
            if kind == "startup_unknown":
                log.warning("pool retry delivery remains unknown for %s; retained without replay", current["key"])
                return False
            return True
        if budget["reachable_at"] is None:
            owner.client.observe_pool_reachable(
                task["id"], current["key"], native_id=native, **arguments,
            )
            return True
        slot = owner.client.take_pool_nudge(
            task["id"], current["key"], native_id=native, **arguments,
        )
        if slot is None:
            return False
        from . import bridge

        message = (
            f"[agent-dispatch] Task {task['id']} is assigned but has not been accepted. "
            f"Re-read its current generation {budget['generation']} and the assigned worker contract. "
            "Explicit acceptance must use the negotiated pool-start operation; "
            "a notification is not acceptance. Record a blocker if you cannot decide. "
            "Do not acquire a second claim or release another owner's assignment."
        )
        delivered = bridge.send_nudge(
            worktree, message, machine=machine, expected_session_id=native,
            idempotency_key=(
                f"pool-startup:{current['key']}:{budget['generation']}:"
                f"{current['startup_revision']}:{slot['next_nudge_at']}"
            ),
        )
        if not delivered:
            log.warning("pool startup nudge delivery is unknown for %s; slot is not acceptance", current["key"])
        return delivered
    except (DispatchError, httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        log.warning("pool startup supervision unavailable or changed for %s; legacy recovery refused",
                    reservation.get("key"), exc_info=True)
        return False

"""Consume durable allocator startup decisions through existing supervision."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

import httpx

from .action_controls import action_inhibited
from .client_errors import DispatchError
from .pool_startup_retry_contract import driver_binding
from .queue_records import SpawnState, Status, TaskError
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
        reservation.get("driver") not in {"cli", "ordinary-bridge"}
        or task.get("status") != Status.CLAIMED or task.get("owner") is not None
    ):
        return None
    try:
        snapshot = owner.client.pool_startup_snapshot(task["id"])
        if snapshot is None:
            return None
        if snapshot.get("worker_decline") is not None:
            return _close_decline(owner, reservation, task, snapshot, observe_only=observe_only)
        budget, current, assigned = (
            snapshot["budget"], snapshot["reservation"], snapshot["task"],
        )
        ordinary = current.get("driver") == "ordinary-bridge"
        if ordinary:
            contract = owner.client.pool_startup_contract()
            if contract.get("ordinary_same_native_retry") is not True or (
                "ordinary-bridge" not in contract.get("supported_drivers", [])
            ):
                return False
        if (
            current["key"] != reservation["key"]
            or current["startup_revision"] != reservation["startup_revision"]
            or assigned["generation"] != task["generation"]
            or budget["generation"] != assigned["generation"]
            or assigned["status"] != Status.CLAIMED or assigned["owner"] is not None
            or assigned.get("hold_reason") is not None or assigned.get("awaiting_steer")
            or budget["exhausted_at"] is not None or budget["startup_deadline"] is None
            or current["state"] not in {SpawnState.RESERVING, SpawnState.SPAWNED}
            or current.get("driver") != reservation.get("driver")
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
        driver = driver_binding(current, native)
        if pending_retry:
            from .pool_startup_delivery import deliver_retry
            kind, detail = deliver_retry(invocation, observe_only=True)
            if kind == "startup_unknown":
                log.info("pool retry remains unknown for %s; no invocation replayed", current["key"])
                return False
            owner.client.complete_pool_startup(
                task["id"], current["key"], native_id=native, worktree=worktree, machine=machine,
                reserved_by=owner.supervisor_id, outcome_kind=kind, detail=detail, **arguments,
                **driver,
            )
            return True
        try:
            if ordinary:
                from .pool_startup_acp import reachable
                session = {"session_id": native, "worktree_id": worktree} if reachable(current, native) else None
            else:
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
                **driver,
            )
            invocation = pending["invocation"]
            kind, detail = deliver_retry(invocation, observe_only=False)
            owner.client.complete_pool_startup(
                task["id"], current["key"], native_id=native, worktree=worktree, machine=machine,
                reserved_by=owner.supervisor_id, outcome_kind=kind, detail=detail,
                generation=budget["generation"], startup_revision=invocation["startup_revision"],
                **driver,
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
        from .pool_startup_retry_contract import decline_guidance
        message += decline_guidance(task["id"], budget["generation"], native)
        key = (
            f"pool-startup:{current['key']}:{budget['generation']}:"
            f"{current['startup_revision']}:{slot['next_nudge_at']}"
        )
        if ordinary:
            from .pool_startup_acp import deliver
            from .pool_startup_retry_contract import ordinary_prompt
            message += "\n\n" + ordinary_prompt({
                "task_id": task["id"], "generation": budget["generation"],
                "native_id": native, "worktree": worktree,
            }, current)
            kind, _ = deliver({
                **driver, "native_id": native, "machine": machine,
                "idempotency_key": key, "prompt": message,
            }, observe_only=False)
            return kind == "startup_succeeded"
        delivered = bridge.send_nudge(
            worktree, message, machine=machine, expected_session_id=native,
            idempotency_key=key,
        )
        if not delivered:
            log.warning("pool startup nudge delivery is unknown for %s; slot is not acceptance", current["key"])
        return delivered
    except (DispatchError, TaskError, httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        log.warning("pool startup supervision unavailable or changed for %s; legacy recovery refused",
                    reservation.get("key"), exc_info=True)
        return False


def _close_decline(
    owner: Supervisor, reservation: dict, task: dict, snapshot: dict, *, observe_only: bool,
) -> bool:
    """Only the existing allocator may ask the coordinator to observe; never finalize here."""
    current, assigned, decline = snapshot["reservation"], snapshot["task"], snapshot["worker_decline"]
    if (
        observe_only or current.get("reserved_by") != owner.supervisor_id
        or current.get("key") != reservation.get("key")
        or current.get("startup_revision") != reservation.get("startup_revision")
        or current.get("driver") != reservation.get("driver")
        or assigned.get("generation") != task.get("generation")
        or assigned.get("status") != Status.CLAIMED or assigned.get("owner") is not None
        or assigned.get("hold_reason") is not None or assigned.get("awaiting_steer")
        or decline.get("task_id") != task["id"]
        or decline.get("generation") != assigned.get("generation")
        or decline.get("startup_revision") != current.get("startup_revision")
        or decline.get("reservation_key") != current.get("key")
        or decline.get("native_id") != snapshot.get("native_id")
    ):
        return False
    if action_inhibited("worker-admission", logger=log):
        return False
    governance = owner._recheck_governance("pre-mutation:pool-decline-closeout")
    if governance is not None and governance.get("status") != "ready":
        return False
    owner.client.close_pool_decline(task["id"], decline)
    # The worker settles its exact claim, then finalizes, after this confirmed requeue.
    log.info("worker decline closeout observed for %s; native retirement remains pending", current["key"])
    return True

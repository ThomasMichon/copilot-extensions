"""Idle unfinished headless workers: confirm done, do not auto-suspend."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from .client import DispatchError
from .queue import SpawnState, Status
from .spawn_factories import (
    _parse_fleet_body_handle,
    _parse_local_body_handle,
    _worktree_from_reservation,
)

log = logging.getLogger("agent-dispatch.supervisor")

IdleConfirmNudgeFn = Callable[[str, dict], bool]
IDLE_CONFIRM_COOLDOWN = 60.0


def idle_confirm_message(task: dict) -> str:
    """Work-bearing idle prompt: idle is not done; continue or complete for real.

    Composed from the task's own recorded objective (``goal``/``prompt``) and
    ``done_criteria`` rather than a fixed generic sentence, so the worker is
    re-oriented on *its actual assignment*, not a template that may not
    describe it. Never suggests a steering card: that is a separate,
    consciously-chosen resolution some task types may not have -- confirmed
    live that suggesting it universally can strand a task for hours behind an
    unanswered human-input gate.
    """
    tid = task.get("id") or "unknown"
    title = str(task.get("title") or "").strip()
    named = f"task {tid}" + (f" ({title})" if title else "")
    objective = str(task.get("goal") or task.get("prompt") or title).strip()
    objective_line = f"\nYour assignment: {objective}" if objective else ""
    resume_line = (
        "Idle does not mean done -- resume working toward the assignment "
        "above now."
        if objective
        else "Idle does not mean done -- resume the task now."
    )
    criteria = str(task.get("done_criteria") or "").strip()
    criteria_line = f"\nDone-criteria: {criteria}" if criteria else ""
    completion_clause = (
        "if the done-criteria are genuinely met"
        if criteria
        else "if the assignment is genuinely, fully complete"
    )
    return (
        f"You are idle, but {named} is not marked complete.{objective_line}"
        f"{criteria_line}\n"
        f"{resume_line} Only run `agent-dispatch complete "
        f"{tid}` {completion_clause}; do not complete just "
        "to clear this prompt. If the assignment is genuinely blocked, use "
        "whatever resolution your own task's instructions define for that "
        "case (e.g. `agent-dispatch suspend`) -- do not invent one."
    )


def default_idle_confirm_nudge(target: str, task: dict) -> bool:
    """Resume the idle session with a real prompt (not a notify-kind nudge)."""
    from . import bridge

    host = task.get("_idle_host")
    host_s = host.strip() if isinstance(host, str) and host.strip() else None
    return bridge.resume_session(
        target, idle_confirm_message(task), host=host_s, wait=False
    )


def nudge_idle_headless_tasks(supervisor: Any, *, now: float) -> int:
    """Idle is not done. Nudge STARTED headless bodies once per cooldown.

    Skips any task carrying a label in ``supervisor.idle_nudge_exempt_labels``
    -- that task type owns its own resume path (an in-process evaluator, or an
    external one driven entirely through this CLI) and going idle with no new
    activity is its correct resting state, not an unfinished turn. See
    :attr:`Supervisor.idle_nudge_exempt_labels` for the full rationale.
    """
    nudged = 0
    exempt = getattr(supervisor, "idle_nudge_exempt_labels", None) or ()
    for res in supervisor._pool_reservations(state=SpawnState.SPAWNED):
        try:
            task = supervisor.client.get(res["task_id"])
        except DispatchError:
            continue
        if (
            not supervisor._matches_pool(task)
            or task.get("status") != Status.STARTED
            or not task.get("owner")
        ):
            continue
        if exempt and set(task.get("labels") or ()).intersection(exempt):
            continue
        activity = None
        fleet = _parse_fleet_body_handle(res.get("session_handle"))
        local_sid = _parse_local_body_handle(res.get("session_handle"))
        try:
            if fleet is not None:
                activity = supervisor.fleet_activity_fn(*fleet)
            elif local_sid is not None:
                activity = supervisor.local_body_activity_fn(local_sid)
        except Exception:
            log.exception(
                "failed to read headless turn state for task %s",
                task.get("id"),
            )
            continue
        if activity != "IDLE":
            continue
        tid = str(task["id"])
        last = supervisor._last_idle_nudge.get(tid, 0.0)
        if (now - last) < IDLE_CONFIRM_COOLDOWN:
            continue
        owner = task.get("owner")
        target = (
            str(task.get("owner_session_id") or "").strip()
            or _worktree_from_reservation(res, owner)
            or local_sid
            or (fleet[1] if fleet is not None else None)
        )
        if not target:
            continue
        payload = dict(task)
        if fleet is not None:
            payload["_idle_host"] = fleet[0]
        try:
            if not supervisor.idle_nudge_fn(target, payload):
                continue
            supervisor._last_idle_nudge[tid] = now
            try:
                supervisor.client.set_activity(tid, "IDLE", reservation_key=res["key"])
            except DispatchError:
                log.exception("failed to record idle activity for task %s", tid)
            nudged += 1
            log.info("nudged idle unfinished headless worker for task %s", tid)
        except Exception:
            log.exception("idle-confirm nudge failed for task %s", tid)
    return nudged

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
    """Work-bearing idle prompt: idle is not done; continue or complete for real."""
    tid = task.get("id") or "unknown"
    title = str(task.get("title") or "").strip()
    named = f"task {tid}" + (f" ({title})" if title else "")
    criteria = str(task.get("done_criteria") or task.get("goal") or "").strip()
    criteria_line = f"\nDone-criteria: {criteria}" if criteria else ""
    return (
        f"I see you are idle, but you have not completed {named}. "
        "Idle after loading a skill or ending a turn is not completion. "
        "Continue the original goal now; do not complete just to clear this prompt."
        f"{criteria_line}\n"
        f"Only if those criteria are actually met, run `agent-dispatch complete {tid}`. "
        "If you cannot proceed, post a steering card with --request-input."
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
    """Idle is not done. Nudge STARTED headless bodies once per cooldown."""
    nudged = 0
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

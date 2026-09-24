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


def default_idle_confirm_nudge(target: str, task: dict) -> bool:
    """Ask an idle headless worker to confirm the task is actually done."""
    from . import bridge

    tid = task.get("id") or "unknown"
    title = str(task.get("title") or "").strip()
    named = f"task {tid}" + (f" ({title})" if title else "")
    message = (
        f"I see you are idle, but have not reported status. Please confirm that "
        f"you are, in fact, done with {named}. If you are not done, continue "
        "driving (load any needed skills, then act) until you can complete the "
        "task or must steer. If you are done, complete it."
    )
    host = task.get("_idle_host")
    if isinstance(host, str) and host.strip():
        return bridge.resume_session(target, message, host=host.strip(), wait=False)
    return bridge.send_nudge(target, message)


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

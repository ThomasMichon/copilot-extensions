"""Bounded current task policy snapshots, not reservations or launch grants."""

from __future__ import annotations

import json
import math
import re
from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from .queue import TaskQueue

_FIELDS = {
    "repo", "owner", "owner_session_id", "generation", "awaiting_steer",
    "resume_requested", "hold_reason", "hold_actor", "hold_at", "pending_steer",
}


def read_snapshot(queue: TaskQueue, task_id: str) -> dict | None:
    """Read task policy and waiter presence under one SQLite read snapshot."""
    conn = queue._connect()
    try:
        conn.execute("BEGIN")
        row = conn.execute(
            "SELECT id, repo, status, owner, owner_session_id, generation, awaiting_steer,"
            " resume_requested, hold_reason, hold_actor, hold_at FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if row is None:
            return None
        task = dict(row)
        for field in ("awaiting_steer", "resume_requested"):
            if type(task[field]) is int and task[field] in (0, 1):
                task[field] = bool(task[field])
        waiter = conn.execute(
            "SELECT 1 FROM run_waiters WHERE task_id = ? AND state IN ('active', 'preparing') LIMIT 1",
            (task_id,),
        ).fetchone()
        task["run_waiter"] = {} if waiter is not None else None
        task["pending_steer"] = conn.execute(
            "SELECT 1 FROM task_steer WHERE task_id = ? AND taken IS NOT 1 LIMIT 1",
            (task_id,),
        ).fetchone() is not None
        return snapshot(task, task_id=task_id)
    finally:
        conn.rollback()
        conn.close()


def _text(value: object) -> bool:
    return (
        isinstance(value, str) and 0 < len(value) <= 4096
        and not any(ord(char) < 32 for char in value)
    )


def snapshot(task: object, *, task_id: str) -> dict:
    """Report only reduced policy facts; unknown/missing fields refuse."""
    result = {
        "schema": "native-restart-task-policy", "version": 1, "task_id": task_id,
        "task_qualified": False, "launch_authorized": False, "automatic_eligible": False,
    }
    if not isinstance(task, dict) or not _FIELDS.union({"id", "status", "run_waiter"}) <= task.keys():
        return {**result, "reason": "incomplete-task-policy"}
    reduced = {key: task[key] for key in _FIELDS}
    reduced.update(state=task["status"], run_waiter=task["run_waiter"] is not None)
    # Do not echo opaque or unbounded coordinator data into a recovery callback.
    if any(
        value is not None and not _text(value)
        for key, value in reduced.items()
        if key in {"repo", "owner", "owner_session_id", "hold_reason", "hold_actor", "state"}
    ):
        return {**result, "reason": "malformed-task-policy"}
    if (
        type(reduced["generation"]) is not int or not 1 <= reduced["generation"] <= 2**63 - 1
        or type(reduced["awaiting_steer"]) is not bool
        or type(reduced["resume_requested"]) is not bool
        or type(reduced["pending_steer"]) is not bool
        or (
            reduced["hold_at"] is not None
            and (
                type(reduced["hold_at"]) not in {int, float}
                or not -2**63 < reduced["hold_at"] < 2**63
                or not math.isfinite(reduced["hold_at"])
            )
        )
    ):
        return {**result, "reason": "malformed-task-policy"}
    if len(json.dumps(reduced, ensure_ascii=True).encode("utf-8")) > 8192:
        return {**result, "reason": "oversized-task-policy"}
    native = reduced["owner_session_id"]
    try:
        canonical_native = isinstance(native, str) and str(UUID(native)) == native
    except ValueError:
        canonical_native = False
    owner = reduced["owner"]
    qualified = (
        task["id"] == task_id and reduced["state"] == "started" and _text(reduced["repo"])
        and isinstance(owner, str)
        and re.fullmatch(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+", owner) is not None
        and canonical_native and not reduced["awaiting_steer"] and not reduced["resume_requested"]
        and not reduced["pending_steer"]
        and all(reduced[key] is None for key in ("hold_reason", "hold_actor", "hold_at"))
        and not reduced["run_waiter"]
    )
    return {
        **result, **reduced, "task_qualified": bool(qualified),
        "reason": "current-task-matches" if qualified else "current-task-refuses",
    }

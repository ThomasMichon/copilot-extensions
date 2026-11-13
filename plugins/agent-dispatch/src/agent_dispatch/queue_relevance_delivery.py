"""Original-owner closeout on the existing steer inbox and wake outbox."""

from __future__ import annotations

import sqlite3
from dataclasses import replace

from .queue_common import Task, WakeOperation
from .queue_records import Status


CLOSEOUT_STATUSES = frozenset({*Status.OWNED, Status.SUBMITTED})


def closeout_owner(task: Task) -> str | None:
    return task.owner or (task.completed_by if task.status == Status.SUBMITTED else None)


def closeout_owner_matches(task: Task, closeout: sqlite3.Row) -> bool:
    if task.generation != closeout["generation"]:
        return False
    if task.status in {Status.PROPOSED, Status.QUEUED}:
        return bool(
            task.owner is None and task.owner_session_id is None
            and closeout["owner"] and closeout["owner_session_id"]
        )
    return bool(
        task.status in CLOSEOUT_STATUSES and closeout_owner(task)
        and (task.generation, closeout_owner(task), task.owner_session_id) == (
            closeout["generation"], closeout["owner"], closeout["owner_session_id"],
        )
    )


def closeout_wake(conn: sqlite3.Connection, wake: WakeOperation) -> bool:
    """Only the outbox row linked to this closeout may bypass the goal fence."""
    if not isinstance(wake, WakeOperation) or wake.pool_recovery_revision is not None:
        return False
    row = conn.execute(
        "SELECT c.generation, c.owner, c.owner_session_id FROM task_relevance_closeouts c"
        " JOIN task_steer s ON s.id=c.steer_id AND s.task_id=c.task_id"
        " JOIN wake_outbox w ON w.id=s.wake_operation_id AND w.task_id=c.task_id"
        " WHERE c.task_id=? AND w.id=?"
        " AND w.generation=c.generation AND w.owner=c.owner"
        " AND w.owner_session_id=c.owner_session_id",
        (wake.task_id, wake.id),
    ).fetchone()
    task_row = conn.execute("SELECT * FROM tasks WHERE id=?", (wake.task_id,)).fetchone()
    return bool(row and task_row and closeout_owner_matches(Task._from_row(task_row), row) and (
        wake.generation, wake.owner, wake.owner_session_id,
    ) == (row["generation"], row["owner"], row["owner_session_id"]))


def enqueue_closeout(owner, conn: sqlite3.Connection, task: Task, closeout: sqlite3.Row,
                     *, ts: float) -> WakeOperation | None:
    if not closeout_owner_matches(task, closeout) or not task.owner_session_id:
        return None
    steer = conn.execute(
        "SELECT wake_operation_id FROM task_steer WHERE id=?", (closeout["steer_id"],),
    ).fetchone()
    if steer["wake_operation_id"] is not None:
        return None
    prompt = (
        f"Task {task.id} no longer needs its original goal. Remain in this original "
        f"conversation at generation {task.generation}; do not start or resume the goal. "
        f"Read `agent-dispatch show {task.id}` and take the closeout instruction with "
        f"`agent-dispatch steer take {task.id} --all`. Preserve existing work, open PRs, "
        "holds and foreign obligations. Close out through the existing task, session "
        "and managed-worktree owners, including finalization when eligible. "
        "A wake receipt or terminal task status is not proof of actual cleanup. "
        "If ownership or generation changed, stop and report the mismatch."
    )
    wake = owner._enqueue_wake(
        conn, replace(task, owner=closeout["owner"]), message=prompt, ts=ts,
    )
    conn.execute(
        "UPDATE task_steer SET wake_operation_id=? WHERE id=?", (wake.id, closeout["steer_id"]),
    )
    return wake

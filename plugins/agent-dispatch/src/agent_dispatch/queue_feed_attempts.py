"""Original-basis admission before a repository subscription can be transmitted."""

from __future__ import annotations

import sqlite3
from typing import Any

from .queue_common import Task
from .queue_feed_preparation import (
    FeedWaiterAdmission, require_feed_preparation, require_feed_waiter_reconciliation,
)
from .queue_records import TaskError

ATTEMPT_PROTOCOL = "agent-dispatch.repository-feed-attempt/v1"


def require_feed_custody(
    conn: sqlite3.Connection, task: Task | None, waiter: dict[str, Any],
    *, now: float,
) -> None:
    """Recheck the original basis under the native waiter owner's write lock."""
    basis = FeedWaiterAdmission.from_record(waiter.get("feed_admission"))
    if waiter["state"] != "preparing" or waiter["replay_settled"]:
        raise TaskError("repository-feed registration requires its original preparing waiter")
    require_feed_preparation(conn, task, basis, worker_id=waiter["owner"])
    require_feed_waiter_reconciliation(task, waiter)
    latest = conn.execute(
        "SELECT MAX(generation) FROM run_waiters WHERE task_id=?", (waiter["task_id"],),
    ).fetchone()[0]
    if latest != waiter["generation"] or conn.execute(
        "SELECT 1 FROM run_waiter_wakes WHERE task_id=? AND waiter_generation=?"
        " AND task_generation=? AND owner=? AND owner_session_id=? LIMIT 1",
        (waiter["task_id"], waiter["generation"], waiter["task_generation"],
         waiter["owner"], waiter["owner_session_id"]),
    ).fetchone():
        raise TaskError("repository-feed waiter already has a native wake or successor")
    if task is not None and conn.execute(
        "SELECT 1 FROM wake_outbox WHERE task_id=? AND id=? AND owner=?"
        " AND owner_session_id=? AND generation=? AND pool_recovery_revision IS NULL"
        " AND (status IN ('pending', 'delivering')"
        " OR execution_send_state IN ('pending', 'unknown')) LIMIT 1",
        (task.id, task.wake_operation_id, task.owner, task.owner_session_id, task.generation),
    ).fetchone():
        raise TaskError("repository-feed attempt cannot override a current owner wake")
    if now >= waiter["delegation"]["feed"]["deadline_at"]:
        raise TaskError("original repository-feed deadline elapsed")


def require_feed_attempt(
    conn: sqlite3.Connection, task: Task | None, waiter: dict[str, Any],
    observed: dict[str, Any], *, now: float,
) -> None:
    """Record possible transmission only once; later uncertainty needs inspection."""
    require_feed_custody(conn, task, waiter, now=now)
    if observed.get("feed_admission") != waiter["feed_admission"]:
        raise TaskError("repository-feed attempt admission basis changed")
    if waiter["replay_attempted"] or waiter["replay_registration_id"] is not None:
        raise TaskError("repository-feed registration already attempted; reconcile original request")


def require_feed_arm(
    conn: sqlite3.Connection, task: Task | None, waiter: dict[str, Any],
    delegation: dict[str, Any] | None, *, now: float,
) -> None:
    if waiter["delegation"] is None or "feed" not in waiter["delegation"]:
        return
    require_feed_custody(conn, task, waiter, now=now)
    if (
        not waiter["replay_attempted"] or delegation is None
        or waiter["replay_registration_id"] != delegation["registration_id"]
    ):
        raise TaskError("repository-feed arming requires its retained attempt and receipt")

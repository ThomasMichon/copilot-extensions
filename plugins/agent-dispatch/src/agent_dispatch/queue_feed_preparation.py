"""Finite feed admission under the existing waiter preparation write lock."""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3
from typing import Any

from .queue_common import Task
from .queue_dedup import find_live_dedup_task
from .queue_execution_handoff import require_execution_admission
from .queue_records import Status, TaskError
from .queue_task_relationships import require_relationship

PREPARATION_PROTOCOL = "agent-dispatch.repository-feed-preparation/v1"


@dataclass(frozen=True)
class FeedWaiterAdmission:
    """Detached task basis observed before asynchronous service negotiation."""

    task_id: str
    lane: str
    dedup_key: str
    worker_id: str
    owner_session_id: str
    task_generation: int
    steering_revision: str

    @classmethod
    def observe(cls, task: Task, *, lane: str, dedup_key: str) -> FeedWaiterAdmission:
        if not task.owner or not task.owner_session_id:
            raise TaskError("repository-feed preparation requires the retained task owner")
        return cls(
            task.id, lane, dedup_key, task.owner, task.owner_session_id,
            task.generation, task.steering_revision,
        )


def require_feed_preparation(
    conn: sqlite3.Connection, task: Task | None, expected: FeedWaiterAdmission,
    *, worker_id: str,
) -> None:
    """Recheck association, policy and current custody before any waiter change."""
    if not isinstance(expected, FeedWaiterAdmission) or task is None:
        raise TaskError("repository-feed preparation requires a current task basis")
    if (
        task.id != expected.task_id or task.repo != expected.lane
        or not expected.lane or task.dedup_key != expected.dedup_key
        or not expected.dedup_key
        or task.source != "recipe" or task.origin_ref != "reviewer"
        or task.kind != "finite"
    ):
        raise TaskError("repository-feed preparation requires the associated finite reviewer task")
    if (
        task.owner != expected.worker_id or worker_id != expected.worker_id
        or task.owner_session_id != expected.owner_session_id
        or task.generation != expected.task_generation
        or task.steering_revision != expected.steering_revision
        or task.status not in (Status.STARTED, Status.SUSPENDED)
    ):
        raise TaskError("repository-feed task changed during preparation")
    if task.hold_reason is not None or task.awaiting_steer:
        raise TaskError("repository-feed preparation cannot override a hold or operator form")
    if task.status == Status.STARTED and conn.execute(
        "SELECT 1 FROM wake_outbox WHERE task_id=? AND id=? AND owner=?"
        " AND owner_session_id=? AND generation=? AND pool_recovery_revision IS NULL"
        " AND (status IN ('pending', 'delivering')"
        " OR execution_send_state IN ('pending', 'unknown')) LIMIT 1",
        (task.id, task.wake_operation_id, task.owner, task.owner_session_id, task.generation),
    ).fetchone():
        raise TaskError("repository-feed suspension would invalidate a current owner wake")
    associated = find_live_dedup_task(conn, expected.lane, expected.dedup_key)
    if associated is None or associated.id != task.id:
        raise TaskError("repository-feed task association changed during preparation")
    require_execution_admission(conn, task.id)
    require_relationship(conn, task.id)


def require_feed_waiter_reconciliation(
    task: Task | None, waiter: dict[str, Any] | None,
) -> None:
    """A suspended owner may observe its live waiter, never replace its wake."""
    if task is not None and task.status == Status.STARTED and waiter is None:
        return
    if (
        task is None or task.status != Status.SUSPENDED or waiter is None
        or waiter["state"] not in ("preparing", "active")
        or waiter["owner"] != task.owner
        or waiter["owner_session_id"] != task.owner_session_id
        or waiter["task_generation"] != task.generation
    ):
        raise TaskError("suspended repository-feed task requires the same live waiter")

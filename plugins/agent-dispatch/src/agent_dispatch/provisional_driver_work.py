"""Read-only Dispatch custody checks for a captured driver's provisional observation."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

from .assignment_workspace import same_assignment_worker
from .queue_common import RunWaiterWakeOperation, Task, WakeOperation
from .queue_execution_custody import require_execution_send_reconciliation
from .queue_execution_wakes import TABLES, _known_native_retry, _wake_targets_reservation
from .queue_records import TaskError
from .queue_relevance_delivery import closeout_wake
from .queue_run_waiter_transition_cleanup import CLAIM_RELEASE_SENDER

if TYPE_CHECKING:
    from .queue import TaskQueue

_TREE = "substr(owner,instr(owner,'/')+1)"
_TARGET = f"COALESCE(NULLIF(owner_session_id,''),{_TREE})"
_LIVE_WAITER = "state IN ('preparing','active')"
_HELD_ASSIGNMENT = "status IN ('claimed','started')"
# A terminal first delivery can settle an unqualified send, but a later
# successful retry says nothing about the outcome of an earlier attempt.
_AMBIGUOUS = (
    "(execution_send_state IN ('pending','unknown') OR"
    " (status='delivering' AND COALESCE(execution_send_state,'') NOT IN ('sent','not_sent')) OR"
    " (attempts>0 AND COALESCE(execution_send_state,'') NOT IN ('sent','not_sent')"
    " AND NOT (execution_send_state IS NULL AND execution_send IS NULL AND attempts=1"
    " AND status='delivered' AND delivered_at IS NOT NULL AND delivery_token IS NULL"
    " AND delivery_expires_at IS NULL AND last_error IS NULL)))"
)
_CANDIDATE = (
    f"({_AMBIGUOUS} OR (status IN ('pending','delivering')"
    " AND execution_send_state IS NOT 'sent'))"
)


def migrate(conn: sqlite3.Connection) -> None:
    for table in TABLES:
        for suffix, key in (("task", "task_id"), ("target", _TARGET)):
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{table}_driver_{suffix}_v2"
                f" ON {table}({key}) WHERE {_CANDIDATE}"
            )
            conn.execute(f"DROP INDEX IF EXISTS idx_{table}_driver_{suffix}")
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_run_waiters_driver_work"
        f" ON run_waiters(task_id) WHERE {_LIVE_WAITER}"
    )
    for table, predicate in (("tasks", _HELD_ASSIGNMENT), ("run_waiters", _LIVE_WAITER)):
        for suffix, key in (("native", "owner_session_id"), ("tree", _TREE)):
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{table}_driver_{suffix}"
                f" ON {table}({key}) WHERE {predicate}"
            )


def _pending_delivery(queue: TaskQueue, conn: sqlite3.Connection, row: sqlite3.Row,
                      table: str, captured_id: str) -> bool:
    current = queue._fetch(conn, row["task_id"])
    if current is None:
        return False
    wake: WakeOperation | RunWaiterWakeOperation
    if table == "wake_outbox":
        wake = WakeOperation._from_row(row)
        if not queue._wake_is_current(current, wake, conn=conn):
            return False
        if wake.task_id == captured_id and closeout_wake(conn, wake):
            return False
    else:
        wake = RunWaiterWakeOperation._from_row(row)
        if not queue._run_waiter_wake_is_current(conn, current, wake):
            return False
    return (
        (row["execution_send"] is None and row["execution_send_state"] is None)
        or _known_native_retry(row, wake, current)
    )


def require_no_outstanding_driver_work(
    queue: TaskQueue, conn: sqlite3.Connection, task: Task,
    reservation: dict[str, Any], native: str,
) -> None:
    require_execution_send_reconciliation(conn, task.id, exclusive_key=task.exclusive_key)
    tree = reservation["worktree"]
    worker = f"{reservation['execution_machine']}/{tree}"
    # A waiter may still hold the driver after the task's active lease ended.
    for row in conn.execute(
        "SELECT id AS task_id,COALESCE(owner,'') AS owner,owner_session_id FROM tasks"  # noqa: S608 -- fixed SQL predicates
        f" WHERE {_HELD_ASSIGNMENT} AND id<>:task AND (owner_session_id=:native OR {_TREE}=:tree)"
        " UNION ALL SELECT task_id,owner,owner_session_id FROM run_waiters"
        f" WHERE {_LIVE_WAITER} AND (task_id=:task OR owner_session_id=:native OR {_TREE}=:tree)",
        {"task": task.id, "native": native, "tree": tree},
    ):
        if (
            row["task_id"] == task.id or same_assignment_worker(row["owner"], worker)
            or (row["owner_session_id"] == native and _wake_targets_reservation(
                row, reservation, native,
            ))
        ):
            raise TaskError("provisional observation has an outstanding assignment or waiter")
    for table in TABLES:
        bookkeeping = (
            " AND (sender<>? OR execution_send_state IN ('pending','unknown'))"
            if table == "run_waiter_wakes" else ""
        )
        params = (task.id, native, tree)
        for row in conn.execute(
            f"SELECT *, {_AMBIGUOUS} AS uncertain FROM {table} WHERE {_CANDIDATE}{bookkeeping}"  # noqa: S608 -- fixed SQL and outbox names
            f" AND (task_id=? OR {_TARGET} IN (?,?))",
            (CLAIM_RELEASE_SENDER, *params) if bookkeeping else params,
        ):
            if row["task_id"] != task.id and not _wake_targets_reservation(
                row, reservation, native,
            ):
                continue
            if row["uncertain"] or _pending_delivery(queue, conn, row, table, task.id):
                raise TaskError("provisional observation has unresolved driver wake custody")

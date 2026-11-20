"""Read-only Dispatch custody checks for a captured driver's provisional observation."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

from .queue_common import RunWaiterWakeOperation, Task, WakeOperation
from .queue_execution_custody import require_execution_send_reconciliation
from .queue_execution_wakes import TABLES, _retryable_native_refusal, _wake_targets_reservation
from .queue_records import TaskError
from .queue_relevance_delivery import closeout_wake
from .queue_run_waiter_transition_cleanup import CLAIM_RELEASE_SENDER

if TYPE_CHECKING:
    from .queue import TaskQueue

_TREE = "substr(owner,instr(owner,'/')+1)"
_TARGET = f"COALESCE(NULLIF(owner_session_id,''),{_TREE})"
_LIVE_WAITER = "state IN ('preparing','active')"
_RETAINED_ASSIGNMENT = (
    "(status IN ('claimed','started','suspended') OR"
    " (status='submitted' AND completed_by IS NOT NULL AND owner_session_id IS NOT NULL))"
)
_ASSIGNED_WORKER = "CASE WHEN status='submitted' THEN completed_by ELSE owner END"
_ASSIGNED_TREE = f"substr({_ASSIGNED_WORKER},instr({_ASSIGNED_WORKER},'/')+1)"


def _host_sql(owner: str) -> str:
    # Bridge routes a bare/partially qualified owner by native ID, not to that hostname.
    slash = f"instr({owner},'/')"
    return (f"CASE WHEN {slash}>0 AND length({owner})>{slash} THEN"
            f" lower(trim(substr({owner},1,{slash}-1))) ELSE '' END")


_ASSIGNED_HOST = _host_sql(_ASSIGNED_WORKER)
_WAITER_HOST = _host_sql("owner")
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
    for table, predicate, host, tree, previous in (
        ("tasks", _RETAINED_ASSIGNMENT, _ASSIGNED_HOST, _ASSIGNED_TREE, "_v2"),
        ("run_waiters", _LIVE_WAITER, _WAITER_HOST, _TREE, ""),
    ):
        for suffix, key in (("native", "owner_session_id"), ("tree", tree)):
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{table}_driver_{suffix}_host"
                f" ON {table}({key},{host}) WHERE {predicate}"
            )
            conn.execute(f"DROP INDEX IF EXISTS idx_{table}_driver_{suffix}{previous}")
            if previous:
                conn.execute(f"DROP INDEX IF EXISTS idx_{table}_driver_{suffix}")


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
        or _retryable_native_refusal(row, wake, current, observing_delivery=True)
    )


def require_no_outstanding_driver_work(
    queue: TaskQueue, conn: sqlite3.Connection, task: Task,
    reservation: dict[str, Any], native: str,
) -> None:
    require_execution_send_reconciliation(conn, task.id, exclusive_key=task.exclusive_key)
    tree = reservation["worktree"]
    host = reservation["execution_machine"].strip().lower()
    params = {"task": task.id, "native": native, "tree": tree, "host": host}
    if conn.execute(
        "SELECT 1 FROM spawn_reservations s CROSS JOIN task_pool_assignments a"
        " ON a.reservation_key=s.key AND a.task_id=s.task_id JOIN tasks t ON t.id=a.task_id"
        " WHERE s.worktree=:tree AND a.task_id<>:task AND a.assignment_released=0"
        " AND lower(trim(a.machine))=:host AND t.generation=a.task_generation"
        " AND t.status='claimed' AND t.owner IS NULL LIMIT 1", params,
    ).fetchone():
        raise TaskError("provisional observation has an outstanding pool placement")
    for table, predicate, owner_host, owner_tree in (
        ("tasks", _RETAINED_ASSIGNMENT + " AND id<>:task", _ASSIGNED_HOST, _ASSIGNED_TREE),
        ("run_waiters", _LIVE_WAITER, _WAITER_HOST, _TREE),
    ):
        probes = [
            f"owner_session_id=:native AND {owner_host} IN (:host,'')",
            f"{owner_tree}=:tree AND {owner_host}=:host",
        ]
        if table == "run_waiters":
            probes.append("task_id=:task")
        for probe in probes:
            if conn.execute(
                f"SELECT 1 FROM {table} WHERE {predicate} AND {probe} LIMIT 1", params,  # noqa: S608 -- fixed SQL; caller data is bound
            ).fetchone():
                raise TaskError("provisional observation has an outstanding assignment or waiter")
    for table in TABLES:
        bookkeeping = (
            " AND (sender<>? OR execution_send_state IN ('pending','unknown'))"
            if table == "run_waiter_wakes" else ""
        )
        wake_params = (task.id, native, tree)
        for row in conn.execute(
            f"SELECT *, {_AMBIGUOUS} AS uncertain FROM {table} WHERE {_CANDIDATE}{bookkeeping}"  # noqa: S608 -- fixed SQL and outbox names
            f" AND (task_id=? OR {_TARGET} IN (?,?))",
            (CLAIM_RELEASE_SENDER, *wake_params) if bookkeeping else wake_params,
        ):
            machine, slash, address = row["owner"].partition("/")
            ambiguous_native = (
                row["owner_session_id"] == native and not (slash and machine and address)
            )
            if (
                row["task_id"] != task.id and not ambiguous_native
                and not _wake_targets_reservation(row, reservation, native)
            ):
                continue
            if row["uncertain"] or _pending_delivery(queue, conn, row, table, task.id):
                raise TaskError("provisional observation has unresolved driver wake custody")

"""Read-only Dispatch custody checks for a captured driver's provisional observation."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

from .current_driver_lookup import DriverMatch, current_driver_match
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
# A terminal first delivery can settle an unqualified send, but a later
# successful retry says nothing about the outcome of an earlier attempt.
_AMBIGUOUS = (
    "(COALESCE(first_qualified_attempt,attempts)>1"
    " OR execution_send_state IN ('pending','unknown') OR"
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
        generation = "generation" if table == "wake_outbox" else "task_generation"
        # A receipt is for this outbox row and owner incarnation, not just a state label.
        original_identity = (
            "CASE WHEN json_valid(execution_send) THEN"
            f" json_extract(execution_send,'$.outbox')='{table}'"
            " AND json_extract(execution_send,'$.wake_id')=id"
            f" AND json_extract(execution_send,'$.task_generation')={generation}"
            " AND json_extract(execution_send,'$.owner_session_id') IS owner_session_id"
            " AND json_type(execution_send,'$.delivery_token')='text'"
            " AND length(json_extract(execution_send,'$.delivery_token'))>0 END"
        )
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if "first_qualified_attempt" not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN first_qualified_attempt INTEGER")
            # A qualified identity already retained on attempt one has no earlier attempt.
            # Historical multi-attempt rows cannot make that assertion from the latest receipt.
            conn.execute(
                f"UPDATE {table} SET first_qualified_attempt=1 WHERE attempts=1"  # noqa: S608 -- fixed outbox names
                " AND execution_send IS NOT NULL AND execution_send_state"
                f" IN ('pending','unknown','sent','not_sent') AND {original_identity}"
            )
        conn.execute(
            f"CREATE TRIGGER IF NOT EXISTS {table}_first_qualified_attempt"  # noqa: S608 -- fixed SQL and outbox names
            f" AFTER UPDATE OF execution_send ON {table}"
            " WHEN OLD.execution_send IS NULL AND NEW.execution_send IS NOT NULL"
            " AND NEW.execution_send_state='pending' AND NEW.first_qualified_attempt IS NULL"
            " AND NEW.status='delivering' AND NEW.attempts>0"
            f" BEGIN UPDATE {table} SET first_qualified_attempt=NEW.attempts"
            f" WHERE id=NEW.id AND {original_identity}"
            " AND json_extract(execution_send,'$.delivery_token')=delivery_token; END"
        )
        for suffix, key in (("task", "task_id"), ("target", _TARGET)):
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{table}_driver_{suffix}_v3"
                f" ON {table}({key}) WHERE {_CANDIDATE}"
            )
            conn.execute(f"DROP INDEX IF EXISTS idx_{table}_driver_{suffix}_v2")
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
    binding = current_driver_match(
        conn, task_id=task.id, machine=reservation["execution_machine"], worktree=tree,
        native_id=native,
    )
    if binding is not DriverMatch.CLEAR:
        raise TaskError(f"cannot exclude an outstanding assignment or waiter ({binding.value})")
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

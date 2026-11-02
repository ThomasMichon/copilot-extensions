"""Transaction-local capacity evidence shared by placement and supervision queries."""

from __future__ import annotations

import sqlite3
from typing import Any

from .ordinary_native_bindings import is_native_retired, native_bindings, reservation_native_identity
from .queue_common import Task
from .queue_execution_custody import ReservationTransportSnapshot
from .queue_records import SpawnState, TaskError


class ReservationAccountability:
    """Reuse evidence only within one unchanged database transaction snapshot."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.receipts = {
            row["reservation_key"]: row["native_session_id"]
            for row in conn.execute("SELECT reservation_key, native_session_id FROM acquisition_receipts")
        }
        self.transport = ReservationTransportSnapshot(conn)
        self.tasks: dict[str, Task] = {}
        self.retired: dict[tuple[Any, ...], frozenset[str]] = {}

    def accountable(self, row: sqlite3.Row) -> bool:
        if row["state"] in SpawnState.ACTIVE or row["native_release_pending"]:
            return True
        history = {**dict(row), "native_session_id": self.receipts.get(row["key"])}
        if self.transport.accountable(history):
            return True
        if not (row["startup_complete"] or row["startup_fenced"]):
            return True
        native = reservation_native_identity(self.conn, history)
        # A proven identity's own retirement already answers membership. Do not
        # repeatedly reclassify its entire allocation history to prove it again.
        if is_native_retired(row):
            return False
        if native is None:
            return bool(row["session_handle"])
        key = (
            row["worktree"], row["allocation_reservation_key"], row["task_id"],
            row["creation_key"], row["exclusive_key"],
            row["key"] if not row["worktree"] else None,
        )
        if key not in self.retired:
            task = self.tasks.get(row["task_id"])
            if task is None:
                task_row = self.conn.execute(
                    "SELECT * FROM tasks WHERE id = ?", (row["task_id"],),
                ).fetchone()
                if task_row is None:
                    raise TaskError("pool capacity has an orphaned reservation; reconcile its execution custody")
                task = self.tasks[row["task_id"]] = Task._from_row(task_row)
            _, retired = native_bindings(self.conn, history, task, require_owned_workspace=False)
            self.retired[key] = frozenset(retired)
        return native not in self.retired[key]


def reservation_accountable(conn: sqlite3.Connection, row: sqlite3.Row) -> bool:
    """Classify a single reservation without retaining evidence across mutations."""
    return ReservationAccountability(conn).accountable(row)

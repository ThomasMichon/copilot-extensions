"""Durable task intent, independent of routing, ownership and lifecycle."""

from __future__ import annotations

import sqlite3
from typing import Literal

from .queue_records import TaskError

ExecutionMode = Literal["legacy", "coordination", "delegated"]
EXECUTION_MODES = ("legacy", "coordination", "delegated")


def execution_mode(value: object) -> ExecutionMode:
    if value not in EXECUTION_MODES:
        raise TaskError("execution_mode must be legacy, coordination or delegated")
    return value


def permits_allocation(task: dict) -> bool:
    return task.get("execution_mode", "legacy") in ("legacy", "delegated")


def migrate_execution_intent(conn: sqlite3.Connection) -> None:
    # These row-scoped fences survive rollback to a provider that omits the
    # new field. Manual ownership/lifecycle writes remain available there.
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS task_execution_intent_retained"
        " BEFORE UPDATE OF execution_mode ON tasks"
        " WHEN NEW.execution_mode IS NOT OLD.execution_mode"
        " BEGIN SELECT RAISE(ABORT, 'task execution intent is immutable'); END"
    )
    for operation in ("INSERT", "UPDATE OF task_id"):
        suffix = "insert" if operation == "INSERT" else "retarget"
        conn.execute(
            f"CREATE TRIGGER IF NOT EXISTS coordination_reservation_{suffix}"
            f" BEFORE {operation} ON spawn_reservations"
            " WHEN EXISTS (SELECT 1 FROM tasks WHERE id=NEW.task_id"
            " AND execution_mode='coordination')"
            " BEGIN SELECT RAISE(ABORT, 'coordination task cannot allocate a worker'); END"
        )

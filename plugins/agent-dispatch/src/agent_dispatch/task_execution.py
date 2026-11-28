"""Durable task intent, independent of routing, ownership and lifecycle."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal, cast

from .queue_records import TaskError

if TYPE_CHECKING:
    from .queue_common import Task

ExecutionMode = Literal["legacy", "coordination", "delegated"]
EXECUTION_MODES = ("legacy", "coordination", "delegated")


def execution_mode(value: object) -> ExecutionMode:
    if value not in EXECUTION_MODES:
        raise TaskError("execution_mode must be legacy, coordination or delegated")
    return cast(ExecutionMode, value)


def permits_allocation(task: Mapping[str, object]) -> bool:
    return task.get("execution_mode", "legacy") in ("legacy", "delegated")


def require_spawn_intent(conn: sqlite3.Connection, task: Task) -> None:
    from .queue_standing_schema import require_finite_execution_admission

    require_finite_execution_admission(conn, task)
    if task.execution_mode == "coordination":
        raise TaskError("coordination task cannot allocate a worker")


def require_matching_execution_intent(task: Task | None, mode: str) -> None:
    if task is not None and mode != "legacy" and task.execution_mode != mode:
        raise TaskError("dedup_key belongs to a task with different execution intent")


def creation_contract_fields(
    *, category: str, execution_mode: str, standing_parent_id: str | None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if category != "work":
        fields["category"] = category
    if execution_mode != "legacy":
        fields["execution_mode"] = execution_mode
    if standing_parent_id is not None:
        fields["standing_parent_id"] = standing_parent_id
    return fields


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
        conn.execute(
            f"CREATE TRIGGER IF NOT EXISTS coordination_handoff_{suffix}"
            f" BEFORE {operation} ON handoff_fallback_attempts"
            " WHEN EXISTS (SELECT 1 FROM tasks WHERE id=NEW.task_id"
            " AND execution_mode='coordination')"
            " BEGIN SELECT RAISE(ABORT, 'coordination task cannot allocate a worker'); END"
        )

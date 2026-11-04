"""Retain assignment requirements in the existing audit and reservation owners."""

from __future__ import annotations

import sqlite3
from typing import Literal

from .task_obligations import ASSIGNMENT_PREDICATE


def bound_relationship_condition(task: Literal["t", "NEW"]) -> str:
    """Keep live checks and persisted rollback guards on the same assignment."""
    return (
        "EXISTS (SELECT 1 FROM task_events e"  # noqa: S608 -- fixed SQL aliases
        f" WHERE e.task_id={task}.id AND e.task_relationship IS NOT NULL"
        " AND json_extract(e.task_relationship, '$.state')='bound'"
        " AND COALESCE(json_extract(e.task_relationship, '$.continuation.generation'),"
        f" json_extract(e.task_relationship, '$.request.generation'))={task}.generation"
        " AND (json_type(e.task_relationship, '$.continuation.owner_session_id') IS NULL"
        " OR json_extract(e.task_relationship, '$.continuation.owner_session_id')"
        f" IS {task}.owner_session_id)"
        f" AND ({task}.owner IS NULL OR ("
        f" lower(trim(substr({task}.owner, 1, instr({task}.owner, '/')-1)))"
        "=lower(trim(substr(e.worker, 1, instr(e.worker, '/')-1)))"
        f" AND substr({task}.owner, instr({task}.owner, '/')+1)"
        "=substr(e.worker, instr(e.worker, '/')+1)))"
        " AND ((COALESCE(json_extract(e.task_relationship, '$.continuation.reservation_key'),"
        " json_extract(e.task_relationship, '$.request.reservation_key')) IS NULL"
        f" AND NOT EXISTS (SELECT 1 FROM task_pool_assignments a WHERE a.task_id={task}.id"
        " AND a.assignment_released=0)) OR EXISTS ("
        " SELECT 1 FROM task_pool_assignments a JOIN spawn_reservations s"
        " ON s.key=a.reservation_key"
        f" WHERE a.task_id={task}.id"
        f" AND a.task_generation={task}.generation"
        " AND s.task_id=e.task_id AND s.task_relationship_required=1"
        " AND s.task_relationship_event_id=e.id"
        f" AND s.task_relationship_generation={task}.generation"
        " AND COALESCE(json_extract(e.task_relationship, '$.continuation.reservation_key'),"
        " json_extract(e.task_relationship, '$.request.reservation_key'))=s.key"
        " AND json_extract(e.task_relationship, '$.request.workspace.worktree')=s.worktree"
        " AND json_extract(e.task_relationship, '$.request.workspace.creation_nonce')"
        "=s.creation_nonce"
        " AND json_extract(e.task_relationship, '$.request.workspace.project')"
        "=s.allocation_project"
        " AND json_extract(e.task_relationship, '$.request.workspace.path')=s.worktree_path"
        " AND lower(trim(json_extract(e.task_relationship, '$.request.workspace.machine')))"
        "=lower(trim(s.execution_machine)))))"
    )


def migrate_relationships(conn: sqlite3.Connection) -> None:
    for table, columns in (
        ("task_events", {"task_relationship": "TEXT"}),
        (
            "spawn_reservations",
            {
                "creation_nonce": "TEXT",
                "task_relationship_event_id": "INTEGER",
                "task_relationship_required": "INTEGER NOT NULL DEFAULT 0",
                "task_relationship_generation": "INTEGER",
            },
        ),
    ):
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, declaration in columns.items():
            if name in existing:
                continue
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_task_relationships ON task_events(task_id, id)"
        " WHERE task_relationship IS NOT NULL"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_task_events_worker_assignment ON task_events(task_id, id)"
        " WHERE " + ASSIGNMENT_PREDICATE
    )
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS task_relationship_identity_retained"
        " BEFORE UPDATE OF task_relationship ON task_events"
        " WHEN OLD.task_relationship IS NOT NULL AND"
        " (NEW.task_relationship IS NULL OR NOT json_valid(NEW.task_relationship)"
        " OR json_extract(OLD.task_relationship, '$.request')"
        " IS NOT json_extract(NEW.task_relationship, '$.request'))"
        " BEGIN SELECT RAISE(ABORT, 'task relationship request is immutable'); END"
    )
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS task_relationship_event_retained"
        " BEFORE DELETE ON task_events WHEN OLD.task_relationship IS NOT NULL"
        " BEGIN SELECT RAISE(ABORT, 'task relationship event is retained'); END"
    )
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS task_relationship_workspace_retained"
        " BEFORE UPDATE OF task_relationship_required, creation_nonce, task_relationship_event_id,"
        " task_relationship_generation ON spawn_reservations"
        " WHEN (OLD.task_relationship_required=1 AND NEW.task_relationship_required IS NOT 1)"
        " OR (OLD.creation_nonce IS NOT NULL AND NEW.creation_nonce IS NOT OLD.creation_nonce)"
        " OR (OLD.task_relationship_event_id IS NOT NULL"
        " AND NEW.task_relationship_event_id IS NOT OLD.task_relationship_event_id)"
        " OR (OLD.task_relationship_generation IS NOT NULL"
        " AND NEW.task_relationship_generation IS NOT OLD.task_relationship_generation)"
        " BEGIN SELECT RAISE(ABORT, 'task relationship workspace is immutable'); END"
    )
    # These guards survive a provider rollback. Older queue code cannot admit
    # a governed assignment by forgetting an optional Python field.
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS task_relationship_execution_guard_v3"  # noqa: S608 -- fixed predicate
        " BEFORE UPDATE OF status, generation, owner, owner_session_id ON tasks"
        " WHEN (EXISTS (SELECT 1 FROM task_events e WHERE e.task_id=NEW.id"
        " AND e.task_relationship IS NOT NULL) OR EXISTS (SELECT 1 FROM spawn_reservations s"
        " WHERE s.task_id=NEW.id AND s.task_relationship_required=1)) AND ("
        " (NEW.status='claimed' AND OLD.status='queued' AND NOT EXISTS ("
        " SELECT 1 FROM task_events e WHERE e.task_id=NEW.id"
        " AND e.task_relationship IS NOT NULL"
        " AND COALESCE(json_extract(e.task_relationship, '$.continuation.generation'),"
        " json_extract(e.task_relationship, '$.request.generation'))=NEW.generation"
        " AND e.worker=NEW.owner) AND NOT (NEW.owner IS NULL AND EXISTS ("
        " SELECT 1 FROM spawn_reservations s WHERE s.task_id=NEW.id"
        " AND s.task_relationship_required=1 AND s.task_relationship_generation=NEW.generation)))"
        " OR (NEW.status IN ('started','submitted','completed') AND NOT "
        + bound_relationship_condition("NEW")
        + "))"
        " BEGIN SELECT RAISE(ABORT, 'task relationship protection is unconfirmed'); END"
    )
    # Install the continuation-aware guard before removing its stricter predecessor.
    conn.execute("DROP TRIGGER IF EXISTS task_relationship_execution_guard_v2")

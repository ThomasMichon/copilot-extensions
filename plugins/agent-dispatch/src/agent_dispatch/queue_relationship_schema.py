"""Retain assignment requirements in the existing audit and reservation owners."""

from __future__ import annotations

import sqlite3

from .task_obligations import ASSIGNMENT_PREDICATE


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
        "CREATE TRIGGER IF NOT EXISTS task_relationship_execution_guard"
        " BEFORE UPDATE OF status, generation, owner ON tasks"
        " WHEN (EXISTS (SELECT 1 FROM task_events e WHERE e.task_id=NEW.id"
        " AND e.task_relationship IS NOT NULL) OR EXISTS (SELECT 1 FROM spawn_reservations s"
        " WHERE s.task_id=NEW.id AND s.task_relationship_required=1)) AND ("
        " (NEW.status='claimed' AND OLD.status='queued' AND NOT EXISTS ("
        " SELECT 1 FROM task_events e WHERE e.task_id=NEW.id"
        " AND e.task_relationship IS NOT NULL"
        " AND json_extract(e.task_relationship, '$.request.generation')=NEW.generation"
        " AND e.worker=NEW.owner) AND NOT (NEW.owner IS NULL AND EXISTS ("
        " SELECT 1 FROM spawn_reservations s WHERE s.task_id=NEW.id"
        " AND s.task_relationship_required=1 AND s.task_relationship_generation=NEW.generation)))"
        " OR (NEW.status IN ('started','submitted','completed') AND"
        " (SELECT json_extract(e.task_relationship, '$.state') FROM task_events e"
        " WHERE e.task_id=NEW.id AND e.task_relationship IS NOT NULL"
        " ORDER BY e.id DESC LIMIT 1) IS NOT 'bound'))"
        " BEGIN SELECT RAISE(ABORT, 'task relationship protection is unconfirmed'); END"
    )

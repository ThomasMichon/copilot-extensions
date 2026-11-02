"""Additive durable queue fencing, including writes from legacy TaskStore code."""

from __future__ import annotations

import sqlite3


def install_hints_schema(conn: sqlite3.Connection) -> None:
    """Add nullable payloads and preserve the fence across worker rollback.

    Older retry/release/status code writes ``queued`` unconditionally. SQLite
    keeps those rows parked even when that code runs from an older runtime.
    """
    conn.execute("BEGIN IMMEDIATE")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
    if "change_hints" not in columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN change_hints TEXT")
    for trigger in ("""
        CREATE TRIGGER IF NOT EXISTS tasks_park_hints_insert
        AFTER INSERT ON tasks
        WHEN NEW.change_hints IS NOT NULL AND NEW.status = 'queued'
        BEGIN
            UPDATE tasks SET status = 'queued_hints' WHERE id = NEW.id;
        END;
        """, """
        CREATE TRIGGER IF NOT EXISTS tasks_park_hints_update
        AFTER UPDATE OF status ON tasks
        WHEN NEW.change_hints IS NOT NULL AND NEW.status = 'queued'
        BEGIN
            UPDATE tasks SET status = 'queued_hints' WHERE id = NEW.id;
        END;
        """, """
        CREATE TRIGGER IF NOT EXISTS tasks_hints_immutable
        BEFORE UPDATE OF change_hints, source, full ON tasks
        WHEN OLD.change_hints IS NOT NULL AND (
            NEW.change_hints IS NULL OR NEW.source != OLD.source OR NEW.full != OLD.full
            OR (NEW.change_hints != OLD.change_hints AND OLD.status != 'queued_hints')
        )
        BEGIN
            SELECT RAISE(ABORT, 'Active change hints and their scope are immutable');
        END;
    """):
        conn.execute(trigger)
    conn.commit()

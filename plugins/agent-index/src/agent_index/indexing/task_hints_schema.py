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
    if "change_hints_version" not in columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN change_hints_version INTEGER NOT NULL DEFAULT 1")
    if "point_change_hints" not in columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN point_change_hints TEXT")
    if "point_claim_id" not in columns:
        conn.execute("ALTER TABLE tasks ADD COLUMN point_claim_id TEXT")
    for name in ("tasks_park_hints_insert", "tasks_park_hints_update", "tasks_hints_immutable"):
        conn.execute(f"DROP TRIGGER IF EXISTS {name}")
    for trigger in ("""
        CREATE TRIGGER tasks_park_hints_insert
        AFTER INSERT ON tasks
        WHEN (NEW.change_hints IS NOT NULL OR NEW.change_hints_version != 1)
          AND NEW.status IN ('queued', 'queued_hints', 'queued_hints_v2')
        BEGIN
            UPDATE tasks SET status = CASE NEW.change_hints_version
                WHEN 1 THEN 'queued_hints' ELSE 'queued_hints_v2' END
            WHERE id = NEW.id;
        END;
        """, """
        CREATE TRIGGER tasks_park_hints_update
        AFTER UPDATE OF status ON tasks
        WHEN (NEW.change_hints IS NOT NULL OR NEW.change_hints_version != 1)
          AND NEW.status IN ('queued', 'queued_hints', 'queued_hints_v2')
          AND NEW.status != CASE NEW.change_hints_version
              WHEN 1 THEN 'queued_hints' ELSE 'queued_hints_v2' END
        BEGIN
            UPDATE tasks SET status = CASE NEW.change_hints_version
                WHEN 1 THEN 'queued_hints' ELSE 'queued_hints_v2' END
            WHERE id = NEW.id;
        END;
        """, """
        CREATE TRIGGER tasks_hints_immutable
        BEFORE UPDATE OF change_hints, point_change_hints, change_hints_version, source, full ON tasks
        WHEN (OLD.change_hints IS NOT NULL OR OLD.point_change_hints IS NOT NULL) AND (
            (OLD.change_hints IS NOT NULL AND NEW.change_hints IS NULL)
            OR (OLD.point_change_hints IS NOT NULL AND NEW.point_change_hints IS NULL)
            OR (NEW.change_hints IS NOT NULL AND NEW.point_change_hints IS NOT NULL)
            OR NEW.source != OLD.source OR NEW.full != OLD.full
            OR NEW.change_hints_version != OLD.change_hints_version
            OR ((NEW.change_hints IS NOT OLD.change_hints
                 OR NEW.point_change_hints IS NOT OLD.point_change_hints)
                AND OLD.status NOT IN ('queued_hints', 'queued_hints_v2'))
        )
        BEGIN
            SELECT RAISE(ABORT, 'Active change hints and their scope are immutable');
        END;
        """, """
        CREATE TRIGGER IF NOT EXISTS tasks_point_execution_unsupported_insert
        BEFORE INSERT ON tasks
        WHEN NEW.change_hints_version != 1 AND NEW.status NOT IN
            ('queued', 'queued_hints', 'queued_hints_v2', 'cancelled', 'failed', 'interrupted')
        BEGIN
            SELECT RAISE(ABORT, 'Change-hints v2 execution is unsupported; claim remains parked');
        END;
        """, """
        CREATE TRIGGER IF NOT EXISTS tasks_point_execution_unsupported_update
        BEFORE UPDATE OF status ON tasks
        WHEN NEW.change_hints_version != 1 AND NEW.status NOT IN
            ('queued', 'queued_hints', 'queued_hints_v2', 'cancelled', 'failed', 'interrupted')
        BEGIN
            SELECT RAISE(ABORT, 'Change-hints v2 execution is unsupported; claim remains parked');
        END;
        """, """
        CREATE TRIGGER IF NOT EXISTS tasks_point_worker_unsupported
        BEFORE UPDATE OF worker_pid, worker_host, worker_version ON tasks
        WHEN NEW.change_hints_version != 1
            AND (NEW.worker_pid IS NOT NULL OR NEW.worker_host IS NOT NULL
                 OR NEW.worker_version IS NOT NULL)
        BEGIN
            SELECT RAISE(ABORT, 'Change-hints v2 execution is unsupported; worker not registered');
        END;
        """, """
        CREATE TRIGGER IF NOT EXISTS tasks_point_claim_insert
        BEFORE INSERT ON tasks
        WHEN NEW.point_claim_id IS NOT NULL
        BEGIN
            SELECT RAISE(ABORT, 'Point claims must pin an existing parked task');
        END;
        """, """
        CREATE TRIGGER IF NOT EXISTS tasks_point_claim_pin
        BEFORE UPDATE OF point_claim_id ON tasks
        WHEN OLD.point_claim_id IS NULL AND NEW.point_claim_id IS NOT NULL AND (
            NEW.status != 'queued_hints_v2' OR NEW.change_hints_version != 2
            OR NEW.full != 0 OR NEW.change_hints IS NOT NULL
            OR NEW.point_change_hints IS NULL
            OR length(NEW.point_claim_id) != 32
            OR NEW.point_claim_id GLOB '*[^0-9a-f]*'
        )
        BEGIN
            SELECT RAISE(ABORT, 'Point claims require a parked v2 task and claim identity');
        END;
        """, """
        CREATE TRIGGER IF NOT EXISTS tasks_point_claim_immutable
        BEFORE UPDATE OF id, source, full, change_hints, point_change_hints,
            change_hints_version, point_claim_id ON tasks
        WHEN OLD.point_claim_id IS NOT NULL
        BEGIN
            SELECT RAISE(ABORT, 'Pinned point claim and its entire scope are immutable');
        END;
        """, """
        CREATE TRIGGER IF NOT EXISTS tasks_point_completed_immutable
        BEFORE UPDATE ON tasks
        WHEN OLD.change_hints_version = 2 AND OLD.status = 'complete'
        BEGIN
            SELECT RAISE(ABORT, 'Completed point claim outcome is immutable');
        END;
    """):
        conn.execute(trigger)
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_point_claim "
        "ON tasks(point_claim_id) WHERE point_claim_id IS NOT NULL",
    )
    conn.commit()

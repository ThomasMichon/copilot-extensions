"""Atomic installation of the pre-start placement projection and driver lookup indexes."""

from __future__ import annotations

import sqlite3

from .current_driver_lookup import install_indexes

_TABLE = "current_driver_pool_v1"
_SOURCE = "current_driver_pool_source_v1"


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        if conn.execute(
            "SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?", (_TABLE,),
        ).fetchone() is None:
            _install(conn)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _install(conn: sqlite3.Connection) -> None:
    # Only this cross-table binding needs materialization. Process/slot status is
    # intentionally not an input: selected placement and explicit release own its lifetime.
    conn.execute(
        f"CREATE VIEW {_SOURCE} AS SELECT t.id AS task_id,"  # noqa: S608 -- fixed source view
        " CASE WHEN NULLIF(s.worktree,'') IS NOT NULL THEN a.reservation_key END"
        " AS reservation_key,"
        " CASE WHEN NULLIF(s.worktree,'') IS NOT NULL THEN a.machine"
        " ELSE COALESCE(t.target_machine,'') END AS machine,"
        " COALESCE(NULLIF(s.worktree,''),t.target_worktree) AS tree,"
        " NULLIF(s.worktree,'') IS NOT NULL AS placed"
        " FROM tasks t LEFT JOIN task_pool_assignments a ON a.task_id=t.id"
        " AND a.assignment_released=0 AND t.generation=a.task_generation"
        " LEFT JOIN spawn_reservations s ON s.key=a.reservation_key AND s.task_id=t.id"
        " WHERE t.status='claimed' AND t.owner IS NULL AND tree IS NOT NULL AND tree<>''"
    )
    conn.execute(
        f"CREATE TABLE {_TABLE}(task_id TEXT PRIMARY KEY,reservation_key TEXT UNIQUE,"
        "machine TEXT NOT NULL,tree TEXT NOT NULL,placed INTEGER NOT NULL)"
    )
    conn.execute(f"INSERT INTO {_TABLE} SELECT * FROM {_SOURCE}")  # noqa: S608 -- fixed schema
    for table, task, key, columns in (
        ("tasks", "id", None, "id,status,owner,generation,target_machine,target_worktree"),
        ("task_pool_assignments", "task_id", "reservation_key",
         "task_id,reservation_key,machine,assignment_released,task_generation"),
        ("spawn_reservations", "task_id", "key", "task_id,key,worktree"),
    ):
        for operation, refs in (("INSERT", ("NEW",)), ("UPDATE", ("OLD", "NEW")),
                                ("DELETE", ("OLD",))):
            ids = ",".join(f"{ref}.{task}" for ref in refs)
            clause = f"task_id IN ({ids})"
            remove = clause
            if key:
                keys = ",".join(f"{ref}.{key}" for ref in refs)
                remove += f" OR reservation_key IN ({keys})"
            displaced = ""
            if key:
                # REPLACE on a unique reservation key can silently displace a
                # different task. Refresh its surviving pin before deleting the old key.
                displaced = (
                    f" UPDATE {_TABLE} SET (reservation_key,machine,tree,placed)="  # noqa: S608 -- fixed schema
                    f"(SELECT reservation_key,machine,tree,placed FROM {_SOURCE} s"
                    f" WHERE s.task_id={_TABLE}.task_id)"
                    f" WHERE reservation_key IN ({keys}) AND task_id NOT IN ({ids})"
                    f" AND EXISTS (SELECT 1 FROM {_SOURCE} s WHERE s.task_id={_TABLE}.task_id);"
                )
            event = "UPDATE OF " + columns if operation == "UPDATE" else operation
            conn.execute(
                f"CREATE TRIGGER {_TABLE}_{table}_{operation} AFTER {event} ON {table}"  # noqa: S608 -- fixed schema
                f" BEGIN {displaced} DELETE FROM {_TABLE} WHERE {remove};"
                f" INSERT INTO {_TABLE} SELECT * FROM {_SOURCE} WHERE {clause}; END"
            )
    install_indexes(conn)

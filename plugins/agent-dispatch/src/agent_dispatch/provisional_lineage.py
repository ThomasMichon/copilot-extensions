"""Indexed read-only form of the no-creation-prefix execution reconciliation fence."""

import sqlite3
from typing import Any

from .queue_common import Task
from .queue_execution_custody import ExecutionSendUnresolved
from .queue_execution_wakes import TABLES


def migrate(conn: sqlite3.Connection) -> None:
    for name, table, keys, present in (
        ("excl", "tasks", "exclusive_key,id", "exclusive_key"),
        ("logical", "tasks", "repo,dedup_key,id", "dedup_key"),
        ("reservation", "spawn_reservations", "exclusive_key,key", "exclusive_key"),
        ("handle", "spawn_reservations", "exclusive_key,session_handle", "session_handle"),
    ):
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_provisional_{name} "
                     f"ON {table}({keys}) WHERE {present} IS NOT NULL")


def require_clear(conn: sqlite3.Connection, task: Task) -> None:
    # Same task, exclusive resource or logical workspace as execution_lineage_match_sql.
    # No creation prefix is supplied for this operation. Only sends (not wakes)
    # additionally inherit the reservation's original exclusive key.
    ids = "SELECT :id"
    params: dict[str, Any] = {"id": task.id, "exclusive": task.exclusive_key,
                              "repo": task.repo, "dedup": task.dedup_key}
    if task.exclusive_key is not None:
        ids += " UNION ALL SELECT id FROM tasks WHERE exclusive_key=:exclusive"
    if task.dedup_key is not None and task.repo is not None:
        ids += " UNION ALL SELECT id FROM tasks WHERE repo=:repo AND dedup_key=:dedup"
    for table in TABLES:
        if conn.execute(
            f"SELECT 1 FROM {table} INDEXED BY idx_{table}_unresolved_send"  # noqa: S608 -- fixed SQL
            f" WHERE execution_send_state IN ('pending', 'unknown')"
            f" AND task_id IN ({ids}) LIMIT 1",
            params,
        ).fetchone():
            raise ExecutionSendUnresolved("unresolved execution wake send in driver lineage")
    if conn.execute(
        "SELECT 1 FROM execution_send_custody INDEXED BY idx_execution_send_unresolved"  # noqa: S608 -- fixed SQL
        f" WHERE state IN ('pending', 'unknown') AND task_id IN ({ids}) LIMIT 1", params,
    ).fetchone():
        raise ExecutionSendUnresolved("unresolved execution send in driver lineage")
    if task.exclusive_key is not None and conn.execute(
        "SELECT 1 FROM spawn_reservations r CROSS JOIN execution_send_custody c"
        " ON c.reservation_key=r.key WHERE r.exclusive_key=? AND c.state IN ('pending','unknown')"
        " LIMIT 1", (task.exclusive_key,),
    ).fetchone():
        raise ExecutionSendUnresolved("unresolved execution send on driver resource")

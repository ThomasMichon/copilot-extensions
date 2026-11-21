"""Read the conclusion owner's outstanding workspace work without claiming its retry."""

import sqlite3
from collections.abc import Callable
from contextlib import closing
from typing import cast

from .bridge_remote import normalize_host
from .current_driver_lookup import DriverMatch
from .queue_common import Task
from .queue_pool_placement import reservation_hosts

_INDEX = "idx_pending_conclusion_workspace_v1"
_PENDING = "conclusion_state='pending'"
_CANDIDATE_LIMIT = 32


def install_index(conn: sqlite3.Connection) -> None:
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS {_INDEX} ON spawn_reservations(worktree) WHERE {_PENDING}"
    )


def pending_workspace_conclusion(
    conn: sqlite3.Connection, *, machine: str, worktree: str,
) -> DriverMatch:
    """Inspect pending conclusions for this workspace in the caller's read snapshot."""
    host = normalize_host(machine)
    if not conn.in_transaction or not host or not worktree:
        raise ValueError("pending conclusion lookup requires a transaction and workspace identity")
    answer = DriverMatch.CLEAR
    with closing(conn.cursor()) as candidates, closing(conn.cursor()) as tasks:
        candidates.row_factory = tasks.row_factory = cast(
            Callable[[sqlite3.Cursor, sqlite3.Row], object], sqlite3.Row,
        )
        candidates.execute(
            f"SELECT task_id,worktree,session_handle,execution_machine,creating_host"  # noqa: S608 -- fixed SQL
            f" FROM spawn_reservations INDEXED BY {_INDEX}"
            f" WHERE {_PENDING} AND worktree=? LIMIT ?",
            (worktree, _CANDIDATE_LIMIT + 1),
        )
        for i, row in enumerate(candidates):
            if i == _CANDIDATE_LIMIT:
                return DriverMatch.UNKNOWN
            owned = tasks.execute(
                "SELECT * FROM tasks WHERE id=? AND owner IS NOT NULL", (row["task_id"],),
            ).fetchone()
            hosts = reservation_hosts(row, Task._from_row(owned) if owned is not None else None)
            if hosts == {host}:
                return DriverMatch.MATCH
            if len(hosts) != 1:
                answer = DriverMatch.UNKNOWN
    return answer

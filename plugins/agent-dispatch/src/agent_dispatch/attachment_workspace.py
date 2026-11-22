"""Capture the allocation's workspace identity at the time of a native attachment."""

from __future__ import annotations

import sqlite3

from .bridge_remote import normalize_host
from .ordinary_native_bindings import reservation_native_identity
from .queue_records import TaskError


def install(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS attachment_workspaces ("
        "attachment_id INTEGER PRIMARY KEY REFERENCES task_attachments(id) ON DELETE CASCADE,"
        "reservation_key TEXT NOT NULL, allocation_project TEXT NOT NULL, worktree_path TEXT NOT NULL)"
    )


def capture(
    conn: sqlite3.Connection, attachment_id: int | None, task_id: str, native: str,
    tree: str | None, machine: str | None,
) -> None:
    if attachment_id is None or not tree or not machine or not machine.strip():
        return
    rows = conn.execute(
        "SELECT * FROM spawn_reservations WHERE task_id=? "
        "AND state IN ('reserving','spawned','cold','releasing') LIMIT 2", (task_id,),
    ).fetchall()
    if len(rows) != 1:
        return
    row = rows[0]
    project, path, host = row["allocation_project"], row["worktree_path"], row["execution_machine"]
    if (row["worktree"] != tree or row["release_requested"] or row["conversation_retired"]
            or row["state"] == "releasing"
            or not all(isinstance(s, str) and s.strip() for s in (project, path, host))
            or normalize_host(host) != normalize_host(machine)):
        return
    try:
        bound = reservation_native_identity(conn, row)
    except (TaskError, TypeError, ValueError, KeyError):
        return  # Unusable optional provenance must not qualify or reject the attachment.
    if bound == native:
        conn.execute(
            "INSERT INTO attachment_workspaces(attachment_id,reservation_key,allocation_project,worktree_path) "
            "VALUES (?,?,?,?)", (attachment_id, row["key"], project, path),
        )

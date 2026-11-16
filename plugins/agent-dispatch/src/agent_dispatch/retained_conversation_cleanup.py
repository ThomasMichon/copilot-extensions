"""Attempt retirement does not retire a still-needed hosted conversation."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

from .queue_records import Status, TaskError


def pending_conversation(
    conn: sqlite3.Connection, reservation: Mapping[str, Any] | sqlite3.Row,
) -> bool:
    """Keep an ordinary local handle until its task or conversation is retired."""
    handle = reservation["session_handle"]
    if (
        reservation["driver"] != "ordinary-bridge"
        or reservation["conversation_retired"]
        or not isinstance(handle, str)
        or not handle.startswith("local-body:")
    ):
        return False
    task = conn.execute(
        "SELECT status FROM tasks WHERE id=?", (reservation["task_id"],),
    ).fetchone()
    return task is None or task["status"] not in Status.TERMINAL


def require_conversation_released(
    conn: sqlite3.Connection, reservation: Mapping[str, Any] | sqlite3.Row,
) -> None:
    """Recheck conversation custody at the existing cleanup write boundary."""
    if pending_conversation(conn, reservation):
        raise TaskError("Original non-terminal conversation excludes session cleanup")

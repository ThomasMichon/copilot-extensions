"""Original managed creation evidence, independent of current producer eligibility."""

from __future__ import annotations

import sqlite3

from .queue_common import Task


def task_producer_receipt(conn: sqlite3.Connection, task: Task) -> dict | None:
    """Return original public receipt fields only when the task fence agrees."""
    fence = task.producer_fence
    if not fence or (
        fence.get("scope") != {"repo": task.repo, "source": task.source}
        or fence.get("producer_id") != task.origin_ref
    ):
        return None
    row = conn.execute(
        "SELECT repo,source,generation,request_id,producer_id,task_id"
        " FROM producer_create_requests WHERE repo=? AND source=?"
        " AND generation=? AND request_id=? AND producer_id=? AND task_id=?",
        (task.repo, task.source, fence.get("generation"), fence.get("request_id"),
         task.origin_ref, task.id),
    ).fetchone()
    return dict(row) if row is not None else None

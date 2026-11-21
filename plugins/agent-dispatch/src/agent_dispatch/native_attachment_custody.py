"""Compare reservation effects with the native's durable assignment history."""

import sqlite3

from .queue_records import TaskError


def require_not_reassigned(
    conn: sqlite3.Connection, row: sqlite3.Row, target: str | None = None,
) -> None:
    from .ordinary_native_bindings import reservation_native_identity

    transfers = conn.execute(
        "SELECT DISTINCT a.session_id FROM task_attachments a JOIN task_attachments later "
        "ON later.session_id=a.session_id AND later.machine IS a.machine AND later.id>a.id "
        "WHERE a.task_id=? AND a.worktree_id IS ? AND a.detached_at IS NOT NULL "
        "AND later.task_id<>a.task_id AND NOT EXISTS (SELECT 1 FROM task_attachments back "
        "WHERE back.task_id=a.task_id AND back.session_id=a.session_id "
        "AND back.machine IS a.machine AND back.worktree_id IS a.worktree_id AND back.id>later.id)",
        (row["task_id"], row["worktree"]),
    ).fetchall()
    if transfers:
        native = reservation_native_identity(conn, row)
        if any(t["session_id"] in (native, target, row["session_handle"]) for t in transfers):
            raise TaskError("native attached to another task after leaving this assignment")


def require_no_prior_effect(
    conn: sqlite3.Connection, task_id: str, native: str, machine: str | None,
) -> None:
    """Serialize a new attachment against already-admitted redrive or terminal cleanup."""
    from .ordinary_native_bindings import reservation_native_identity

    rows = conn.execute(
        "SELECT s.* FROM spawn_reservations s WHERE s.state IN ('spawned','cold','releasing') "
        "AND (s.startup_fenced OR (NOT s.startup_complete AND s.startup_revision>1)) "
        "AND s.task_id<>? AND EXISTS (SELECT 1 FROM task_attachments a WHERE a.task_id=s.task_id "
        "AND a.session_id=? AND a.machine IS ? AND a.worktree_id IS s.worktree "
        "AND a.detached_at IS NOT NULL)",
        (task_id, native, machine),
    )
    if any(row["session_handle"] == native or reservation_native_identity(conn, row) == native
           for row in rows):
        raise TaskError("original native delivery or terminal cleanup remains admitted")

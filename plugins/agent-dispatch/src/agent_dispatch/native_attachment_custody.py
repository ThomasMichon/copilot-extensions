"""Compare reservation effects with the native's durable assignment history."""

import sqlite3

from .bridge_remote import normalize_host
from .queue_records import TaskError


def _host(value: str | None) -> str:
    return normalize_host(value or "")


def require_not_reassigned(
    conn: sqlite3.Connection, row: sqlite3.Row, target: str | None = None,
) -> None:
    from .ordinary_native_bindings import reservation_native_identity

    task_id = row["task_id"]
    originals = conn.execute(
        "SELECT id,session_id,machine,worktree_id FROM task_attachments WHERE task_id=? "
        "AND (worktree_id=? OR worktree_id IS NULL OR ? IS NULL)",
        (task_id, row["worktree"], row["worktree"]),
    )
    for original in originals:
        later = conn.execute(
            "SELECT task_id,machine,worktree_id,session_id FROM task_attachments "
            "WHERE (session_id=? OR worktree_id=?) AND id>? ORDER BY id DESC",
            (original["session_id"], original["worktree_id"], original["id"]),
        )
        returned: set[tuple[str, str]] = set()
        for event in later:
            host = _host(event["machine"])
            if event["task_id"] == task_id:
                if host and host == _host(original["machine"]) and (
                    original["worktree_id"] and event["worktree_id"] == original["worktree_id"]
                ):
                    returned.add((host, event["session_id"]))
                continue
            native = reservation_native_identity(conn, row)
            if native == event["session_id"] and (host, native) in returned:
                continue
            if native is not None and event["session_id"] not in (native, target):
                continue
            raise TaskError("native attached to another task after leaving this assignment")


def require_no_prior_effect(
    conn: sqlite3.Connection, task_id: str, native: str, worktree: str | None,
) -> None:
    """Serialize a new attachment against already-admitted redrive or terminal cleanup."""
    from .ordinary_native_bindings import reservation_native_identity

    rows = conn.execute(
        "SELECT s.* FROM spawn_reservations s WHERE s.task_id<>? AND ("
        "(s.state IN ('spawned','cold','releasing') AND (s.state='releasing' "
        "OR s.cooling_token IS NOT NULL OR s.startup_fenced "
        "OR (NOT s.startup_complete AND s.startup_revision>1))) "
        "OR (s.state IN ('failed','settled') AND s.conclusion_state IN ('pending','held'))) "
        "AND EXISTS (SELECT 1 FROM task_attachments a WHERE a.task_id=s.task_id "
        "AND (a.session_id=? OR a.worktree_id=?) AND (a.worktree_id=s.worktree OR s.worktree IS NULL "
        "OR a.worktree_id IS NULL))",
        (task_id, native, worktree),
    )
    # A different machine spelling is not proof of a distinct endpoint.
    # Only candidates sharing a native or checkout with a prior assignment reach here.
    if any(reservation_native_identity(conn, row) in (None, native) for row in rows):
        raise TaskError("original native delivery or terminal cleanup remains admitted")

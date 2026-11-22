"""Compare reservation effects with the native's durable assignment history."""

import sqlite3

from .bridge_remote import normalize_host
from .ordinary_native_bindings import reservation_native_identity
from .queue_common import Task, _claimant_worktree_machine
from .queue_records import TaskError


def _host(value: str | None) -> str:
    return normalize_host(value or "")


def require_not_reassigned(
    conn: sqlite3.Connection, row: sqlite3.Row, target: str | None = None,
    worktree: str | None = None,
) -> None:
    task_id = row["task_id"]
    tree = row["worktree"] or worktree
    native = reservation_native_identity(conn, row)
    if conn.execute(
        "SELECT 1 FROM tasks WHERE id<>? AND status IN ('claimed','started','suspended') "
        "AND owner_session_id IN (?,?) LIMIT 1", (task_id, native, target),
    ).fetchone():
        raise TaskError("native is bound to another task")
    originals = conn.execute(
        "SELECT id,session_id,machine,worktree_id FROM task_attachments WHERE task_id=? "
        "AND (worktree_id=? OR worktree_id IS NULL OR ? IS NULL)",
        (task_id, tree, tree),
    )
    has_original = False
    for original in originals:
        has_original = True
        later = conn.execute(
            "SELECT task_id,machine,worktree_id,session_id FROM task_attachments "
            "WHERE (session_id IN (?,?,?) OR worktree_id=?) AND id>? ORDER BY id DESC",
            (original["session_id"], native, target, original["worktree_id"] or tree, original["id"]),
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
            if native == event["session_id"] and (host, native) in returned:
                continue
            if native is not None and event["session_id"] not in (native, target):
                continue
            raise TaskError("native attached to another task after leaving this assignment")
    # A worker may publish a body and never START. Without an original
    # attachment there is no event ordering that proves a return to this task.
    if not has_original and conn.execute(
        "SELECT 1 FROM task_attachments WHERE task_id<>? AND "
        "(session_id IN (?,?) OR (? IS NULL AND worktree_id=?)) LIMIT 1",
        (task_id, native, target, native, tree),
    ).fetchone():
        raise TaskError("reservation target attached to another task without original attachment history")


def require_no_prior_effect(
    conn: sqlite3.Connection, task_id: str, native: str, worktree: str | None,
) -> None:
    """Serialize a new attachment against already-admitted redrive or terminal cleanup."""
    rows = conn.execute(
        "SELECT s.*,r.native_session_id FROM spawn_reservations s "
        "LEFT JOIN acquisition_receipts r ON r.reservation_key=s.key WHERE s.task_id<>? AND ("
        "(s.state IN ('spawned','cold','releasing') AND (s.state='releasing' "
        "OR s.cooling_token IS NOT NULL OR s.startup_fenced "
        "OR (NOT s.startup_complete AND s.startup_revision>1))) "
        "OR (s.state IN ('failed','settled') AND s.conclusion_state IN ('pending','held'))) "
        "AND (s.worktree=? OR s.session_handle=? OR r.native_session_id=? "
        "OR CASE WHEN json_valid(s.agent_info) THEN ? IN (json_extract(s.agent_info,'$.native_session_id'),"
        "json_extract(s.agent_info,'$.acp_session_id'),json_extract(s.agent_info,'$.connection.native_session_id')) END "
        "OR EXISTS (SELECT 1 FROM task_attachments a WHERE a.task_id=s.task_id "
        "AND (a.session_id=? OR a.worktree_id=?) AND (a.worktree_id=s.worktree OR s.worktree IS NULL "
        "OR a.worktree_id IS NULL)))",
        (task_id, worktree, native, native, native, native, worktree),
    )
    # A different machine spelling is not proof of a distinct endpoint.
    # Reservation publication can precede any attachment for the original task.
    if any(reservation_native_identity(conn, row) in (None, native) for row in rows):
        raise TaskError("original native delivery or terminal cleanup remains admitted")


def require_retained_resume(conn: sqlite3.Connection, task: Task) -> None:
    if task.owner_session_id:
        _, tree = _claimant_worktree_machine(task)
        require_no_prior_effect(conn, task.id, task.owner_session_id, tree)

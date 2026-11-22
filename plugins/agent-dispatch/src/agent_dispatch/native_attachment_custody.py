"""Compare reservation effects with the native's durable assignment history."""

import json
import sqlite3

from .bridge_remote import normalize_host
from .ordinary_native_bindings import reservation_native_identity
from .queue_common import Task, _claimant_worktree_machine
from .queue_records import TaskError


def _host(value: str | None) -> str:
    return normalize_host(value or "")


_OUTSTANDING = (
    "((state IN ('reserving','spawned','cold','releasing') AND (state='releasing' "
    "OR cooling_token IS NOT NULL OR startup_fenced OR (NOT startup_complete AND "
    "(startup_revision>1 OR session_handle IS NOT NULL OR agent_info IS NOT NULL)))) "
    "OR (state IN ('failed','settled') AND conclusion_state IN ('pending','held')))"
)
_BOUND = "status IN ('claimed','started','suspended')"


def install_indexes(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE INDEX IF NOT EXISTS idx_native_custody_session ON task_attachments(session_id,id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_native_custody_tree ON task_attachments(worktree_id,id)")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_native_custody_owner ON tasks(owner_session_id) WHERE {_BOUND}")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_native_custody_effect_v1 ON spawn_reservations(task_id) "
                 f"WHERE {_OUTSTANDING}")


def require_available_native(conn: sqlite3.Connection, task_id: str, *natives: str | None) -> None:
    for native in set(natives) - {None}:
        if conn.execute(
            f"SELECT 1 FROM tasks WHERE {_BOUND} AND owner_session_id=? AND id<>? LIMIT 1",
            (native, task_id),
        ).fetchone():
            raise TaskError("native is bound to another task")


def _last_other(conn: sqlite3.Connection, task_id: str, key: str | None, *, tree: bool = False) -> int:
    if key is None:
        return 0
    column = "worktree_id" if tree else "session_id"
    row = conn.execute(f"SELECT id FROM task_attachments WHERE {column}=? AND task_id<>? "
                       "ORDER BY id DESC LIMIT 1", (key, task_id)).fetchone()
    return int(row[0]) if row else 0


def _cleanup_checkout(row: sqlite3.Row) -> str | None:
    tree = row["worktree"]
    if not isinstance(tree, str) or not tree or row["state"] not in ("failed", "settled") or (
        row["conclusion_state"] not in ("pending", "held")
    ):
        return None
    try:
        detail = json.loads(row["conclusion_detail"] or "{}")
    except (ValueError, TypeError):
        return tree
    component = detail.get("worktree_cleanup") if isinstance(detail, dict) else None
    if isinstance(component, dict) and component.get("state") == "complete" and isinstance(component.get("outcome"), dict):
        return None
    return tree  # Legacy or incomplete evidence cannot narrow the lease to process-only.


def require_checkout_cleanup_custody(conn: sqlite3.Connection, row: sqlite3.Row) -> None:
    tree = _cleanup_checkout(row)
    if tree is None:
        return
    task = row["task_id"]
    first = conn.execute("SELECT min(id) FROM task_attachments WHERE task_id=? AND worktree_id=?",
                         (task, tree)).fetchone()[0]
    if _last_other(conn, task, tree, tree=True) > (first or 0) or conn.execute(
        "SELECT 1 FROM task_attachments a JOIN tasks t ON t.id=a.task_id WHERE a.worktree_id=? "
        "AND a.task_id<>? AND a.detached_at IS NULL AND a.session_id=t.owner_session_id "
        "AND t.status IN ('claimed','started','suspended','submitted') LIMIT 1", (tree, task),
    ).fetchone():
        raise TaskError("cleanup checkout belongs to another task")


def require_not_reassigned(
    conn: sqlite3.Connection, row: sqlite3.Row, target: str | None = None,
    worktree: str | None = None,
) -> None:
    task_id = row["task_id"]
    tree = row["worktree"] or worktree
    native = reservation_native_identity(conn, row)
    require_available_native(conn, task_id, native, target)
    originals = conn.execute(
        "SELECT min(id) first_id,session_id,machine,worktree_id FROM task_attachments WHERE task_id=? "
        "AND (worktree_id=? OR worktree_id IS NULL OR ? IS NULL) GROUP BY session_id,machine,worktree_id",
        (task_id, tree, tree),
    ).fetchall()
    since = min((int(row["first_id"]) for row in originals), default=0)
    conflict = "native attached to another task after leaving this assignment"
    if target != native and _last_other(conn, task_id, target) > since:
        raise TaskError(conflict)
    if native is None:
        session_starts: dict[str, int] = {}
        tree_starts: dict[str, int] = {}
        for row in originals:
            for key, starts in ((row["session_id"], session_starts), (row["worktree_id"] or tree, tree_starts)):
                if key:
                    starts[key] = min(row["first_id"], starts.get(key, row["first_id"]))
        if not originals and tree:
            tree_starts[tree] = 0
        if (any(_last_other(conn, task_id, k) > v for k, v in session_starts.items())
                or any(_last_other(conn, task_id, k, tree=True) > v for k, v in tree_starts.items())):
            raise TaskError(conflict)
        return
    if _last_other(conn, task_id, native) <= since:
        return

    # One indexed pass over this native, not one suffix scan per original attachment.
    foreign: dict[str, int] = {}
    returned: dict[tuple[str, str], int] = {}
    for event in conn.execute(
        "SELECT (task_id=?) is_original,machine,worktree_id,max(id) last_id FROM task_attachments "
        "WHERE session_id=? AND id>? GROUP BY is_original,machine,worktree_id", (task_id, native, since),
    ):
        host, last = _host(event["machine"]), event["last_id"]
        if not event["is_original"]:
            foreign[host] = max(foreign.get(host, 0), last)
        elif host and event["worktree_id"]:
            key = (host, event["worktree_id"])
            returned[key] = max(returned.get(key, 0), last)
    if not originals and foreign:
        raise TaskError(conflict)
    newest = sorted(foreign.items(), key=lambda item: item[1], reverse=True)[:2]
    for row in originals:
        host, first = _host(row["machine"]), row["first_id"]
        own = returned.get((host, row["worktree_id"]), 0)
        if any(other != host and last > first for other, last in newest) or foreign.get(host, 0) > max(first, own):
            raise TaskError(conflict)


def require_no_prior_effect(
    conn: sqlite3.Connection, task_id: str, native: str, worktree: str | None,
) -> None:
    """Serialize a new attachment against already-admitted redrive or terminal cleanup."""
    rows = conn.execute(
        "SELECT s.*,r.native_session_id FROM spawn_reservations s INDEXED BY idx_native_custody_effect_v1 "
        f"LEFT JOIN acquisition_receipts r ON r.reservation_key=s.key WHERE s.task_id<>? AND {_OUTSTANDING} "
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
    if any((worktree is not None and _cleanup_checkout(row) == worktree)
           or reservation_native_identity(conn, row) in (None, native) for row in rows):
        raise TaskError("original native delivery or terminal cleanup remains admitted")


def require_retained_resume(conn: sqlite3.Connection, task: Task) -> None:
    if task.owner_session_id:
        _, tree = _claimant_worktree_machine(task)
        require_no_prior_effect(conn, task.id, task.owner_session_id, tree)

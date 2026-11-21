"""Assignment fences evaluated inside the owner's task transition transaction."""

import sqlite3

from .queue_common import Task
from .queue_records import Status, TaskError


def require_disposition(
    conn: sqlite3.Connection, task: Task, worker: str | None, to: str, *, voluntary: bool,
) -> None:
    if not voluntary:
        if protects_disposition(conn, task.id) and (
            to == Status.ABANDONED or retains_assignment(conn, task.id)
        ):
            raise TaskError("spawn reservation or source closeout retains assignment")
        return
    if _pool_retains(conn, task.id):
        raise TaskError("spawn reservation retains assignment; conclude it through its owner")
    if retains_assignment(conn, task.id):
        _require_independent_requeue(conn, task, worker)


def _require_independent_requeue(conn: sqlite3.Connection, task: Task, worker: str | None) -> None:
    """Unassign the actual worker without acting on its retained reservation."""
    from .queue_execution_custody import require_execution_send_reconciliation
    from .queue_relevance import closeout_request
    from .queue_submission_preparation import require_unprepared_submission

    if not worker or task.owner != worker:
        raise TaskError("spawn reservation retains assignment; conclude it through its owner")
    closeout = closeout_request(conn, task.id)
    if closeout is not None and (
        not task.owner_session_id
        or (closeout["owner"], closeout["owner_session_id"], closeout["generation"])
        != (worker, task.owner_session_id, task.generation)
        or closeout["readiness"] is not None or closeout["acknowledgement"] is not None
    ):
        raise TaskError("changed or prepared owner closeout retains assignment")
    if conn.execute(
        "SELECT 1 FROM spawn_reservations WHERE task_id=? AND "
        "(state='releasing' OR native_release_pending=1 OR cooling_token IS NOT NULL)",
        (task.id,),
    ).fetchone():
        raise TaskError("unresolved reservation transition retains assignment")
    require_execution_send_reconciliation(conn, task.id, exclusive_key=task.exclusive_key)
    require_unprepared_submission(conn, task.id)


def supersedable(conn: sqlite3.Connection, repo: str | None, key: str | None) -> list[sqlite3.Row]:
    candidates = conn.execute(
        "SELECT id,status FROM tasks WHERE repo=? AND exclusive_key=? AND status IN (?,?)",
        (repo, key, Status.PROPOSED, Status.QUEUED),
    ).fetchall()
    if any(protects_disposition(conn, row["id"]) for row in candidates):
        raise TaskError("cannot supersede a reserved task; request owner closeout")
    return candidates


def protects_disposition(conn: sqlite3.Connection, task_id: str) -> bool:
    """Only a live pool allocation or an accepted source closeout owns automatic disposal."""
    from .queue_relevance import closeout_request

    closeout = closeout_request(conn, task_id)
    return bool(closeout is not None and closeout["acknowledgement"] is None) or _pool_retains(
        conn, task_id,
    )


def _pool_retains(conn: sqlite3.Connection, task_id: str) -> bool:
    from .queue_pool_placement import pool_assignment

    allocation = pool_assignment(conn, task_id)
    if allocation is None:
        return False
    if conn.execute(
        "SELECT 1 FROM spawn_reservations WHERE task_id=? AND key=?",
        (task_id, allocation.reservation_key),
    ).fetchone() is None:
        return True  # A dangling allocator placement is not evidence of retirement.
    return retains_assignment(conn, task_id, key=allocation.reservation_key)


def retains_assignment(conn: sqlite3.Connection, task_id: str, *, key: str | None = None) -> bool:
    if not conn.in_transaction:
        raise ValueError("reservation assignment requires a transaction")
    return conn.execute(
        "SELECT 1 FROM spawn_reservations WHERE task_id=? AND (? IS NULL OR key=?) AND ("
        "state IN ('reserving','spawned','cold','releasing') OR native_release_pending=1"
        " OR conclusion_state IN ('pending','held')"
        " OR (worktree_ownership='created' AND worktree IS NOT NULL"
        " AND COALESCE(conclusion_state,'') NOT IN ('complete','skipped'))"
        " OR ((session_handle IS NOT NULL OR agent_info IS NOT NULL OR EXISTS ("
        " SELECT 1 FROM acquisition_receipts a WHERE a.reservation_key=spawn_reservations.key))"
        " AND conversation_retired=0"
        " AND NOT (release_requested=1 AND state IN ('failed','settled','rearmed')))) LIMIT 1",
        (task_id, key, key),
    ).fetchone() is not None

"""Assignment fences evaluated inside the owner's task transition transaction."""

import sqlite3

from .queue_common import Task
from .queue_records import Status, TaskError


def require_independent_requeue(conn: sqlite3.Connection, task: Task, worker: str | None) -> None:
    """Unassign the actual worker without acting on its retained reservation."""
    from .queue_execution_custody import require_execution_send_reconciliation
    from .queue_pool_placement import pool_assignment
    from .queue_relevance import closeout_request
    from .queue_submission_preparation import require_unprepared_submission

    # An unreleased allocator claim belongs to its placement owner, even if
    # its catalog entry disappears or its native identity is not yet known.
    if not worker or task.owner != worker or pool_assignment(conn, task.id) is not None:
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
        "SELECT 1 FROM gone_owner_requeues WHERE task_id=?", (task.id,),
    ).fetchone():
        raise TaskError("pending owner closeout retains assignment")
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
    if any(retains_assignment(conn, row["id"]) for row in candidates):
        raise TaskError("cannot supersede a reserved task; request owner closeout")
    return candidates


def retains_assignment(conn: sqlite3.Connection, task_id: str) -> bool:
    if not conn.in_transaction:
        raise ValueError("reservation assignment requires a transaction")
    return conn.execute(
        "SELECT 1 FROM spawn_reservations WHERE task_id=? AND ("
        "state IN ('reserving','spawned','cold','releasing') OR native_release_pending=1"
        " OR conclusion_state IN ('pending','held')"
        " OR (worktree_ownership='created' AND worktree IS NOT NULL"
        " AND COALESCE(conclusion_state,'') NOT IN ('complete','skipped'))"
        " OR ((session_handle IS NOT NULL OR agent_info IS NOT NULL OR EXISTS ("
        " SELECT 1 FROM acquisition_receipts a WHERE a.reservation_key=spawn_reservations.key))"
        " AND conversation_retired=0"
        " AND NOT (release_requested=1 AND state IN ('failed','settled','rearmed')))) LIMIT 1",
        (task_id,),
    ).fetchone() is not None

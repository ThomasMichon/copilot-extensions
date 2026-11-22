"""Fixture for databases where an older provider yielded a failed pool native."""

from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_records import SpawnReservation


def seed_pre_retention_yield(
    q: TaskQueue, reservation: SpawnReservation, *, expected_state: str = "failed",
) -> None:
    """Load the historical queued task; do not retire or alter its reservation."""
    with q._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT r.task_id,r.session_handle,r.worktree,r.execution_machine,t.owner,t.updated_at"
            " FROM spawn_reservations r JOIN tasks t ON t.id=r.task_id"
            " JOIN task_pool_assignments a ON a.task_id=t.id AND a.reservation_key=r.key"
            " WHERE r.key=? AND r.state=? AND r.conversation_retired=0"
            " AND t.status='started' AND t.owner_session_id=r.session_handle"
            " AND t.generation=a.task_generation", (reservation.key, expected_state),
        ).fetchone()
        assert row is not None, reservation.key
        conn.execute(
            "UPDATE tasks SET status='queued',owner=NULL,owner_session_id=NULL,"
            " claimed_at=NULL,lease_expires_at=NULL,last_liveness=NULL,activity=NULL"
            " WHERE id=?", (row["task_id"],),
        )
        q._record_attachment(
            conn, row["task_id"], ts=row["updated_at"], old_session_id=row["session_handle"],
            new_session_id=None, worktree_id=row["worktree"], machine=row["execution_machine"],
            detach_reason="yield",
        )
        q._audit(
            conn, row["task_id"], ts=row["updated_at"], from_status="started", to_status="queued",
            worker=row["owner"], note="yield", unassigned_worker=row["owner"],
        )
        conn.commit()

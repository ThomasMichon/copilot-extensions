"""Seed historical non-pool yield without dropping an ordinary agent reservation."""

import pytest

from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_common import _claimant_worktree_machine
from agent_dispatch.queue_records import TaskError
from tests._helpers import required


def seed_retained_native_yield(queue: TaskQueue, task_id: str, owner: str, native: str) -> None:
    """Old non-pool yield detached the owner without releasing the original native."""
    task = required(queue.get(task_id))
    reservation = required(queue.latest_reservation(task_id))
    assert queue.task_pool_assignment(task_id) is None
    assert reservation.driver == "ordinary-bridge" and not reservation.release_requested
    assert required(reservation.agent_info)["connection"]["native_session_id"] == native
    assert task.owner == owner and task.status in {"claimed", "started"}
    assert task.owner_session_id == (native if task.status == "started" else None)
    with pytest.raises(TaskError, match="retains assignment"):
        queue.yield_task(task_id, owner, release_spawn=False)
    assert queue.get(task_id) == task and queue.get_reservation(reservation.key) == reservation
    with queue._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE tasks SET status='queued', owner=NULL, owner_session_id=NULL,"
            "lease_expires_at=NULL, claimed_at=NULL, last_liveness=NULL, activity=NULL,"
            "activity_updated_at=? WHERE id=?",
            (task.updated_at, task_id),
        )
        machine, worktree = _claimant_worktree_machine(task)
        queue._record_attachment(
            conn,
            task_id,
            ts=task.updated_at,
            old_session_id=task.owner_session_id,
            new_session_id=None,
            machine=machine,
            worktree_id=worktree,
            detach_reason="yield",
        )
        queue._audit(
            conn,
            task_id,
            ts=task.updated_at,
            from_status=task.status,
            to_status="queued",
            worker=owner,
            note="yield",
            unassigned_worker=owner,
        )

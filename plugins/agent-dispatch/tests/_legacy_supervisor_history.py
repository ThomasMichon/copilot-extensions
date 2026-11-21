"""Persisted non-pool supervisor states from before tasks retained their reservations."""

import json

import pytest

from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_common import Task, _claimant_worktree_machine
from agent_dispatch.queue_records import SpawnState, Status, TaskError
from tests._helpers import required


def before_retention_supersession(queue: TaskQueue, prior: Task) -> Task:
    """Old writers superseded a queued task even when it still had a live reservation."""
    key = required(prior.exclusive_key)
    with pytest.raises(TaskError, match="cannot supersede a reserved task"):
        queue.create("new", repo=prior.repo, exclusive_key=key, supersede_exclusive_key=True)
    successor = queue.create("new", repo=prior.repo, exclusive_key=key)
    assert isinstance(successor, Task)
    with queue._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        updated = conn.execute(
            "UPDATE tasks SET status='abandoned',owner=NULL,lease_expires_at=NULL,activity=NULL,"
            "activity_updated_at=?,updated_at=? WHERE id=? AND status='queued' AND owner IS NULL",
            (successor.created_at, successor.created_at, prior.id),
        )
        assert updated.rowcount == 1
        queue._audit(
            conn,
            prior.id,
            ts=successor.created_at,
            from_status=Status.QUEUED,
            to_status=Status.ABANDONED,
            note=f"superseded by exclusive task {successor.id}",
        )
    return successor


def before_retention_release(
    queue: TaskQueue,
    task_id: str,
    owner: str,
    *,
    reason: str | None = None,
    exclude: str | None = None,
) -> None:
    """The old writer unassigned the task and requested teardown, but did not prove exit."""
    task = required(queue.get(task_id))
    reservation = required(queue.latest_reservation(task_id))
    assert task.owner == owner
    assert queue.task_pool_assignment(task_id) is None
    assert task.status == (Status.SUSPENDED if reason else Status.STARTED)
    assert reservation.state == (SpawnState.COLD if reason else SpawnState.SPAWNED)
    with pytest.raises(TaskError, match="retains assignment"):
        if reason:
            queue.release_suspended(task_id, owner, reason=reason)
        else:
            queue.yield_task(task_id, owner, exclude=exclude)
    assert queue.get(task_id) == task and queue.get_reservation(reservation.key) == reservation
    note = reason or "yield"
    excludes = list(task.excludes)
    if exclude and exclude not in excludes:
        excludes.append(exclude)
    with queue._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE tasks SET status='queued',owner=NULL,owner_session_id=NULL,"
            "lease_expires_at=NULL,claimed_at=NULL,last_liveness=NULL,activity=NULL,"
            "activity_updated_at=?,resume_requested=0,excludes=? WHERE id=?",
            (task.updated_at, json.dumps(excludes), task_id),
        )
        conn.execute(
            "UPDATE spawn_reservations SET state='releasing',release_requested=1,"
            "release_disposition='settled',conclusion_state='pending',detail=? WHERE key=?",
            ("task released from suspension" if reason else "task yielded", reservation.key),
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
            detach_reason=note,
        )
        queue._audit(
            conn,
            task_id,
            ts=task.updated_at,
            from_status=task.status,
            to_status=Status.QUEUED,
            worker=owner,
            note=note,
            unassigned_worker=owner,
        )


def before_retention_abandon(queue: TaskQueue, task_id: str, *, reason: str) -> None:
    task = required(queue.get(task_id))
    reservation = required(queue.latest_reservation(task_id))
    assert task.status == Status.QUEUED and task.owner is None
    assert reservation.state in (SpawnState.SPAWNED, SpawnState.COLD)
    with pytest.raises(TaskError, match="retains assignment"):
        queue.abandon(task_id, permitted=True, reason=reason)
    with queue._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE tasks SET status='abandoned',completed_at=?,updated_at=? WHERE id=?",
            (task.updated_at, task.updated_at, task_id),
        )
        queue._audit(
            conn,
            task_id,
            ts=task.updated_at,
            from_status=task.status,
            to_status=Status.ABANDONED,
            worker=None,
            note=reason,
        )


def before_retention_gc(queue: TaskQueue, task_id: str) -> None:
    """Old GC cleared a vanished worker without settling its SPAWNED reservation."""
    task = required(queue.get(task_id))
    reservation = required(queue.latest_reservation(task_id))
    assert task.status == Status.STARTED and task.owner and task.owner_session_id is None
    assert reservation.state == SpawnState.SPAWNED
    counts = queue.reconcile_liveness(headless_local_verdict=lambda _: "gone")
    assert counts["requeued"] == 0 and required(queue.get(task_id)).owner == task.owner
    assert queue.get_reservation(reservation.key) == reservation
    with queue._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE tasks SET status='queued',updated_at=?,owner=NULL,owner_session_id=NULL,"
            "lease_expires_at=NULL,attempts=attempts+1 WHERE id=?",
            (task.updated_at, task_id),
        )
        queue._audit(
            conn,
            task_id,
            ts=task.updated_at,
            from_status=Status.STARTED,
            to_status=Status.QUEUED,
            note="owner-gone",
        )

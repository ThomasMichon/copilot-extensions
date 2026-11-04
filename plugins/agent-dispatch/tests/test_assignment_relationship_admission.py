"""Current-assignment protection survives older writers without starving other work."""

from __future__ import annotations

import copy
import sqlite3
from dataclasses import replace

import pytest

from agent_dispatch.queue import Status, TaskError, TaskQueue
from agent_dispatch.queue_task_relationships import require_relationship
from agent_dispatch.task_obligations import read_snapshot
from agent_dispatch.task_relationships import AssignmentSubject
from tests.test_assignment_relationships import assignment as assignment
from tests.test_assignment_relationships import claim
from tests.test_pool_placement import place, pool, publish


def test_qualified_atomic_create_claim_refuses_without_creating_work(assignment):
    queue, _, workspace, _ = assignment
    before = len(queue.list())
    with pytest.raises(TaskError, match="atomic create-and-claim"):
        queue.create("atomic", repo="example", claim_as=workspace.worker_id)
    assert len(queue.list()) == before


@pytest.mark.parametrize("settled", [False, True])
def test_unpinned_legacy_claim_skips_governed_work_without_starving_other_tasks(
    assignment, settled
):
    queue, _, workspace, task = assignment
    claim(queue, workspace, task)
    queue.yield_task(task.id, workspace.worker_id)
    if settled:
        queue.reconcile_task_relationship(task.id)
    unrelated = queue.create("other", repo="example")
    legacy = TaskQueue(queue.db_path)
    assert legacy.claim_one("ordinary").id == unrelated.id
    with pytest.raises(TaskError, match="contract"):
        legacy.claim_one(workspace.worker_id, task_id=task.id)


def test_previous_bound_assignment_cannot_protect_another_pool_workspace_after_rollback(
    assignment,
):
    queue, _, workspace, task = assignment
    claim(queue, workspace, task)
    queue.yield_task(task.id, workspace.worker_id)
    publish(queue, pool())
    reservation, _ = place(queue, task)
    with queue._connect() as conn:
        conn.execute(
            "UPDATE spawn_reservations SET worktree='different', execution_machine='host-a',"
            " allocation_project='example', worktree_path='/different' WHERE key=?",
            (reservation.key,),
        )
        conn.execute("DROP TRIGGER task_relationship_execution_guard_v2")
        conn.execute(
            "CREATE TRIGGER task_relationship_execution_guard BEFORE UPDATE ON tasks"
            " BEGIN SELECT 1; END"
        )
    legacy = TaskQueue(queue.db_path)
    with legacy._connect() as conn:
        with pytest.raises(TaskError, match="unconfirmed"):
            require_relationship(conn, task.id)
        with pytest.raises(sqlite3.IntegrityError, match="unconfirmed"):
            conn.execute(
                "UPDATE tasks SET status='started', owner='host-a/different' WHERE id=?",
                (task.id,),
            )
    assert queue.get(task.id).status == Status.CLAIMED
    assert queue.task_relationship(task.id)["state"] == "bound"


def test_host_case_alias_start_and_exact_unassignment_settle_original_relationship(assignment):
    queue, owner, workspace, task = assignment
    publish(queue, pool())
    reservation, _ = place(queue, task)
    queue.record_spawn_worktree(
        reservation.key,
        workspace.worktree,
        ownership="created",
        execution_machine=workspace.machine,
        allocation_project=workspace.project,
        worktree_path=workspace.path,
        driver="cli",
        creation_nonce=workspace.creation_nonce,
    )
    queue.record_spawn(reservation.key, session_handle="native", worktree=workspace.worktree)
    worker = "HOST-A/" + workspace.worktree
    queue.start(
        task.id,
        worker,
        owner_session_id="native",
        expected_generation=queue.get(task.id).generation,
    )
    queue.yield_task(task.id, worker)
    with queue._connect() as conn:
        conn.execute(
            "UPDATE task_pool_assignments SET assignment_released=1 WHERE task_id=?", (task.id,)
        )
        conn.execute(
            "UPDATE spawn_reservations SET state='settled' WHERE key=?", (reservation.key,)
        )
    snapshot = read_snapshot(queue, task.id)
    metadata = next(iter(owner.claims.values()))
    subject = AssignmentSubject.decode(
        owner.reference(metadata).split(":", 1)[1], authority="local"
    )
    wrong = copy.deepcopy(snapshot)
    wrong["unassignment"]["worker_id"] = replace(workspace, worktree="different").worker_id
    assert not subject.verification({"task_obligation": wrong})["relationship_verification"][
        "retired"
    ]
    assert subject.verification({"task_obligation": snapshot})["relationship_verification"][
        "retired"
    ]
    assert queue.reconcile_task_relationship(task.id)["state"] == "closed"


def test_submission_keeps_exact_relationship_after_execution_capacity_retires(assignment):
    queue, _, workspace, task = assignment
    publish(queue, pool())
    reservation, _ = place(queue, task)
    queue.record_spawn_worktree(
        reservation.key,
        workspace.worktree,
        ownership="created",
        execution_machine=workspace.machine,
        allocation_project=workspace.project,
        worktree_path=workspace.path,
        driver="cli",
        creation_nonce=workspace.creation_nonce,
    )
    queue.record_spawn(reservation.key, session_handle="native", worktree=workspace.worktree)
    queue.start(
        task.id,
        workspace.worker_id,
        owner_session_id="native",
        expected_generation=queue.get(task.id).generation,
    )
    queue.complete(task.id, workspace.worker_id, result={"answer": "proposed"})
    with queue._connect() as conn:
        conn.execute(
            "UPDATE task_pool_assignments SET assignment_released=1 WHERE task_id=?",
            (task.id,),
        )
        conn.execute(
            "UPDATE spawn_reservations SET state='settled' WHERE key=?", (reservation.key,)
        )
        require_relationship(conn, task.id)
    assert queue.reconcile_task_relationship(task.id)["state"] == "bound"
    queue.confirm(task.id)
    assert queue.reconcile_task_relationship(task.id)["state"] == "closed"

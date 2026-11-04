"""Current-assignment protection survives older writers without starving other work."""

from __future__ import annotations

import copy
import json
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
    before = queue.get(task.id)
    with pytest.raises(TaskError, match="previous task relationship"):
        place(queue, task)
    assert queue.get(task.id) == before
    assert queue.list_reservations(task_id=task.id) == []
    assert queue.task_pool_assignment(task.id) is None
    queue.reconcile_task_relationship(task.id)
    reservation, _ = place(queue, task)
    with queue._connect() as conn:
        value = queue.task_relationship(task.id)
        value["state"] = "bound"
        conn.execute(
            "UPDATE task_events SET task_relationship=? WHERE id=?",
            (json.dumps(value), value["request"]["event_id"]),
        )
        conn.execute(
            "UPDATE spawn_reservations SET worktree='different', execution_machine='host-a',"
            " allocation_project='example', worktree_path='/different' WHERE key=?",
            (reservation.key,),
        )
        conn.execute("DROP TRIGGER task_relationship_execution_guard_v3")
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


def test_suspended_successor_keeps_original_claim_and_fences_old_native(assignment):
    queue, owner, workspace, task = assignment
    claimed = claim(queue, workspace, task)
    queue.start(task.id, workspace.worker_id, owner_session_id="original-native")
    queue.suspend(task.id, workspace.worker_id, reason="handoff", cooldown_seconds=None)
    original = copy.deepcopy(queue.task_relationship(task.id))
    for previous, successor in (
        ("original-native", "successor-native"),
        ("successor-native", "second-successor"),
    ):
        current = queue.get(task.id)
        with pytest.raises(TaskError, match="incarnation changed"):
            queue.resume(
                task.id, workspace.worker_id, adopt_owner_session_id=successor,
                reuse_session=True, expected_generation=current.generation,
                expected_owner_session_id="stale-native",
            )
        resumed = queue.resume(
            task.id, workspace.worker_id, adopt_owner_session_id=successor,
            reuse_session=True, expected_generation=current.generation,
            expected_owner_session_id=previous,
        )
        assert resumed.status == Status.STARTED and resumed.generation == current.generation + 1
        retained = queue.task_relationship(task.id)
        assert retained["request"] == original["request"]
        assert retained["receipt"] == original["receipt"]
        assert retained["continuation"] == {
            "generation": resumed.generation, "owner_session_id": successor,
        }
        with queue._connect() as conn:
            require_relationship(conn, task.id)
            with pytest.raises(sqlite3.IntegrityError, match="unconfirmed"):
                conn.execute(
                    "UPDATE tasks SET owner_session_id=? WHERE id=?", (previous, task.id),
                )
        queue.suspend(task.id, workspace.worker_id, reason="wait", cooldown_seconds=None)
    assert len(owner.claims) == 1 and original["request"]["generation"] == claimed.generation


@pytest.mark.parametrize("pending", [False, True])
def test_protected_startup_retry_continues_same_claim_after_exact_retirement(
    assignment, monkeypatch, pending,
):
    from tests.test_pool_startup_policy import renew

    queue, owner, workspace, task = assignment
    monkeypatch.setattr(queue, "_now", lambda now=None: 100.0 if now is None else now)
    publish(queue, pool())
    reservation, _ = place(queue, task)
    facts = {
        "execution_machine": workspace.machine, "allocation_project": workspace.project,
        "worktree_path": workspace.path, "driver": "cli",
        "creation_nonce": workspace.creation_nonce,
    }
    if pending:
        owner.lose_reply = "bind"
        with pytest.raises(TaskError):
            queue.record_spawn_worktree(
                reservation.key, workspace.worktree, ownership="created", **facts,
            )
    else:
        queue.record_spawn_worktree(
            reservation.key, workspace.worktree, ownership="created", **facts,
        )
    original = copy.deepcopy(queue.task_relationship(task.id))
    for episode, (deadline, due) in enumerate(((700, 4300), (4900, 15700))):
        queued = queue.exhaust_pool_startup(
            reservation.key, generation=queue.get(task.id).generation,
            startup_revision=reservation.startup_revision, now=deadline,
        )
        assert queued.status == Status.QUEUED and queued.not_before == due
        assert not any(operation == "close" for operation, _ in owner.calls)
        assert place(queue, queued, now=due)[1] is False
        if episode == 0:
            with pytest.raises(TaskError, match="original caller"):
                queue.retire_spawn(reservation.key, exact_absence=True, now=deadline + 1)
        queue.request_spawn_release(
            reservation.key, finished_startup_revision=reservation.startup_revision,
            now=deadline + 1,
        )
        queue.retire_spawn(
            reservation.key, exact_absence=True, conclusion_state="complete",
            conclusion_detail="workspace retained for original task retry", now=deadline + 2,
        )
        renew(queue, due)
        queue._relationship_authority = "central"
        before = queue.get(task.id), queue.list_reservations(task_id=task.id)
        with pytest.raises(TaskError, match="previous task relationship"):
            place(queue, queued, now=due)
        assert (queue.get(task.id), queue.list_reservations(task_id=task.id)) == before
        queue._relationship_authority = "local"
        replacement, reserved = place(queue, queued, now=due)
        assert reserved and replacement.key != reservation.key
        assert replacement.task_relationship_event_id == original["request"]["event_id"]
        assert replacement.creation_nonce == workspace.creation_nonce
        retained = queue.task_relationship(task.id)
        assert retained["request"] == original["request"]
        assert retained["continuation"] == {
            "generation": queue.get(task.id).generation, "reservation_key": replacement.key,
        }
        with pytest.raises(TaskError, match="incarnation cannot change"):
            queue.record_spawn_worktree(
                replacement.key, workspace.worktree, ownership="reused", now=due,
                **{**facts, "creation_nonce": "replacement"},
            )
        queue.record_spawn_worktree(
            replacement.key, workspace.worktree, ownership="reused", now=due, **facts,
        )
        reservation = queue.record_spawn(
            replacement.key, session_handle=f"replacement-{episode}", worktree=workspace.worktree,
            startup_complete=True, startup_revision=replacement.startup_revision, now=due + 1,
        )
    queue.start(
        task.id, workspace.worker_id, owner_session_id="replacement-1",
        expected_generation=queue.get(task.id).generation, now=15702,
    )
    assert len(owner.claims) == 1
    queue.complete(task.id, workspace.worker_id, result={"answer": "proposed"}, now=15703)
    queue.confirm(task.id, now=15704)
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

"""Current-assignment protection survives older writers without starving other work."""

from __future__ import annotations

import copy
import json
import sqlite3
from dataclasses import replace
from typing import Any

import pytest

from agent_dispatch.assignment_workspace import AssignmentWorkspace
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_common import Task
from agent_dispatch.queue_records import Status, TaskError
from agent_dispatch.queue_task_relationships import require_relationship
from agent_dispatch.task_obligations import read_snapshot
from agent_dispatch.task_relationships import AssignmentSubject
from tests.test_assignment_relationships import assignment as assignment
from tests.test_assignment_relationships import GroundOwner
from tests.test_assignment_relationships import independent_readiness as independent_readiness
from tests.test_pool_placement import current_task, place, pool, publish

Assignment = tuple[TaskQueue, GroundOwner, AssignmentWorkspace, Task]


def claim(queue: TaskQueue, workspace: AssignmentWorkspace, task: Task) -> Task:
    held = queue.claim_one(workspace.worker_id, task_id=task.id, assignment_workspace=workspace)
    assert isinstance(held, Task)
    return held


def relationship(queue: TaskQueue, task_id: str) -> dict[str, Any]:
    retained = queue.task_relationship(task_id)
    assert retained is not None
    return retained


def test_qualified_atomic_create_claim_refuses_without_creating_work(assignment: Assignment) -> None:
    queue, _, workspace, _ = assignment
    before = len(queue.list())
    with pytest.raises(TaskError, match="atomic create-and-claim"):
        queue.create("atomic", repo="example", claim_as=workspace.worker_id)
    assert len(queue.list()) == before


@pytest.mark.parametrize("destination", [Status.SUBMITTED, Status.COMPLETED])
def test_unprotected_assignment_cannot_bypass_first_result_admission(assignment: Assignment, destination: str) -> None:
    queue, owner, workspace, task = assignment
    owner.lose_reply = "bind"
    with pytest.raises(TaskError, match="unresolved"):
        claim(queue, workspace, task)
    with queue._connect() as conn, pytest.raises(
        sqlite3.IntegrityError, match="unconfirmed|readiness",
    ):
        conn.execute(
            "UPDATE tasks SET status=?, owner=NULL WHERE id=?", (destination, task.id),
        )
    assert current_task(queue, task.id).status == Status.CLAIMED
    assert relationship(queue, task.id)["state"] == "pending"


@pytest.mark.parametrize("settled", [False, True])
def test_unpinned_legacy_claim_skips_governed_work_without_starving_other_tasks(
    assignment: Assignment, settled: bool
) -> None:
    queue, _, workspace, task = assignment
    claim(queue, workspace, task)
    queue.yield_task(task.id, workspace.worker_id)
    if settled:
        queue.reconcile_task_relationship(task.id)
    unrelated = queue.create("other", repo="example")
    legacy = TaskQueue(queue.db_path)
    taken = legacy.claim_one("ordinary")
    assert isinstance(taken, Task) and isinstance(unrelated, Task)
    assert taken.id == unrelated.id
    with pytest.raises(TaskError, match="contract"):
        legacy.claim_one(workspace.worker_id, task_id=task.id)


def test_previous_bound_assignment_cannot_protect_another_pool_workspace_after_rollback(
    assignment: Assignment,
) -> None:
    queue, _, workspace, task = assignment
    claim(queue, workspace, task)
    queue.yield_task(task.id, workspace.worker_id)
    publish(queue, pool())
    before = current_task(queue, task.id)
    with pytest.raises(TaskError, match="previous task relationship"):
        place(queue, task)
    assert current_task(queue, task.id) == before
    assert queue.list_reservations(task_id=task.id) == []
    assert queue.task_pool_assignment(task.id) is None
    queue.reconcile_task_relationship(task.id)
    reservation, _ = place(queue, task)
    with queue._connect() as conn:
        value = relationship(queue, task.id)
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
        # A rolled-back writer can install its old trigger alongside the current guard.
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
    assert current_task(queue, task.id).status == Status.CLAIMED
    assert relationship(queue, task.id)["state"] == "bound"


def test_host_case_alias_start_and_exact_unassignment_settle_original_relationship(assignment: Assignment) -> None:
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
        expected_generation=current_task(queue, task.id).generation,
    )
    queue.request_spawn_release(reservation.key, finished_startup_revision=1)
    queue.retire_spawn(reservation.key, exact_absence=True, conclusion_state="skipped")
    queue.yield_task(task.id, worker)
    with queue._connect() as conn:
        conn.execute(
            "UPDATE task_pool_assignments SET assignment_released=1 WHERE task_id=?", (task.id,)
        )
    snapshot = read_snapshot(queue, task.id)
    assert snapshot is not None
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


def test_suspended_successor_keeps_original_claim_and_fences_old_native(assignment: Assignment) -> None:
    queue, owner, workspace, task = assignment
    claimed = claim(queue, workspace, task)
    queue.start(task.id, workspace.worker_id, owner_session_id="original-native")
    queue.suspend(task.id, workspace.worker_id, reason="handoff", cooldown_seconds=None)
    original = copy.deepcopy(relationship(queue, task.id))
    for previous, successor in (
        ("original-native", "successor-native"),
        ("successor-native", "second-successor"),
    ):
        current = current_task(queue, task.id)
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
        retained = relationship(queue, task.id)
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
    assignment: Assignment, monkeypatch: pytest.MonkeyPatch, pending: bool, independent_readiness: None,
) -> None:
    from tests.test_pool_startup_policy import renew

    queue, owner, workspace, task = assignment
    monkeypatch.setattr(queue, "_now", lambda now=None: 100.0 if now is None else now)
    publish(queue, pool())
    reservation, _ = place(queue, task)
    def prepare(key: str, ownership: str, nonce: str, *, now: float | None = None) -> None:
        queue.record_spawn_worktree(
            key, workspace.worktree, ownership=ownership, now=now,
            execution_machine=workspace.machine, allocation_project=workspace.project,
            worktree_path=workspace.path, driver="cli", creation_nonce=nonce,
        )
    if pending:
        owner.lose_reply = "bind"
        with pytest.raises(TaskError):
            prepare(reservation.key, "created", workspace.creation_nonce)
    else:
        prepare(reservation.key, "created", workspace.creation_nonce)
    original = copy.deepcopy(relationship(queue, task.id))
    for episode, (deadline, due) in enumerate(((700, 4300), (4900, 15700))):
        queued = queue.exhaust_pool_startup(
            reservation.key, generation=current_task(queue, task.id).generation,
            startup_revision=reservation.startup_revision, now=deadline,
        )
        assert queued.status == Status.CLAIMED and queued.not_before == due
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
        queued = current_task(queue, task.id)
        assert queued.status == Status.QUEUED and queued.not_before == due
        renew(queue, due)
        queue._relationship_authority = "central"
        before = current_task(queue, task.id), queue.list_reservations(task_id=task.id)
        with pytest.raises(TaskError, match="previous task relationship"):
            place(queue, queued, now=due)
        assert (current_task(queue, task.id), queue.list_reservations(task_id=task.id)) == before
        queue._relationship_authority = "local"
        replacement, reserved = place(queue, queued, now=due)
        assert reserved and replacement.key != reservation.key
        assert replacement.task_relationship_event_id == original["request"]["event_id"]
        assert replacement.creation_nonce == workspace.creation_nonce
        retained = relationship(queue, task.id)
        assert retained["request"] == original["request"]
        assert retained["continuation"] == {
            "generation": current_task(queue, task.id).generation, "reservation_key": replacement.key,
        }
        with pytest.raises(TaskError, match="incarnation cannot change"):
            prepare(replacement.key, "reused", "replacement", now=due)
        prepare(replacement.key, "reused", workspace.creation_nonce, now=due)
        reservation = queue.record_spawn(
            replacement.key, session_handle=f"replacement-{episode}", worktree=workspace.worktree,
            startup_complete=True, startup_revision=replacement.startup_revision, now=due + 1,
        )
    queue.start(
        task.id, workspace.worker_id, owner_session_id="replacement-1",
        expected_generation=current_task(queue, task.id).generation, now=15702,
    )
    assert len(owner.claims) == 1
    queue.complete(task.id, workspace.worker_id, result={"answer": "proposed"}, now=15703)
    queue.confirm(task.id, now=15704)
    assert queue.reconcile_task_relationship(task.id)["state"] == "closed"


def test_submission_keeps_exact_relationship_after_execution_capacity_retires(
    assignment: Assignment, independent_readiness: None,
) -> None:
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
        expected_generation=current_task(queue, task.id).generation,
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


@pytest.mark.parametrize("state,closed,legacy_guard", [
    (Status.SUBMITTED, False, False), (Status.COMPLETED, False, False),
    (Status.COMPLETED, True, False), (Status.SUBMITTED, False, True),
])
def test_retired_agent_detachment_does_not_readmit_execution_or_release_submitted_work(
    assignment: Assignment, state: str, closed: bool, legacy_guard: bool, independent_readiness: None,
) -> None:
    queue, owner, workspace, task = assignment
    publish(queue, pool())
    reservation, _ = place(queue, task)
    queue.record_spawn_worktree(
        reservation.key, workspace.worktree, ownership="created",
        execution_machine=workspace.machine, allocation_project=workspace.project,
        worktree_path=workspace.path, driver="cli", creation_nonce=workspace.creation_nonce,
    )
    queue.record_spawn(
        reservation.key, session_handle="native", worktree=workspace.worktree,
        startup_complete=True, startup_revision=reservation.startup_revision,
    )
    queue.start(
        task.id, workspace.worker_id, owner_session_id="native",
        expected_generation=current_task(queue, task.id).generation,
    )
    queue.complete(task.id, workspace.worker_id, result={"answer": "retained"})
    if state == Status.COMPLETED:
        queue.confirm(task.id)
    if closed:
        queue.reconcile_task_relationship(task.id)
    queue.request_spawn_release(reservation.key, disposition="settled")
    queue.retire_spawn(reservation.key, exact_absence=True)
    if legacy_guard:
        with queue._connect() as conn:
            conn.execute(
                "CREATE TRIGGER task_relationship_execution_guard_v3"
                " BEFORE UPDATE OF status, generation, owner, owner_session_id ON tasks"
                " WHEN NEW.status IN ('started','submitted','completed')"
                " BEGIN SELECT RAISE(ABORT, 'legacy relationship guard'); END"
            )
        queue = TaskQueue(
            queue.db_path, relationship_writer=owner, relationship_authority="local",
        )
        owner.queue = queue
    before = current_task(queue, task.id)
    original = copy.deepcopy(relationship(queue, task.id))
    snapshot = queue.task_diagnostics(task.id)
    assert snapshot["actions"]["detach_agent"]["allowed"]
    request = {
        "operation": "detach_agent", "expected_revision": snapshot["revision"],
        "request_id": "detach-retired-native", "actor": "operator",
        "reason": "qualified native retirement complete",
    }
    detached = queue.recover_task(task.id, **request)
    assert detached["task"]["status"] == state
    assert detached["task"]["generation"] == before.generation + 1
    assert detached["task"]["owner"] is None and detached["task"]["owner_session_id"] is None
    assert current_task(queue, task.id).result == before.result
    assert relationship(queue, task.id) == original
    assert len(owner.claims) == 1
    assert queue.recover_task(task.id, **request) == {**detached, "replayed": True}
    with queue._connect() as conn, pytest.raises(sqlite3.IntegrityError, match="unconfirmed"):
        conn.execute(
            "UPDATE tasks SET status='started', owner=?, owner_session_id='native' WHERE id=?",
            (workspace.worker_id, task.id),
        )
    if state == Status.SUBMITTED:
        obligation = read_snapshot(queue, task.id)
        assert obligation is not None and obligation["release_outcome"] is None
        assert queue.reconcile_task_relationship(task.id)["state"] == "bound"
        assert next(iter(owner.claims.values()))["phase"] == "bound"
        queue.confirm(task.id)
    assert queue.reconcile_task_relationship(task.id)["state"] == "closed"


@pytest.mark.parametrize("suspended", [False, True])
def test_nonterminal_recovery_cannot_orphan_the_logical_pool_assignment(assignment: Assignment, suspended: bool) -> None:
    queue, owner, workspace, task = assignment
    publish(queue, pool())
    reservation, _ = place(queue, task)
    queue.record_spawn_worktree(
        reservation.key, workspace.worktree, ownership="created",
        execution_machine=workspace.machine, allocation_project=workspace.project,
        worktree_path=workspace.path, driver="cli", creation_nonce=workspace.creation_nonce,
    )
    queue.record_spawn(
        reservation.key, session_handle="native", worktree=workspace.worktree,
        startup_complete=True, startup_revision=reservation.startup_revision,
    )
    queue.start(
        task.id, workspace.worker_id, owner_session_id="native",
        expected_generation=current_task(queue, task.id).generation,
    )
    if suspended:
        queue.suspend(task.id, workspace.worker_id, reason="external", cooldown_seconds=None)
    queue.request_spawn_release(reservation.key, disposition="settled")
    queue.retire_spawn(reservation.key, exact_absence=True)
    before = current_task(queue, task.id), copy.deepcopy(relationship(queue, task.id))
    placement = queue.task_pool_assignment(task.id)
    assert placement is not None
    snapshot = queue.task_diagnostics(task.id)
    assert not snapshot["actions"]["detach_agent"]["allowed"]
    for operation in ("detach_agent", "reset_queued", "reset_proposed"):
        with pytest.raises(TaskError, match="logical pool assignment"):
            queue.recover_task(
                task.id, operation=operation, expected_revision=snapshot["revision"],
                request_id=operation, actor="operator", reason="qualified native retirement",
            )
    assert (current_task(queue, task.id), relationship(queue, task.id)) == before
    assert queue.task_pool_assignment(task.id) == placement
    assert not any(operation == "close" for operation, _ in owner.calls)


@pytest.mark.parametrize("pending", [False, True])
def test_exhausted_queued_pool_recovery_preserves_original_retry_binding(
    assignment: Assignment, monkeypatch: pytest.MonkeyPatch, pending: bool,
) -> None:
    queue, owner, workspace, task = assignment
    monkeypatch.setattr(queue, "_now", lambda now=None: 100.0 if now is None else now)
    publish(queue, pool())
    reservation, _ = place(queue, task)
    if pending:
        owner.lose_reply = "bind"
    try:
        queue.record_spawn_worktree(
            reservation.key, workspace.worktree, ownership="created",
            execution_machine=workspace.machine, allocation_project=workspace.project,
            worktree_path=workspace.path, driver="cli", creation_nonce=workspace.creation_nonce,
        )
    except TaskError:
        assert pending
    assert relationship(queue, task.id)["state"] == ("pending" if pending else "bound")
    queued = queue.exhaust_pool_startup(
        reservation.key, generation=current_task(queue, task.id).generation,
        startup_revision=reservation.startup_revision, now=700,
    )
    assert queued.status == Status.CLAIMED
    queue.request_spawn_release(
        reservation.key, finished_startup_revision=reservation.startup_revision, now=701,
    )
    queue.retire_spawn(
        reservation.key, exact_absence=True, conclusion_state="complete",
        conclusion_detail="retained for original startup retry", now=702,
    )
    queued = current_task(queue, task.id)
    assert queued.status == Status.QUEUED
    before = (
        current_task(queue, task.id), copy.deepcopy(relationship(queue, task.id)),
        queue.task_pool_assignment(task.id), queue.list_reservations(task_id=task.id),
    )
    snapshot = queue.task_diagnostics(task.id)
    for operation in ("reset_proposed", "reset_queued"):
        assert not snapshot["actions"][operation]["allowed"]
        with pytest.raises(TaskError, match="logical pool assignment"):
            queue.recover_task(
                task.id, operation=operation, expected_revision=snapshot["revision"],
                request_id=operation, actor="operator", reason="retirement is not unassignment",
            )
    with pytest.raises(TaskError, match="logical pool assignment"):
        queue.reset(task.id)
    assert (
        current_task(queue, task.id), relationship(queue, task.id),
        queue.task_pool_assignment(task.id), queue.list_reservations(task_id=task.id),
    ) == before
    from tests.test_pool_startup_policy import renew

    renew(queue, queued.not_before)
    replacement, reserved = place(queue, queued, now=queued.not_before)
    assert reserved and replacement.task_relationship_event_id == before[1]["request"]["event_id"]
    assert len(owner.claims) == 1


@pytest.mark.parametrize("pending", [False, True])
@pytest.mark.parametrize("operation", ["reset_proposed", "reset_queued", "legacy_reset"])
def test_direct_claim_reset_requires_original_relationship_settlement(
    assignment: Assignment, pending: bool, operation: str,
) -> None:
    queue, owner, workspace, task = assignment
    if pending:
        owner.lose_reply = "bind"
        with pytest.raises(TaskError, match="unresolved"):
            claim(queue, workspace, task)
    else:
        claim(queue, workspace, task)
    before = current_task(queue, task.id), copy.deepcopy(relationship(queue, task.id))
    snapshot = queue.task_diagnostics(task.id)
    assert not snapshot["actions"]["reset_proposed"]["allowed"]
    assert not snapshot["actions"]["reset_queued"]["allowed"]
    with pytest.raises(TaskError, match="relationship remains unsettled"):
        if operation == "legacy_reset":
            queue.reset(task.id)
        else:
            queue.recover_task(
                task.id, operation=operation, expected_revision=snapshot["revision"],
                request_id=operation, actor="operator", reason="do not erase unassignment proof",
            )
    assert (current_task(queue, task.id), relationship(queue, task.id)) == before
    queue.yield_task(task.id, workspace.worker_id)
    unassigned = queue.task_diagnostics(task.id)
    obligation = read_snapshot(queue, task.id)
    assert obligation is not None and obligation["release_outcome"] == "unassigned_queued"
    queue.reconcile_task_relationship(task.id)
    assert relationship(queue, task.id)["state"] == "closed"
    with pytest.raises(TaskError, match="retained execution changed"):
        queue.recover_task(
            task.id, operation="reset_proposed", expected_revision=unassigned["revision"],
            request_id="stale-relationship", actor="operator", reason="refresh original claim",
        )
    assert queue.reset(task.id).status == Status.PROPOSED

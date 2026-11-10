"""Persistent standing lanes and rollback fences use the real SQLite queue."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import sqlite3
from threading import Barrier

import pytest

from agent_dispatch.assignment_workspace import AssignmentWorkspace
from agent_dispatch.queue import TaskError, TaskQueue
from agent_dispatch.queue_standing_charters import StandingCharterDeclaration
from tests.test_assignment_relationships import GroundOwner


@pytest.fixture
def queue(tmp_path):
    return TaskQueue(tmp_path / "queue.sqlite3")


@pytest.fixture
def declaration():
    return StandingCharterDeclaration(
        namespace="owner", charter_key="dispatcher", lane_key="primary",
        effort_ref="efforts/dispatcher", materialization_ref="roles/dispatcher",
        source_revision="source-1", expected_materialization_revision="effective-1",
    )


@pytest.fixture
def standing_assignment(tmp_path, declaration):
    owner = GroundOwner()
    queue = TaskQueue(
        tmp_path / "queue.sqlite3", relationship_writer=owner, relationship_authority="local",
    )
    owner.queue = queue
    workspace = AssignmentWorkspace(
        "host", "example", "workspace", str(tmp_path / "checkout"), "original",
    )
    return queue, owner, workspace, enroll(queue, declaration)


def enroll(queue, declaration):
    return queue.reconcile_standing_charter("Dispatcher", declaration, repo="owner/repo")


def test_reconcile_race_restart_and_independent_lanes(queue, declaration):
    with ThreadPoolExecutor(max_workers=4) as executor:
        tasks = list(executor.map(lambda _: enroll(queue, declaration), range(8)))
    assert len({task.id for task in tasks}) == 1
    first = tasks[0]
    restarted = TaskQueue(queue.db_path)
    assert enroll(restarted, declaration) == first
    second = enroll(restarted, replace(declaration, lane_key="secondary"))
    assert first.id != second.id
    with restarted._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM task_events WHERE task_id=?", (first.id,),
        ).fetchone()[0] == 1


def test_revision_changes_preserve_identity_and_acceptance(queue, declaration):
    task = enroll(queue, declaration)
    changed = replace(declaration, source_revision="source-2")
    with pytest.raises(TaskError, match="revision changed"):
        enroll(queue, changed)
    updated = queue.reconcile_standing_charter(
        "Renamed", changed, repo="owner/repo", expected_revision=task.standing_revision,
    )
    assert updated.id == task.id
    assert updated.standing_revision == task.standing_revision + 1
    assert updated.standing_charter["accepted_materialization"] is None
    with pytest.raises(TaskError, match="revision changed"):
        queue.retire_standing_charter(task.id, expected_revision=task.standing_revision)


def test_retirement_tombstone_requires_explicit_reenrollment(queue, declaration):
    task = enroll(queue, declaration)
    retiring = queue.retire_standing_charter(task.id, expected_revision=task.standing_revision)
    with pytest.raises(TaskError, match="explicit re-enrollment"):
        enroll(queue, declaration)
    with pytest.raises(TaskError, match="settled retirement"):
        queue.reenroll_standing_charter(task.id, expected_revision=retiring.standing_revision)
    retired = queue.settle_standing_retirement(task.id, expected_revision=retiring.standing_revision)
    with pytest.raises(TaskError, match="explicit re-enrollment"):
        enroll(queue, declaration)
    reopened = queue.reenroll_standing_charter(task.id, expected_revision=retired.standing_revision)
    assert reopened.id == task.id
    assert reopened.standing_charter["declaration_generation"] == 2
    assert reopened.status == "queued"
    with queue._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM task_events WHERE task_id=?", (task.id,),
        ).fetchone()[0] == 4


def test_legacy_client_finite_work_continues_beside_standing_task(queue, declaration):
    standing = enroll(queue, declaration)
    finite = queue.create("Unlinked finite work", repo="owner/repo")
    claimed = queue.claim_one("worker", repo="owner/repo")
    assert claimed.id == finite.id
    queue.start(finite.id, "worker")
    assert queue.complete(finite.id, "worker", result={"done": True}).status == "completed"
    with pytest.raises(TaskError, match="explicit attachment admission"):
        queue.claim_one("worker", task_id=standing.id)
    with pytest.raises(TaskError, match="cannot complete"):
        queue.complete(standing.id, "worker")
    with pytest.raises(TaskError, match="fenced lifecycle"):
        queue.abandon(standing.id, permitted=True)


def test_linked_finite_children_finish_independently_across_parent_retirement(
    queue, declaration,
):
    standing = enroll(queue, declaration)
    verified = queue.create(
        "Verified child", repo="owner/repo", standing_parent_id=standing.id,
        require_verification=True, exclusive_key="child-work",
    )
    pending = queue.create(
        "Pending child", repo="owner/repo", standing_parent_id=standing.id, dedup_key="child-2",
    )
    assert (verified.kind, verified.standing_parent_id, verified.exclusive_key) == (
        "finite", standing.id, "child-work",
    )
    assert {task.id for task in queue.list(standing_parent_id=standing.id)} == {
        verified.id, pending.id,
    }
    queue.claim_one("worker", task_id=verified.id)
    queue.start(verified.id, "worker")
    assert queue.complete(verified.id, "worker").status == "submitted"
    assert queue.confirm(verified.id, actor="evaluator").status == "completed"
    assert queue.get(standing.id) == standing

    queue.claim_one("worker", task_id=pending.id)
    queue.start(pending.id, "worker")
    retiring = queue.retire_standing_charter(standing.id, expected_revision=standing.standing_revision)
    with pytest.raises(TaskError, match="admits no new finite children"):
        queue.create("Late child", repo="owner/repo", standing_parent_id=standing.id)
    retried = queue.create(
        "Pending child", repo="owner/repo", standing_parent_id=standing.id, dedup_key="child-2",
    )
    assert retried.id == pending.id and retried.status == "started"
    retired = queue.settle_standing_retirement(
        standing.id, expected_revision=retiring.standing_revision,
    )
    assert queue.complete(pending.id, "worker").status == "completed"
    assert TaskQueue(queue.db_path).get(standing.id) == retired
    assert queue.get(verified.id).standing_parent_id == standing.id


def test_finite_child_link_requires_same_lane_parent_and_is_immutable(queue, declaration):
    standing = enroll(queue, declaration)
    sibling = enroll(queue, replace(declaration, lane_key="secondary"))
    foreign = queue.reconcile_standing_charter("Foreign", declaration, repo="owner/other")
    finite = queue.create("Finite", repo="owner/repo")
    for parent in ("missing", finite.id, foreign.id):
        with pytest.raises(TaskError, match="same repo lane"):
            queue.create("Child", repo="owner/repo", standing_parent_id=parent)
    with pytest.raises(TaskError, match="bounded task id"):
        queue.create("Child", repo="owner/repo", standing_parent_id=f" {standing.id}")
    child = queue.create(
        "Child", repo="owner/repo", standing_parent_id=standing.id, dedup_key="subject",
    )
    with pytest.raises(TaskError, match="different standing parent"):
        queue.create("Child", repo="owner/repo", standing_parent_id=sibling.id, dedup_key="subject")
    assert queue.create("Child", repo="owner/repo", dedup_key="subject") == child
    with sqlite3.connect(queue.db_path) as old:
        for statement, values in (
            ("UPDATE tasks SET standing_parent_id=NULL WHERE id=?", (child.id,)),
            ("UPDATE tasks SET standing_parent_id=? WHERE id=?", (standing.id, finite.id)),
            (
                "INSERT INTO tasks(id, title, repo, standing_parent_id) VALUES ('forged', 'x', ?, ?)",
                (finite.repo, finite.id),
            ),
            (
                "INSERT INTO tasks(id, title, repo, standing_parent_id) VALUES ('forged', 'x', ?, ?)",
                (finite.repo, foreign.id),
            ),
        ):
            with pytest.raises(sqlite3.IntegrityError, match="standing parent"):
                old.execute(statement, values)
    assert queue.get(child.id) == child
    assert queue.get(finite.id).standing_parent_id is None


@pytest.mark.parametrize("statement", [
    "UPDATE tasks SET status='completed' WHERE id=?",
    "UPDATE tasks SET status='submitted' WHERE id=?",
    "UPDATE tasks SET status='abandoned' WHERE id=?",
    "UPDATE tasks SET status='dead_letter' WHERE id=?",
    "UPDATE tasks SET status='claimed', owner='old-worker', generation=generation+1 WHERE id=?",
    "UPDATE tasks SET kind='finite', standing_charter=NULL, standing_revision=0 WHERE id=?",
    "UPDATE tasks SET prompt='legacy overwrite' WHERE id=?",
    "DELETE FROM tasks WHERE id=?",
    "INSERT OR REPLACE INTO tasks(id, title) VALUES (?, 'legacy replacement')",
    "DELETE FROM task_events WHERE task_id=?",
    "UPDATE task_events SET standing_change=NULL WHERE task_id=?",
    "INSERT OR REPLACE INTO task_events(id, task_id, ts, to_status)"
    " SELECT id, task_id, ts, to_status FROM task_events WHERE task_id=?",
    "INSERT OR REPLACE INTO task_events(task_id, ts, to_status, standing_change)"
    " SELECT task_id, ts, to_status, standing_change FROM task_events WHERE task_id=?",
])
def test_old_provider_sql_cannot_conclude_reclaim_or_erase_standing(queue, declaration, statement):
    task = enroll(queue, declaration)
    with sqlite3.connect(queue.db_path) as old:
        with pytest.raises(sqlite3.IntegrityError, match="standing"):
            old.execute(statement, (task.id,))
        old.execute("INSERT INTO tasks(id, title) VALUES ('old-finite', 'Old provider')")
        old.execute("UPDATE tasks SET status='completed' WHERE id='old-finite'")
    assert queue.get(task.id) == task
    assert queue.get("old-finite").status == "completed"


def test_retirement_preserves_independent_hold(queue, declaration):
    task = enroll(queue, declaration)
    queue.set_hold(task.id, reason="operator hold", actor="operator")
    retiring = queue.retire_standing_charter(task.id, expected_revision=task.standing_revision)
    with pytest.raises(TaskError, match="held"):
        queue.settle_standing_retirement(task.id, expected_revision=retiring.standing_revision)
    assert queue.get(task.id).hold_reason == "operator hold"


def test_legacy_spawn_cannot_launch_standing_or_retarget_finite_reservation(queue, declaration):
    task = enroll(queue, declaration)
    with pytest.raises(TaskError, match="explicit attachment admission"):
        queue.reserve_spawn(task.id, reserved_by="old-spawner")
    finite = queue.create("Finite", repo="owner/repo")
    reservation, reserved = queue.reserve_spawn(finite.id, reserved_by="old-spawner")
    assert reserved
    with sqlite3.connect(queue.db_path) as old:
        with pytest.raises(sqlite3.IntegrityError, match="explicit attachment admission"):
            old.execute(
                "INSERT INTO spawn_reservations(key, task_id, attempt, state, reserved_at, updated_at)"
                " VALUES ('legacy-standing', ?, 1, 'reserving', 1, 1)",
                (task.id,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="standing reservation identity"):
            old.execute(
                "UPDATE spawn_reservations SET task_id=? WHERE key=?",
                (task.id, reservation.key),
            )
        assert old.execute(
            "SELECT task_id FROM spawn_reservations WHERE key=?", (reservation.key,),
        ).fetchone()[0] == finite.id
        assert old.execute(
            "SELECT COUNT(*) FROM spawn_reservations WHERE task_id=?", (task.id,),
        ).fetchone()[0] == 0


def test_failed_reconciliation_leaves_no_declaration(queue, declaration):
    with pytest.raises(TaskError, match="bounded nonempty"):
        replace(declaration, lane_key="")
    with pytest.raises(TaskError, match="repository lane"):
        queue.reconcile_standing_charter("Dispatcher", declaration, repo=None)
    with queue._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 0


@pytest.mark.parametrize("lost_reply", [None, "reserve", "bind"])
def test_standing_reservation_survives_restart_without_accepting_coverage(
    standing_assignment, lost_reply,
):
    queue, owner, workspace, task = standing_assignment
    owner.lose_reply = lost_reply
    if lost_reply:
        with pytest.raises(TaskError, match="unresolved"):
            queue.reserve_standing_charter(
                task.id, workspace, expected_revision=task.standing_revision,
            )
    else:
        queue.reserve_standing_charter(
            task.id, workspace, expected_revision=task.standing_revision,
        )
    original = queue.task_relationship(task.id)["request"]
    restarted = TaskQueue(
        queue.db_path, relationship_writer=owner, relationship_authority="local",
    )
    owner.queue = restarted
    reserved = restarted.reserve_standing_charter(
        task.id, workspace, expected_revision=task.standing_revision,
    )
    assert reserved.status == "claimed" and reserved.owner == workspace.worker_id
    assert reserved.generation == task.generation + 1 and reserved.attempts == 1
    assert reserved.standing_revision == task.standing_revision + 1
    assert reserved.owner_session_id is None and reserved.lease_expires_at is None
    assert reserved.standing_charter["accepted_materialization"] is None
    relationship = restarted.task_relationship(task.id)
    assert relationship["state"] == "bound" and relationship["request"] == original
    assert len(owner.claims) == 1
    calls = len(owner.calls)
    assert restarted.reserve_standing_charter(
        task.id, workspace, expected_revision=task.standing_revision,
    ) == reserved
    assert len(owner.calls) == calls
    for supplied_workspace in (None, workspace):
        with pytest.raises(TaskError, match="explicit attachment admission"):
            restarted.claim_one(
                workspace.worker_id, task_id=task.id, assignment_workspace=supplied_workspace,
            )
    with pytest.raises(TaskError, match="fenced lifecycle"):
        restarted.start(task.id, workspace.worker_id, owner_session_id="unproven-session")
    assert restarted.get(task.id) == reserved


def test_standing_reservation_race_retains_exactly_one_workspace(standing_assignment):
    queue, owner, workspace, task = standing_assignment
    barrier = Barrier(2)
    candidates = [workspace, replace(workspace, worktree="other", creation_nonce="other")]

    def reserve(candidate):
        barrier.wait()
        try:
            return queue.reserve_standing_charter(
                task.id, candidate, expected_revision=task.standing_revision,
            )
        except TaskError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(reserve, candidates))
    accepted = [result for result in results if result is not None]
    assert len(accepted) == 1 and len(owner.claims) == 1
    reserved = queue.get(task.id)
    assert reserved.owner == accepted[0].owner and reserved.attempts == 1
    assert queue.task_relationship(task.id)["request"]["generation"] == reserved.generation


@pytest.mark.parametrize("field", ["machine", "project", "worktree", "path", "creation_nonce"])
def test_standing_reservation_cannot_replace_original_workspace(standing_assignment, field):
    queue, owner, workspace, task = standing_assignment
    reserved = queue.reserve_standing_charter(
        task.id, workspace, expected_revision=task.standing_revision,
    )
    calls = len(owner.calls)
    with pytest.raises(TaskError, match="conflicts with its retained assignment"):
        queue.reserve_standing_charter(
            task.id, replace(workspace, **{field: "replacement"}),
            expected_revision=task.standing_revision,
        )
    with pytest.raises(TaskError, match="conflicts with its retained assignment"):
        queue.reserve_standing_charter(
            task.id, workspace, expected_revision=reserved.standing_revision,
        )
    assert queue.get(task.id) == reserved and len(owner.calls) == calls


@pytest.mark.parametrize("change", ["hold", "retire", "reconcile"])
def test_standing_reservation_rechecks_changes_during_native_write(
    standing_assignment, declaration, change,
):
    queue, owner, workspace, task = standing_assignment

    def before_reply(operation, result):
        if operation != "bind":
            return
        current = queue.get(task.id)
        if change == "hold":
            queue.set_hold(task.id, reason="hold during write", actor="operator")
        elif change == "retire":
            queue.retire_standing_charter(task.id, expected_revision=current.standing_revision)
        else:
            queue.reconcile_standing_charter(
                task.title, replace(declaration, source_revision="changed"), repo=task.repo,
                expected_revision=current.standing_revision,
            )

    owner.before_reply = before_reply
    with pytest.raises(TaskError, match="held|revision changed"):
        queue.reserve_standing_charter(
            task.id, workspace, expected_revision=task.standing_revision,
        )
    current = queue.get(task.id)
    assert current.owner == workspace.worker_id and current.owner_session_id is None
    assert current.standing_charter["accepted_materialization"] is None
    assert queue.task_relationship(task.id)["state"] == "bound"
    assert len(owner.claims) == 1


def test_standing_reservation_without_qualified_writer_rolls_back(queue, declaration, tmp_path):
    task = enroll(queue, declaration)
    workspace = AssignmentWorkspace("host", "example", "workspace", str(tmp_path), "original")
    events = queue.events(task.id)
    with pytest.raises(TaskError, match="qualified relationship writer"):
        queue.reserve_standing_charter(task.id, workspace, expected_revision=task.standing_revision)
    assert queue.get(task.id) == task and queue.events(task.id) == events


def test_held_standing_reservation_has_no_native_effects(standing_assignment):
    queue, owner, workspace, task = standing_assignment
    queue.set_hold(task.id, reason="operator hold", actor="operator")
    before = queue.get(task.id)
    with pytest.raises(TaskError, match="held"):
        queue.reserve_standing_charter(
            task.id, workspace, expected_revision=task.standing_revision,
        )
    assert queue.get(task.id) == before and not owner.calls

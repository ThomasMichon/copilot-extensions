"""Task recovery keeps lifecycle, attachment history and execution custody distinct."""

import json

import pytest

from agent_dispatch.queue import Status, TaskError
from tests._helpers import RepoDefaultingQueue as TaskQueue


@pytest.fixture
def q(tmp_path):
    return TaskQueue(tmp_path / "tasks.db")


def recover(q, task_id, operation, *, request_id="request-1", **changes):
    return q.recover_task(
        task_id, operation=operation, reason="operator recovery",
        actor="operator", request_id=request_id,
        expected_revision=q.task_diagnostics(task_id)["revision"], **changes,
    )


@pytest.mark.parametrize("destination", ["proposed", "queued"])
def test_reset_preserves_progress_and_routing(q, destination):
    task = q.create("work", status=Status.QUEUED, target_worktree="workspace")
    q.claim_one("worker", worktree="workspace")
    q.record_progress(task.id, "worker", phase="working", summary="retained progress")
    before = q.get(task.id)
    result = recover(q, task.id, f"reset_{destination}")
    assert result["task"]["status"] == destination
    assert result["task"]["owner"] is None
    assert result["task"]["generation"] == before.generation + 1
    assert q.get(task.id).target_worktree == "workspace"
    assert q.progress_log(task.id)[0]["summary"] == "retained progress"


def test_exact_replay_does_not_repeat_or_undo_later_work(q, tmp_path):
    task = q.create("work")
    revision = q.task_diagnostics(task.id)["revision"]
    request = dict(
        operation="reset_proposed", reason="operator recovery", actor="operator",
        request_id="request-1", expected_revision=revision,
    )
    first = q.recover_task(task.id, **request)
    q.approve(task.id)
    reopened = TaskQueue(tmp_path / "tasks.db")
    assert reopened.recover_task(task.id, **request) == {**first, "replayed": True}
    assert q.get(task.id).status == Status.QUEUED
    with pytest.raises(TaskError, match="different request"):
        q.recover_task(task.id, **{**request, "operation": "reset_queued"})


def test_revision_fences_status_same_incarnation_and_new_attachment(q):
    task = q.create("work")
    revision = q.task_diagnostics(task.id)["revision"]
    q.detach(task.id)
    with pytest.raises(TaskError, match="changed"):
        q.recover_task(
            task.id, operation="reset_proposed", reason="recovery", actor="operator",
            request_id="request-1", expected_revision=revision,
        )


def test_agent_detach_does_not_unpin_or_reset_card(q):
    task = q.create("work", target_worktree="workspace")
    q.claim_one("worker", worktree="workspace")
    result = recover(q, task.id, "detach_agent")
    assert result["task"]["status"] == Status.QUEUED
    assert q.get(task.id).target_worktree == "workspace"
    assert q.get(task.id).owner is None


def test_live_or_unknown_native_binding_is_not_orphaned(q):
    task = q.create("work")
    q.claim_one("worker")
    q.start(task.id, "worker", owner_session_id="native-1")
    before = q.get(task.id)
    diagnostics = q.task_diagnostics(task.id)
    assert not diagnostics["actions"]["detach_agent"]["allowed"]
    with pytest.raises(TaskError, match="native"):
        recover(q, task.id, "detach_agent")
    assert q.get(task.id) == before
    assert q.attachment_history(task.id)[0].detached_at is None
    with pytest.raises(TaskError, match="native"):
        q.reset(task.id)


def test_legacy_attempted_wake_remains_unknown_after_reset_request(q):
    task = q.create("work")
    q.claim_one("worker")
    q.start(task.id, "worker", owner_session_id="native-1")
    q.submit_steer(task.id, fields={"answer": "continue"}, wake_requested=True, sender="operator")
    with q._connect() as conn:
        conn.execute("UPDATE wake_outbox SET attempts=1, status='failed' WHERE task_id=?", (task.id,))
        conn.execute("UPDATE tasks SET status='queued', owner=NULL WHERE id=?", (task.id,))
    snapshot = q.task_diagnostics(task.id)
    assert snapshot["wakes"][0]["outcome"] == "unknown"
    assert not snapshot["actions"]["reset_queued"]["allowed"]
    with pytest.raises(TaskError, match="wake"):
        recover(q, task.id, "reset_queued")
    with pytest.raises(TaskError, match="wake"):
        q.reset(task.id)


def test_diagnostics_are_redacted_bounded_and_do_not_mutate(q):
    task = q.create("secret prompt")
    q.claim_one("worker")
    q.start(task.id, "worker", owner_session_id="native-1")
    q.submit_steer(task.id, fields={"secret": "payload"}, wake_requested=True)
    with q._connect() as conn:
        conn.execute(
            "UPDATE wake_outbox SET delivery_token='private-delivery-token',"
            " execution_send=? WHERE task_id=?",
            (json.dumps({"delivery_token": "private-delivery-token"}), task.id),
        )
        before = conn.total_changes
    snapshot = q.task_diagnostics(task.id)
    rendered = json.dumps(snapshot)
    assert "secret prompt" not in rendered
    assert "payload" not in rendered
    assert "private-delivery-token" not in rendered
    assert snapshot["evidence"]["liveness"] == "unknown"
    assert before == 1
    assert q.task_diagnostics(task.id) == snapshot


def test_independent_hold_remains_authoritative(q):
    task = q.create("work")
    q.set_hold(task.id, reason="independent", actor="operator")
    with pytest.raises(TaskError, match="held"):
        recover(q, task.id, "reset_queued")
    assert q.get(task.id).hold_reason == "independent"


def test_truncated_history_refuses_instead_of_claiming_empty(q):
    task = q.create("work")
    with q._connect() as conn:
        conn.executemany(
            "INSERT INTO task_attachments (task_id,session_id,attached_at,detached_at)"
            " VALUES (?,?,?,?)",
            [(task.id, f"prior-{index}", index, index + 0.5) for index in range(101)],
        )
    snapshot = q.task_diagnostics(task.id)
    assert snapshot["evidence"]["truncated"]
    assert len(snapshot["attachments"]) == 100
    with pytest.raises(TaskError, match="bounded"):
        recover(q, task.id, "reset_queued")


def test_attachment_change_is_fenced_even_without_task_timestamp_change(q):
    task = q.create("work")
    revision = q.task_diagnostics(task.id)["revision"]
    with q._connect() as conn:
        conn.execute(
            "INSERT INTO task_attachments (task_id,session_id,attached_at)"
            " VALUES (?,?,?)", (task.id, "new-native", 10),
        )
    with pytest.raises(TaskError, match="changed"):
        q.recover_task(
            task.id, operation="reset_proposed", reason="recovery", actor="operator",
            request_id="request-1", expected_revision=revision,
        )

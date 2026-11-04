"""Proposal custody is not readiness, submission, evaluation or task release."""

from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from agent_dispatch.assignment_workspace import AssignmentWorkspace
from agent_dispatch.coordinator import create_app
from agent_dispatch.execution_effect_adapter import EffectRevocation, ExecutionScope
from agent_dispatch.queue import (
    ResultTooLargeError,
    ResultValidationError,
    Status,
    TaskError,
    TaskQueue,
)
from tests.test_assignment_relationships import GroundOwner, claim


@pytest.fixture
def submission(tmp_path):
    owner = GroundOwner()
    queue = TaskQueue(
        tmp_path / "queue.db", relationship_writer=owner, relationship_authority="local",
    )
    owner.queue = queue
    workspace = AssignmentWorkspace(
        "host-a", "example", "tree", str(tmp_path / "checkout"), "original",
    )
    task = queue.create("work", repo="example", require_verification=True, evaluator_ref="review")
    claim(queue, workspace, task)
    task = queue.start(task.id, workspace.worker_id, owner_session_id="native")
    arguments = {
        "request_id": "original-request",
        "expected_generation": task.generation,
        "expected_owner_session_id": task.owner_session_id,
        "workspace": workspace,
        "result": {"answer": [1, {"ok": True}]},
        "result_ref": "https://example.invalid/result",
    }
    return queue, owner, task, arguments


def test_preparation_retains_exact_proposal_across_restart_without_submission(submission):
    queue, owner, task, arguments = submission
    relationship = queue.task_relationship(task.id)
    value = queue.prepare_submission(task.id, task.owner, **arguments)
    assert queue.get(task.id) == task
    assert queue.task_relationship(task.id) == relationship
    assert value["request"]["result"] == arguments["result"]
    assert value["request"]["workspace"] == relationship["request"]["workspace"]
    assert value["request"]["assignment_event_id"] == relationship["request"]["event_id"]
    assert value["request"]["owner_session_id"] == "native"
    assert len(owner.claims) == 1 and len(owner.calls) == 2
    restarted = TaskQueue(queue.db_path)
    assert restarted.submission_preparation(task.id) == value
    assert restarted.prepare_submission(task.id, task.owner, **arguments) == value
    with queue._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM verification_requests").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM task_events WHERE task_id=? AND to_status='submitted'",
            (task.id,),
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM task_events WHERE submission_preparation IS NOT NULL",
        ).fetchone()[0] == 1


def test_blocker_retains_result_and_ownership_and_same_request_retry(submission):
    queue, _owner, task, arguments = submission
    original = queue.prepare_submission(task.id, task.owner, **arguments)
    value = queue.record_submission_blocker(
        task.id, request_id=arguments["request_id"], code="worktree-wip",
        message="Commit and land the current changes through their normal review.", now=20,
    )
    assert value["request"] == original["request"]
    assert value["blocker"]["code"] == "worktree-wip"
    assert queue.prepare_submission(task.id, task.owner, **arguments) == value
    assert queue.get(task.id) == task
    with pytest.raises(TaskError, match="request changed"):
        queue.record_submission_blocker(
            task.id, request_id="other", code="unknown", message="Not this proposal",
        )
    assert queue.submission_preparation(task.id) == value


def test_progress_does_not_change_the_submission_request(submission):
    queue, _owner, task, arguments = submission
    value = queue.prepare_submission(task.id, task.owner, **arguments)
    queue.record_progress(task.id, task.owner, phase="review", summary="Waiting for review")
    assert queue.prepare_submission(task.id, task.owner, **arguments) == value


def test_suspension_retains_the_original_preparation_and_native(submission):
    queue, _owner, task, arguments = submission
    value = queue.prepare_submission(task.id, task.owner, **arguments)
    suspended = queue.suspend(task.id, task.owner, reason="waiting")
    assert suspended.status == Status.SUSPENDED
    assert queue.prepare_submission(task.id, task.owner, **arguments) == value
    assert queue.get(task.id) == suspended


@pytest.mark.parametrize(
    "change",
    [
        {"result": {"answer": "different"}},
        {"result_ref": None},
        {"request_id": "new-request"},
        {"expected_generation": 999},
        {"expected_owner_session_id": "replacement"},
    ],
)
def test_changed_proposal_or_native_cannot_replace_the_original(submission, change):
    queue, _owner, task, arguments = submission
    original = queue.prepare_submission(task.id, task.owner, **arguments)
    with pytest.raises(TaskError, match=r"conflicts|incarnation"):
        queue.prepare_submission(task.id, task.owner, **(arguments | change))
    assert queue.submission_preparation(task.id) == original
    assert queue.get(task.id) == task


@pytest.mark.parametrize("field", ["creation_nonce", "path", "project"])
def test_foreign_workspace_cannot_prepare(submission, field):
    queue, _owner, task, arguments = submission
    arguments["workspace"] = replace(arguments["workspace"], **{field: "replacement"})
    with pytest.raises(TaskError, match="workspace assignment"):
        queue.prepare_submission(task.id, task.owner, **arguments)
    assert queue.submission_preparation(task.id) is None


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"result": "scalar"}, ResultValidationError),
        ({"result": {"answer": float("nan")}}, ResultValidationError),
        ({"result": {"answer": "x" * 65536}}, ResultTooLargeError),
        ({"request_id": ""}, TaskError),
        ({"expected_owner_session_id": None}, TaskError),
        ({"expected_generation": True}, TaskError),
        ({"result_ref": "x" * 4097}, TaskError),
    ],
)
def test_invalid_or_unbounded_proposal_leaves_no_preparation(submission, change, error):
    queue, _owner, task, arguments = submission
    with pytest.raises(error):
        queue.prepare_submission(task.id, task.owner, **(arguments | change))
    assert queue.submission_preparation(task.id) is None and queue.get(task.id) == task


@pytest.mark.parametrize("result", [None, [], {}])
def test_empty_structured_or_absent_result_is_retained(submission, result):
    queue, _owner, task, arguments = submission
    arguments["result"] = result
    value = queue.prepare_submission(task.id, task.owner, **arguments)
    assert value["request"]["result"] == result


def test_native_change_and_hold_refuse_blocker_recording_without_losing_proposal(submission):
    queue, _owner, task, arguments = submission
    value = queue.prepare_submission(task.id, task.owner, **arguments)
    queue.set_hold(task.id, actor="operator", reason="inspection")
    with pytest.raises(TaskError, match="held"):
        queue.record_submission_blocker(
            task.id, request_id=arguments["request_id"], code="wip", message="Changed",
        )
    assert queue.submission_preparation(task.id) == value
    queue.clear_hold(task.id, actor="operator")
    with queue._connect() as conn:
        conn.execute("UPDATE tasks SET owner_session_id='replacement' WHERE id=?", (task.id,))
    with pytest.raises(TaskError, match="incarnation"):
        queue.record_submission_blocker(
            task.id, request_id=arguments["request_id"], code="wip", message="Changed",
        )
    assert queue.submission_preparation(task.id) == value


def test_hold_before_preparation_retains_the_original_active_assignment(submission):
    queue, _owner, task, arguments = submission
    held = queue.set_hold(task.id, actor="operator", reason="inspection")
    with pytest.raises(TaskError, match="held"):
        queue.prepare_submission(task.id, task.owner, **arguments)
    assert queue.get(task.id) == held and queue.submission_preparation(task.id) is None


def test_execution_closure_refuses_preparation_and_late_blocker_write(submission):
    queue, _owner, task, arguments = submission
    value = queue.prepare_submission(task.id, task.owner, **arguments)
    queue.handoff_producer_scope(
        task.repo, "scheduled", producer_id="producer", expected_generation=0,
    )
    with queue._connect() as conn:
        conn.execute("UPDATE tasks SET source='scheduled' WHERE id=?", (task.id,))
    request = EffectRevocation(
        ExecutionScope(
            "installation", value["request"]["queue_identity"], task.repo, "registration",
        ),
        "closure", 1, "automatic", "manual",
    )
    queue.close_execution_admission(
        request, source="scheduled", expected_producer_generation=1,
    )
    with pytest.raises(TaskError, match="admission closed"):
        queue.prepare_submission(task.id, task.owner, **arguments)
    with pytest.raises(TaskError, match="admission closed"):
        queue.record_submission_blocker(
            task.id, request_id=arguments["request_id"], code="wip", message="Not ready",
        )
    assert queue.submission_preparation(task.id) == value


def test_legacy_assignment_without_a_protected_workspace_is_not_guessed(tmp_path):
    queue = TaskQueue(tmp_path / "queue.db")
    workspace = AssignmentWorkspace("host", "example", "tree", str(tmp_path / "tree"), "nonce")
    task = queue.create("legacy", repo="example")
    queue.claim_one(workspace.worker_id, task_id=task.id)
    task = queue.start(task.id, workspace.worker_id, owner_session_id="native")
    with pytest.raises(TaskError, match="original protected workspace"):
        queue.prepare_submission(
            task.id, task.owner, request_id="request", expected_generation=task.generation,
            expected_owner_session_id=task.owner_session_id, workspace=workspace,
        )
    assert queue.submission_preparation(task.id) is None


def test_unconfirmed_assignment_refuses_preparation(submission):
    queue, _owner, task, arguments = submission
    relationship = queue.task_relationship(task.id)
    relationship["state"] = "pending"
    with queue._connect() as conn:
        conn.execute(
            "UPDATE task_events SET task_relationship=? WHERE id=?",
            (json.dumps(relationship), relationship["request"]["event_id"]),
        )
    with pytest.raises(TaskError, match="unconfirmed"):
        queue.prepare_submission(task.id, task.owner, **arguments)
    assert queue.submission_preparation(task.id) is None


def test_preparation_and_legacy_completion_serialize_at_the_queue_boundary(submission):
    queue, _owner, task, arguments = submission
    barrier = threading.Barrier(2)

    def run(prepare):
        barrier.wait(timeout=5)
        try:
            if prepare:
                queue.prepare_submission(task.id, task.owner, **arguments)
            else:
                queue.complete(task.id, task.owner, result=arguments["result"])
        except TaskError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(run, [True, False]))
    assert sum(outcomes) == 1
    if outcomes[0]:
        assert queue.get(task.id) == task and queue.submission_preparation(task.id) is not None
    else:
        assert queue.get(task.id).status == Status.SUBMITTED
        assert queue.submission_preparation(task.id) is None


@pytest.mark.parametrize("require_verification", [True, False])
def test_completion_and_rollback_cannot_bypass_retained_preparation(
    submission, require_verification,
):
    queue, _owner, task, arguments = submission
    with queue._connect() as conn:
        conn.execute(
            "UPDATE tasks SET require_verification=? WHERE id=?",
            (require_verification, task.id),
        )
    task = queue.get(task.id)
    value = queue.prepare_submission(task.id, task.owner, **arguments)
    with pytest.raises(TaskError, match="authoritative readiness"):
        queue.complete(task.id, task.owner, result=arguments["result"])
    with queue._connect() as conn, pytest.raises(sqlite3.IntegrityError, match="readiness"):
        conn.execute("UPDATE tasks SET status='submitted' WHERE id=?", (task.id,))
    assert queue.get(task.id) == task and queue.submission_preparation(task.id) == value
    with TestClient(create_app(queue, enable_mcp=False)) as client:
        response = client.post(
            f"/tasks/{task.id}/complete",
            json={"worker_id": task.owner, "result": arguments["result"]},
        )
    assert response.status_code == 409
    assert "authoritative readiness" in response.text
    assert queue.get(task.id).status == Status.STARTED


def test_request_and_audit_custody_cannot_be_overwritten_or_deleted(submission):
    queue, _owner, task, arguments = submission
    value = queue.prepare_submission(task.id, task.owner, **arguments)
    changed = json.loads(json.dumps(value))
    changed["request"]["result"] = {"forged": True}
    for sql, parameters in (
        (
            "UPDATE task_events SET submission_preparation=?"
            " WHERE submission_preparation IS NOT NULL",
            (json.dumps(changed),),
        ),
        ("DELETE FROM task_events WHERE submission_preparation IS NOT NULL", ()),
        ("UPDATE task_events SET task_id='other' WHERE submission_preparation IS NOT NULL", ()),
    ):
        with queue._connect() as conn, pytest.raises(
            sqlite3.IntegrityError, match=r"immutable|retained",
        ):
            conn.execute(sql, parameters)
    assert queue.submission_preparation(task.id) == value

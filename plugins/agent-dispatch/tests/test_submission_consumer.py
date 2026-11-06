"""Ordinary completion cannot bypass guarded submission at the shared queue."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from agent_dispatch import queue_submission_advancement as advancement
from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import Status, TaskError, TaskQueue
from tests import test_submission_readiness as readiness_controls

ready = readiness_controls.ready
pytestmark = pytest.mark.parametrize(
    "ready", [("unprepared", {"answer": "done"})], indirect=True,
)


def test_complete_prepares_observes_and_replays_without_releasing_claim(ready):
    queue, owner, task, _, _, _, _, calls = ready
    relationship = queue.task_relationship(task.id)
    submitted = queue.complete_with_outcome(task.id, task.owner, result={"answer": "done"})
    proposal = queue.submission_preparation(task.id)
    assert proposal["request"]["result"] == {"answer": "done"}
    assert proposal["request"]["owner_session_id"] == task.owner_session_id
    assert proposal["advancement"]["state"] == "submitted"
    assert submitted.task.status == Status.SUBMITTED and submitted.event_type == "task.submitted"
    assert queue.task_relationship(task.id) == relationship and len(owner.claims) == 1
    assert len(calls) == 3 and len(queue.list_verification_requests(task.id)) == 1
    restarted = TaskQueue(queue.db_path)
    replay = restarted.complete_with_outcome(task.id, task.owner, result={"answer": "done"})
    assert replay.task == submitted.task and replay.event_type is None and len(calls) == 3
    restarted.confirm(task.id, actor="independent reviewer")
    assert restarted.complete(task.id, task.owner, result={"answer": "done"}).status == Status.COMPLETED


def test_readiness_failure_retains_proposal_and_same_request_can_retry(ready, monkeypatch):
    queue, _, task, _, _, _, _, calls = ready
    observe = advancement.observe_owner_state

    def blocked(task_id, original):
        raise TaskError("land the current pull request")

    monkeypatch.setattr(advancement, "observe_owner_state", blocked)
    with pytest.raises(TaskError, match="land the current pull request"):
        queue.complete(task.id, task.owner, result={"answer": "done"})
    original = queue.submission_preparation(task.id)
    assert original["blocker"]["code"] == "submission-readiness"
    assert queue.get(task.id) == task and not queue.list_verification_requests(task.id)
    with pytest.raises(TaskError, match="conflicts"):
        queue.complete(task.id, task.owner, result={"answer": "replacement"})
    monkeypatch.setattr(advancement, "observe_owner_state", observe)
    assert queue.complete(task.id, task.owner, result={"answer": "done"}).status == Status.SUBMITTED
    assert queue.submission_preparation(task.id)["request"] == original["request"]
    assert len(calls) == 3


@pytest.mark.parametrize("change", ["worker", "native", "generation", "hold"])
def test_changed_owner_cannot_prepare_or_submit(ready, change):
    queue, _, task, _, _, _, _, calls = ready
    arguments = {"result": {"answer": "done"}}
    worker = task.owner
    if change == "worker":
        worker = "another/tree"
    elif change == "native":
        arguments["expected_owner_session_id"] = "replacement"
    elif change == "generation":
        arguments.update(
            expected_generation=task.generation + 1,
            expected_owner_session_id=task.owner_session_id,
        )
    else:
        queue.set_hold(task.id, actor="operator", reason="inspection")
    with pytest.raises(TaskError):
        queue.complete(task.id, worker, **arguments)
    assert queue.submission_preparation(task.id) is None and not calls
    assert queue.get(task.id).status == Status.STARTED


def test_legacy_sql_completion_cannot_skip_preparation_before_first_worker_call(ready):
    queue, _, task, _, _, _, _, calls = ready
    for destination in ("submitted", "completed"):
        with queue._connect() as conn, pytest.raises(sqlite3.IntegrityError, match="readiness"):
            conn.execute("UPDATE tasks SET status=? WHERE id=?", (destination, task.id))
    assert queue.get(task.id) == task and not calls


def test_ordinary_completion_consumes_exact_rejection_and_resubmits_in_place(ready):
    queue, owner, task, _, _, _, _, calls = ready
    first = queue.complete(task.id, task.owner, result={"answer": "done"})
    original = queue.submission_preparation(task.id)
    queue.reject_submission(
        task.id, reason="revise the answer", feedback={"revision": 2},
        expected_generation=first.generation,
        expected_owner_session_id=first.owner_session_id,
        expected_completed_by=first.completed_by, expected_updated_at=first.updated_at,
    )
    queue.take_steer(task.id, task.owner, all_pending=True)
    second = queue.complete(task.id, task.owner, result={"answer": "revised"})
    revised = queue.submission_preparation(task.id)
    assert revised["request"]["request_id"] != original["request"]["request_id"]
    assert revised["request"]["resubmission"]["feedback"] == {"revision": 2}
    assert second.owner_session_id == first.owner_session_id and second.generation == first.generation
    assert len(owner.claims) == 1 and len(calls) == 6
    with queue._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM task_events WHERE submission_preparation IS NOT NULL",
        ).fetchone()[0] == 2


def test_concurrent_ordinary_requests_share_one_preparation_and_submission(ready, monkeypatch):
    queue, _, task, _, _, _, _, _ = ready
    observe = advancement.observe_owner_state
    barrier = threading.Barrier(2)

    def observed(task_id, original):
        result = observe(task_id, original)
        barrier.wait(timeout=5)
        return result

    monkeypatch.setattr(advancement, "observe_owner_state", observed)
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(
            lambda _: queue.complete_with_outcome(task.id, task.owner, result={"answer": "done"}),
            range(2),
        ))
    assert sorted(str(outcome.event_type) for outcome in outcomes) == ["None", "task.submitted"]
    assert len(queue.list_verification_requests(task.id)) == 1
    with queue._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM task_events WHERE submission_preparation IS NOT NULL",
        ).fetchone()[0] == 1


def test_old_http_submission_reaches_same_guard_and_exact_retry(ready):
    queue, _, task, _, _, _, _, calls = ready
    with TestClient(create_app(queue, enable_mcp=False, verification_interval=0)) as api:
        body = {"worker_id": task.owner, "result": {"answer": "done"}}
        response = api.post(f"/tasks/{task.id}/complete", json=body)
        assert response.status_code == 200 and response.json()["status"] == Status.SUBMITTED
        assert api.post(f"/tasks/{task.id}/complete", json=body).status_code == 200
        assert api.post(
            f"/tasks/{task.id}/complete", json={**body, "result": {"answer": "changed"}},
        ).status_code == 409
    assert len(calls) == 3
    assert queue.submission_preparation(task.id)["request"]["result"] == {"answer": "done"}
    assert len(queue.list_verification_requests(task.id)) == 1


def test_confirmation_is_not_implicit_and_no_worktree_baseline_is_unchanged(ready, tmp_path):
    queue, _, task, _, _, _, _, calls = ready
    with queue._connect() as conn:
        conn.execute("UPDATE tasks SET require_verification=0 WHERE id=?", (task.id,))
    with pytest.raises(TaskError, match="independent review"):
        queue.complete(task.id, task.owner)
    assert queue.get(task.id).status == Status.STARTED and not calls
    assert queue.submission_preparation(task.id)["request"]["result"] is None
    legacy = TaskQueue(tmp_path / "baseline.db")
    other = legacy.create("baseline", repo="example")
    legacy.claim_one("ordinary", task_id=other.id)
    legacy.start(other.id, "ordinary")
    assert legacy.complete(other.id, "ordinary").status == Status.COMPLETED
    assert legacy.submission_preparation(other.id) is None


@pytest.mark.parametrize("boundary", ["preparation", "readiness"])
def test_expected_status_fences_both_sides_of_external_readiness(ready, monkeypatch, boundary):
    queue, _, task, _, _, _, _, calls = ready
    prepare = queue.prepare_submission
    observe = advancement.observe_owner_state

    def suspend():
        queue.suspend(task.id, task.owner, reason="concurrent suspension")

    if boundary == "preparation":
        def changed(*args, **kwargs):
            suspend()
            return prepare(*args, **kwargs)
        monkeypatch.setattr(queue, "prepare_submission", changed)
    else:
        def changed(task_id, original):
            result = observe(task_id, original)
            suspend()
            return result
        monkeypatch.setattr(advancement, "observe_owner_state", changed)
    with pytest.raises(TaskError, match="expected status"):
        queue.complete(task.id, task.owner, result={"answer": "done"}, expected_status="started")
    assert queue.get(task.id).status == Status.SUSPENDED
    assert not queue.list_verification_requests(task.id)
    assert len(calls) == (0 if boundary == "preparation" else 3)
    if boundary == "readiness":
        assert queue.submission_preparation(task.id)["request"]["expected_status"] == "started"


def test_manual_protected_assignment_is_retained_not_guessed_to_be_unmanaged(ready, tmp_path):
    from agent_dispatch.assignment_workspace import AssignmentWorkspace
    from tests.test_assignment_relationships import GroundOwner, claim

    owner = GroundOwner()
    queue = TaskQueue(
        tmp_path / "manual.db", relationship_writer=owner, relationship_authority="local",
    )
    owner.queue = queue
    workspace = AssignmentWorkspace("host-a", "example", "manual", str(tmp_path / "manual"), "nonce")
    task = queue.create("manual", repo="example", require_verification=True)
    claim(queue, workspace, task)
    task = queue.start(task.id, workspace.worker_id, owner_session_id="native")
    with pytest.raises(TaskError, match="qualified managed allocation"):
        queue.complete(task.id, task.owner, result={"answer": "retained"})
    assert queue.get(task.id) == task and len(owner.claims) == 1
    assert queue.submission_preparation(task.id)["request"]["result"] == {"answer": "retained"}


def test_registered_http_mcp_completion_cannot_skip_queue_readiness(ready, monkeypatch):
    from agent_dispatch.events import EventBus
    from agent_dispatch.mcp_http import build_coordinator_mcp

    queue, _, task, _, _, _, _, calls = ready
    server = build_coordinator_mcp(queue, EventBus())
    arguments = {"task_id": task.id, "worker_id": task.owner, "result": {"answer": "done"}}
    observe = advancement.observe_owner_state

    def blocked(*_):
        raise TaskError("readiness unavailable")

    monkeypatch.setattr(advancement, "observe_owner_state", blocked)
    asyncio.run(server.call_tool("dispatch_complete", arguments))
    assert queue.get(task.id).status == Status.STARTED
    proposal = queue.submission_preparation(task.id)
    assert proposal["request"]["result"] == {"answer": "done"}
    assert not calls and not queue.list_verification_requests(task.id)
    monkeypatch.setattr(advancement, "observe_owner_state", observe)
    asyncio.run(server.call_tool("dispatch_complete", arguments))
    assert queue.get(task.id).status == Status.SUBMITTED
    assert queue.submission_preparation(task.id)["request"] == proposal["request"]
    assert len(calls) == 3 and len(queue.list_verification_requests(task.id)) == 1


def test_original_machine_case_alias_keeps_exact_workspace_and_completion_owner(ready):
    queue, _, task, _, _, _, _, _ = ready
    with queue._connect() as conn:
        conn.execute("UPDATE tasks SET owner='HOST-A/tree' WHERE id=?", (task.id,))
    submitted = queue.complete(task.id, "HOST-A/tree", result={"answer": "done"})
    assert submitted.completed_by == "HOST-A/tree"
    assert queue.submission_preparation(task.id)["request"]["workspace"]["machine"] == "host-a"


def test_http_client_waits_for_owner_observation_beyond_its_baseline_read_timeout(ready, monkeypatch):
    from agent_dispatch.client import DispatchClient
    from tests.test_mcp_http import _boot

    queue, _, task, _, _, _, _, calls = ready
    observe = advancement.observe_owner_state

    def delayed(task_id, original):
        time.sleep(11)
        return observe(task_id, original)

    monkeypatch.setattr(advancement, "observe_owner_state", delayed)
    url, stop = _boot(create_app(queue, enable_mcp=False, verification_interval=0))
    try:
        with DispatchClient(url) as client:
            assert client._http.timeout.read == 10.0
            submitted = client.complete(task.id, task.owner, result={"answer": "done"})
            assert client._http.timeout.read == 10.0
        assert submitted["status"] == Status.SUBMITTED
        assert queue.submission_preparation(task.id)["advancement"]["state"] == "submitted"
        assert len(calls) == 3 and len(queue.list_verification_requests(task.id)) == 1
    finally:
        stop()


@pytest.mark.parametrize("configured,expected", [(10.0, 60.0), (120.0, 120.0)])
def test_completion_extends_only_read_budget_and_preserves_a_longer_selected_timeout(
    ready, configured, expected,
):
    import httpx

    from agent_dispatch.client import DispatchClient

    observed = []

    def reply(request):
        observed.append((request.url.path, request.extensions["timeout"]))
        return httpx.Response(200, json={"status": "submitted"})

    with DispatchClient(
        "http://coordinator.invalid", timeout=configured, transport=httpx.MockTransport(reply),
    ) as client:
        client.complete("task", "worker")
        client.health()
        assert client._http.timeout.read == configured
    assert observed == [
        ("/tasks/task/complete", {
            "connect": configured, "read": expected, "write": configured, "pool": configured,
        }),
        ("/health", {
            "connect": configured, "read": configured, "write": configured, "pool": configured,
        }),
    ]

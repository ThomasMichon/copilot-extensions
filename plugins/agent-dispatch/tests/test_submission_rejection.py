"""Whole-goal rejection preserves the conversation and exact submission evidence."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import sys
from unittest.mock import Mock

import pytest

from agent_dispatch import task_state_machine
from agent_dispatch.producers.evaluator import (
    EvaluatorError, Reject, SpecEvaluator, apply_decisions, decision_from_dict,
)
from agent_dispatch.queue import Status, TaskError
from agent_dispatch.supervisor import Supervisor
from agent_dispatch.verification import evaluate_submitted_task
from tests._helpers import RepoDefaultingQueue as TaskQueue, TEST_REPO


def _submitted(q, *, owner="host-a/wt-1", session="session-1", reserve=False):
    task = q.create(
        "finish the whole goal", require_verification=True, evaluator_ref="goal",
        goal="Reach the external terminal condition",
    )
    if reserve:
        q.reserve_spawn(task.id)
    q.claim_one(owner, task_id=task.id, now=10)
    q.start(task.id, owner, owner_session_id=session, now=11)
    q.record_progress(task.id, owner, phase="review", summary="verdict posted", now=12)
    return q.complete(
        task.id, owner, result={"verdict": "stale"}, result_ref="artifact:old", now=13,
    )


def _reject(q, task, **overrides):
    args = {
        "reason": "Goal still open; inspected verdict was already obsolete",
        "feedback": {"stale_verdict": True, "next": "wait for a fresh submission"},
        "expected_generation": task.generation,
        "expected_owner_session_id": task.owner_session_id,
        "expected_completed_by": task.completed_by,
        "expected_updated_at": task.updated_at,
        "now": 20,
    }
    args.update(overrides)
    return q.reject_submission(task.id, **args)


def test_submission_rejection_roundtrip_and_exact_bounds():
    value = {"decision": "reject", "reason": "x" * 4096, "feedback": {"x": "y" * 16376}}
    assert decision_from_dict(value).to_dict() == value
    assert SpecEvaluator({"rules": [{"on": "task.submitted", "reject": {
        "reason": "not done", "feedback": {"next": "continue"},
    }}]}).evaluate({"type": "task.submitted"}) == [Reject("not done", {"next": "continue"})]
    assert Reject("reason").to_dict()["feedback"] is None
    with pytest.raises(EvaluatorError, match="coordinator-owned"):
        apply_decisions([Reject("not done")], creator=Mock(), task_id="t1")


@pytest.mark.parametrize("extra", [
    {"reason": None}, {"reason": ""}, {"reason": " \n"}, {"reason": "a\0b"},
    {"reason": "\ud800"}, {"reason": "x" * 4097}, {"reason": "\u00e9" * 2049},
    {"feedback": []}, {"feedback": "text"}, {"feedback": {"x": float("nan")}},
    {"feedback": {"x": "y" * 16377}}, {"unknown": True},
])
def test_submission_rejection_invalid_payload(extra):
    with pytest.raises(EvaluatorError):
        decision_from_dict({"decision": "reject", "reason": "unfinished", **extra})


def test_submission_rejection_preserves_identity_evidence_and_retries(tmp_path):
    q = TaskQueue(tmp_path / "tasks.db")
    task = _submitted(q)
    attached = q.attachment_history(task.id)
    notifications = []
    q.set_wake_notifier(lambda: notifications.append(q.get(task.id).status))
    outcome = _reject(q, task)
    recovered = outcome.task
    assert outcome.event_type == "task.rejected"
    assert (recovered.id, recovered.generation, recovered.owner, recovered.owner_session_id) == (
        task.id, task.generation, task.completed_by, task.owner_session_id,
    )
    assert recovered.status == Status.STARTED
    assert recovered.goal == task.goal and recovered.latest_progress == task.latest_progress
    assert q.attachment_history(task.id) == attached
    assert recovered.result is None and recovered.result_ref is None
    assert recovered.completed_at is None and recovered.completed_by is None
    assert notifications == [Status.STARTED]
    evidence = q.steer_log(task.id)[0]["fields"]["verification_rejection"]
    assert evidence["submission"]["result"] == {"verdict": "stale"}
    assert evidence["submission"]["result_ref"] == "artifact:old"
    assert evidence["submission"]["completed_by"] == task.completed_by
    assert evidence["submission"]["updated_at"] == task.updated_at
    before = q.events(task.id)
    assert _reject(q, task).event_type is None
    assert q.events(task.id) == before and len(q.list_wakes(task.id)) == 1
    assert len(q.steer_log(task.id)) == 1 and notifications == [Status.STARTED]
    with pytest.raises(TaskError, match="changed"):
        _reject(q, task, reason="different rejection")
    first = q.claim_due_wake(now=20)
    assert first.owner_session_id == task.owner_session_id
    assert "already obsolete" in first.message
    retry = q.finish_wake(
        first.id, first.delivery_token, delivered=False,
        error="bridge unavailable", retry_base=2, now=21,
    )
    assert retry.status == "pending" and retry.not_before == 23
    assert q.claim_due_wake(now=22) is None
    restarted = TaskQueue(q.db_path)
    second = restarted.claim_due_wake(now=23)
    assert second.id == first.id and second.attempts == 2
    restarted.finish_wake(second.id, second.delivery_token, delivered=True, now=24)
    [steer] = q.take_steer(task.id, task.completed_by, all_pending=True)
    assert steer["fields"]["verification_rejection"] == evidence
    q.suspend(task.id, task.completed_by, reason="waiting for author", now=25)
    q.complete(task.id, task.completed_by, result={"verdict": "fresh"}, now=26)
    assert q.get(task.id).status == Status.SUBMITTED
    assert q.get(task.id).result == {"verdict": "fresh"}
    assert q.steer_log(task.id)[0]["fields"]["verification_rejection"] == evidence
    assert len(q.list_verification_requests(task.id)) == 2
    with pytest.raises(TaskError, match="changed"):
        _reject(q, task)


@pytest.mark.parametrize("changed", [
    {"expected_generation": 999}, {"expected_owner_session_id": "other-session"},
    {"expected_completed_by": "other-owner"}, {"expected_updated_at": 12},
])
def test_submission_rejection_fences_snapshot(tmp_path, changed):
    q = TaskQueue(tmp_path / "tasks.db")
    task = _submitted(q)
    with pytest.raises(TaskError, match="changed"):
        _reject(q, task, **changed)
    assert q.get(task.id) == task
    assert q.steer_log(task.id) == [] and q.list_wakes(task.id) == []


@pytest.mark.parametrize("same_owner", [True, False])
def test_submission_rejection_owner_or_session_collision(tmp_path, same_owner):
    q = TaskQueue(tmp_path / "tasks.db")
    task = _submitted(q)
    other = q.create("new work")
    owner = task.completed_by if same_owner else "other-worker"
    q.claim_one(owner, task_id=other.id)
    q.start(other.id, owner, owner_session_id="new-session" if same_owner else task.owner_session_id)
    with pytest.raises(TaskError, match="other work"):
        _reject(q, task)
    assert q.get(task.id) == task


def test_submission_rejection_missing_session_and_terminal_status(tmp_path):
    q = TaskQueue(tmp_path / "tasks.db")
    missing = _submitted(q, session=None)
    with pytest.raises(TaskError, match="exact submitting"):
        _reject(q, missing)
    task = _submitted(q)
    q.confirm(task.id)
    with pytest.raises(TaskError, match="changed"):
        _reject(q, task)
    assert q.get(task.id).status == Status.COMPLETED


def test_submission_rejection_outbox_failure_is_atomic(tmp_path, monkeypatch):
    q = TaskQueue(tmp_path / "tasks.db")
    task = _submitted(q)
    before = q.events(task.id)
    monkeypatch.setattr(q, "_enqueue_wake", Mock(side_effect=TaskError("outbox unavailable")))
    with pytest.raises(TaskError, match="outbox unavailable"):
        _reject(q, task)
    assert q.get(task.id) == task and q.events(task.id) == before
    assert q.steer_log(task.id) == [] and q.list_wakes(task.id) == []


def test_submission_rejection_uses_declared_transition(tmp_path, monkeypatch):
    q = TaskQueue(tmp_path / "tasks.db")
    task = _submitted(q)
    table = dict(task_state_machine.TRANSITIONS_BY_NAME)
    table["reject_submission"] = replace(table["reject_submission"], from_states=frozenset())
    monkeypatch.setattr(task_state_machine, "TRANSITIONS_BY_NAME", table)
    with pytest.raises(TaskError, match="changed"):
        _reject(q, task)


def test_submission_rejection_whole_goal_script_and_event(tmp_path):
    q = TaskQueue(tmp_path / "tasks.db")
    script = tmp_path / "judge.py"
    script.write_text(
        "import json, sys\n"
        "event = json.load(sys.stdin)\n"
        "assert event['task']['result'] == {'verdict': 'stale'}\n"
        "json.dump({'decision': 'reject', 'reason': 'Goal open; verdict DOA', "
        "'feedback': {'stale_verdict': True}}, sys.stdout)\n",
    )
    q.register_registration("evaluator", {
        "repo": TEST_REPO, "evaluator_ref": "goal",
        "evaluator_spec": {"scripts": {"goal": [sys.executable, str(script)]}},
    })
    task = _submitted(q)
    bus = Mock()
    report = evaluate_submitted_task(q, task.id, trigger="submitted", bus=bus)
    assert report["applied"][0]["decision"] == "reject"
    assert report["decisions"][0]["feedback"] == {"stale_verdict": True}
    assert q.get(task.id).status == Status.STARTED
    assert bus.publish.call_args.args[0]["type"] == "task.rejected"


def test_submission_rejection_supervisor_keeps_pending_conversation():
    supervisor = object.__new__(Supervisor)
    supervisor.client = Mock()
    supervisor.client.get.return_value = {"status": Status.SUBMITTED, "require_verification": True}
    supervisor._pool_reservations = Mock(side_effect=[[{
        "key": "reservation", "task_id": "t1", "state": "spawned",
        "worktree_ownership": "created",
    }], []])
    assert supervisor.reconcile() == 0
    supervisor.client.request_spawn_release.assert_not_called()
    supervisor.client.record_spawn_conclusion.assert_not_called()


def test_submission_rejection_concurrent_replay_is_one_operation(tmp_path):
    q = TaskQueue(tmp_path / "tasks.db")
    task = _submitted(q)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: _reject(q, task), range(2)))
    assert sorted(result.event_type or "" for result in results) == ["", "task.rejected"]
    assert len(q.steer_log(task.id)) == len(q.list_wakes(task.id)) == 1


def test_submission_rejection_evaluator_inflight_change_is_fenced(tmp_path, monkeypatch):
    q = TaskQueue(tmp_path / "tasks.db")
    task = _submitted(q)
    q.register_registration("evaluator", {
        "repo": TEST_REPO, "evaluator_ref": "goal",
        "evaluator_spec": {"rules": []},
    })

    def evaluate(_event):
        q.append_event_note(task.id, note="new evidence", sender="source")
        return [Reject("outdated judgment")]

    evaluator = Mock()
    evaluator.evaluate.side_effect = evaluate
    monkeypatch.setattr("agent_dispatch.verification._evaluator_for_task", lambda *a, **k: evaluator)
    with pytest.raises(TaskError, match="changed"):
        evaluate_submitted_task(q, task.id, trigger="submitted")
    assert q.get(task.id).status == Status.SUBMITTED
    assert q.steer_log(task.id) == q.list_wakes(task.id) == []


def test_submission_rejection_does_not_repair_retiring_session(tmp_path):
    q = TaskQueue(tmp_path / "tasks.db")
    task = _submitted(q, reserve=True)
    [reservation] = q.list_reservations()
    q.request_spawn_release(reservation.key, disposition="settled")
    with pytest.raises(TaskError, match="retirement already began"):
        _reject(q, task)
    assert q.get(task.id) == task
    assert q.steer_log(task.id) == q.list_wakes(task.id) == []

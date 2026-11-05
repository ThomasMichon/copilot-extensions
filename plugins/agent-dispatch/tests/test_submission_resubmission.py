"""Repeated queue proposals retain the original claim; not ordinary/native acceptance."""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from typing import Any

import pytest

from agent_dispatch.assignment_workspace import AssignmentWorkspace
from agent_dispatch.queue import Status, TaskError, TaskQueue
from agent_dispatch.queue_common import Task
from agent_dispatch.queue_submission_resubmission import rejected_preparation
from tests import test_submission_readiness as readiness_controls

ready = readiness_controls.ready
retained = readiness_controls.retained


def _reject(queue: TaskQueue, task_id: str, request_id: str) -> Task:
    task = queue.advance_prepared_submission(task_id, request_id=request_id).task
    return queue.reject_submission(
        task_id, reason="Review the changed candidate",
        feedback={"inspected_answer": task.result, "next_candidate": request_id + "-next"},
        expected_generation=task.generation,
        expected_owner_session_id=task.owner_session_id,
        expected_completed_by=task.completed_by,
        expected_updated_at=task.updated_at,
    ).task


def _prepare(queue: TaskQueue, task_id: str, request_id: str, result: dict) -> dict:
    original = queue.submission_preparation(task_id)["request"]
    task = queue.get(task_id)
    assert task is not None and task.owner is not None and task.owner_session_id is not None
    return queue.prepare_submission(
        task_id, task.owner, request_id=request_id,
        expected_generation=task.generation,
        expected_owner_session_id=task.owner_session_id,
        workspace=AssignmentWorkspace.from_dict(original["workspace"]),
        result=result, result_ref="file:///" + request_id,
    )


@pytest.mark.parametrize("parked", [False, True])
def test_two_rejections_resubmit_in_place_and_preserve_every_consumed_proposal(
    ready: Any, parked: bool,
) -> None:
    queue, owner, task, first, _, _, _, _ = ready
    relationship = queue.task_relationship(task.id)
    claims = deepcopy(owner.claims)
    receipts = []
    request_id = "proposal"
    for number in (2, 3):
        rejected = _reject(queue, task.id, request_id)
        receipts.append(queue.submission_preparation(task.id))
        assert rejected.status == Status.STARTED and rejected.owner == task.owner
        assert rejected.owner_session_id == task.owner_session_id
        if parked:
            queue.take_steer(task.id, task.owner, all_pending=True)
            queue.suspend(task.id, task.owner, reason="Investigating the retained feedback")
        request_id = f"proposal-{number}"
        value = _prepare(queue, task.id, request_id, {"answer": number})
        lineage = value["request"]["resubmission"]
        assert lineage["reason"] == "Review the changed candidate"
        assert lineage["feedback"]["inspected_answer"] == receipts[-1]["request"]["result"]
        assert queue.prepare_submission(
            task.id, task.owner, request_id=request_id,
            expected_generation=task.generation,
            expected_owner_session_id=task.owner_session_id,
            workspace=AssignmentWorkspace.from_dict(first["request"]["workspace"]),
            result={"answer": number}, result_ref="file:///" + request_id,
        ) == value
        queue = TaskQueue(queue.db_path)
    submitted = queue.advance_prepared_submission(task.id, request_id=request_id).task
    assert submitted.result == {"answer": 3} and submitted.status == Status.SUBMITTED
    assert submitted.completed_by == task.owner
    assert submitted.owner_session_id == task.owner_session_id
    assert queue.task_relationship(task.id) == relationship and owner.claims == claims
    with queue._connect() as conn:
        history = [
            json.loads(row[0]) for row in conn.execute(
                "SELECT submission_preparation FROM task_events"
                " WHERE task_id=? AND submission_preparation IS NOT NULL ORDER BY id", (task.id,),
            )
        ]
    assert history[:2] == receipts and len(history) == 3
    assert history[0]["request"] == first["request"]
    assert queue.confirm(task.id, actor="independent reviewer").status == Status.COMPLETED


def test_each_exact_rejection_gates_only_one_new_request_and_old_ids_never_rearm(
    ready: Any,
) -> None:
    queue, _, task, _, _, _, _, _ = ready
    _reject(queue, task.id, "proposal")
    with pytest.raises(TaskError, match="already consumed"):
        _prepare(queue, task.id, "proposal", {"answer": "old"})
    value = _prepare(queue, task.id, "new", {"answer": "revised"})
    before = retained(queue, queue.get(task.id))
    with pytest.raises(TaskError, match="conflicts"):
        _prepare(queue, task.id, "another", {"answer": "replacement"})
    with pytest.raises(TaskError, match="superseded"):
        _prepare(queue, task.id, "proposal", {"answer": "old"})
    with pytest.raises(TaskError, match="request changed"):
        queue.advance_prepared_submission(task.id, request_id="proposal")
    assert queue.submission_preparation(task.id) == value
    assert retained(queue, queue.get(task.id)) == before


@pytest.mark.parametrize("change", [
    "result", "result_ref", "generation", "owner_session_id", "completed_by",
    "completed_at", "updated_at", "reason", "feedback", "sender", "audit",
])
def test_copied_changed_or_unauthored_rejection_cannot_admit_another_proposal(
    ready: Any, change: str,
) -> None:
    queue, _, task, _, _, _, _, _ = ready
    rejected = _reject(queue, task.id, "proposal")
    with queue._connect() as conn:
        row = conn.execute("SELECT id, fields FROM task_steer WHERE task_id=?", (task.id,)).fetchone()
        fields = json.loads(row["fields"])
        if change == "sender":
            conn.execute("UPDATE task_steer SET sender='caller' WHERE id=?", (row["id"],))
        elif change == "audit":
            conn.execute(
                "DELETE FROM task_events WHERE task_id=? AND note LIKE 'submission rejected:%'",
                (task.id,),
            )
        else:
            target = fields["verification_rejection"]
            if change not in {"reason", "feedback"}:
                target = target["submission"]
            target[change] = {"copied": True} if change == "result" else "changed"
            conn.execute("UPDATE task_steer SET fields=? WHERE id=?", (json.dumps(fields), row["id"]))
    before = retained(queue, rejected)
    with pytest.raises(TaskError, match="resubmission"):
        _prepare(queue, task.id, "new", {"answer": "not yet qualified"})
    assert retained(queue, rejected) == before


@pytest.mark.parametrize("same_request", [False, True])
def test_concurrent_preparations_consume_one_rejection_once(
    ready: Any, same_request: bool,
) -> None:
    queue, _, task, _, _, _, _, _ = ready
    _reject(queue, task.id, "proposal")
    barrier = threading.Barrier(2)

    def prepare(number: int) -> dict | None:
        barrier.wait(timeout=5)
        try:
            return _prepare(
                queue, task.id, "new" if same_request else f"new-{number}", {"answer": "revised"},
            )
        except TaskError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        values = list(executor.map(prepare, (1, 2)))
    assert sum(value is not None for value in values) == (2 if same_request else 1)
    if same_request:
        assert values[0] == values[1]
    with queue._connect() as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM task_events WHERE submission_preparation IS NOT NULL",
        ).fetchone()[0] == 2


def test_original_feedback_changes_refuse_before_a_fresh_owner_observation(ready: Any) -> None:
    queue, _, task, _, _, _, _, calls = ready
    _reject(queue, task.id, "proposal")
    _prepare(queue, task.id, "new", {"answer": "revised"})
    with queue._connect() as conn:
        row = conn.execute("SELECT id, fields FROM task_steer WHERE task_id=?", (task.id,)).fetchone()
        fields = json.loads(row["fields"])
        fields["verification_rejection"]["feedback"] = {"unattributed": True}
        conn.execute("UPDATE task_steer SET fields=? WHERE id=?", (json.dumps(fields), row["id"]))
    before = retained(queue, queue.get(task.id))
    observed = len(calls)
    with pytest.raises(TaskError, match="lineage changed"):
        queue.advance_prepared_submission(task.id, request_id="new")
    assert retained(queue, queue.get(task.id)) == before and len(calls) == observed


@pytest.mark.parametrize("field", [
    "generation", "owner_session_id", "queue_identity", "workspace", "assignment_event_id",
])
def test_rejection_cannot_follow_a_different_assignment_binding(ready: Any, field: str) -> None:
    queue, _, task, _, _, _, _, _ = ready
    _reject(queue, task.id, "proposal")
    request = deepcopy(queue.submission_preparation(task.id)["request"])
    request["request_id"] = "new"
    request[field] = "changed"
    with queue._connect() as conn:
        row = conn.execute(
            "SELECT id, submission_preparation FROM task_events"
            " WHERE task_id=? AND submission_preparation IS NOT NULL", (task.id,),
        ).fetchone()
        with pytest.raises(TaskError, match="binding"):
            rejected_preparation(conn, row, request)


def test_feedback_change_during_owner_read_refuses_the_locked_transition(
    ready: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_dispatch import queue_submission_advancement as advancement

    queue, _, task, _, _, _, _, _ = ready
    _reject(queue, task.id, "proposal")
    _prepare(queue, task.id, "new", {"answer": "revised"})
    observe = advancement.observe_owner_state

    def changed(task_id: str, original: dict) -> dict:
        result = observe(task_id, original)
        with queue._connect() as conn:
            row = conn.execute(
                "SELECT id, fields FROM task_steer WHERE task_id=?", (task_id,),
            ).fetchone()
            fields = json.loads(row["fields"])
            fields["verification_rejection"]["feedback"] = {"unattributed": True}
            conn.execute(
                "UPDATE task_steer SET fields=? WHERE id=?", (json.dumps(fields), row["id"]),
            )
        return result

    monkeypatch.setattr(advancement, "observe_owner_state", changed)
    with pytest.raises(TaskError, match="lineage changed"):
        queue.advance_prepared_submission(task.id, request_id="new")
    assert queue.get(task.id).status == Status.STARTED
    assert "advancement" not in queue.submission_preparation(task.id)

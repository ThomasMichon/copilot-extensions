from __future__ import annotations

import pytest
from collections.abc import Callable
from typing import cast

from agent_dispatch import pool_decline_observation, queue_submission_advancement, review_closeout_submission
from agent_dispatch.queue_records import TaskError
from agent_dispatch.review_closeout_submission import SUBMIT
from tests import test_task_relevance_closeout_transport as transport
from tests import test_task_relevance_closeout_readiness as readiness
from tests.test_task_relevance_closeout_readiness import Ready as Ready
from tests.test_task_relevance_binding import Assignment
from tests import test_task_relevance_binding as bindings
from tests.test_assignment_relationships import start_managed
from tests.test_task_relevance import report

managed_assignment = transport.managed_assignment
@pytest.fixture
def assignment(managed_assignment: Assignment) -> Assignment:
    queue, owner, workspace, task = managed_assignment
    with queue._connect() as conn:
        conn.execute("UPDATE tasks SET source='emitter',origin_ref='review-source',labels=?,evaluator_ref=? WHERE id=?",
                     ('["pr-review"]', "review-lifecycle", task.id))
    return queue, owner, workspace, task


ready = transport.ready
finalized = transport.finalized


@pytest.mark.parametrize("reject", [False, True])
def test_original_worker_submits_only_after_finalization_and_never_twice(finalized: Ready, reject: bool) -> None:
    queue, _, workspace, task, _ = finalized
    with transport.api(finalized) as api:
        url, args, headers = transport.path(finalized), transport.body(finalized), transport.headers(finalized)
        assert api.get(url, headers=headers).status_code == 409
        headers["X-Dispatch-Closeout-Action"] = SUBMIT
        assert api.get(url, headers=headers).json()["result"]["finish_action"] == SUBMIT
        assert api.post(url + "/finish", headers=headers, json=args).status_code == 409
        args["finish_action"] = SUBMIT
        response = api.post(url + "/finish", headers=headers, json=args)
        assert response.status_code == 200, response.text
        assert response.json()["result"]["status"] == "submitted"
        original = queue.get(task.id)
        assert original is not None and original.completed_by == workspace.worker_id
        assert len(queue.list_verification_requests(task.id)) == 1
        assert api.post(url + "/finish", headers=headers, json=args).json() == response.json()
        if reject:
            wakes = queue.list_wakes(task.id)
            for expected_event in ("task.closeout_disputed", None):
                result = queue.reject_submission(
                    task.id, reason="source observation disputed", feedback={"inspect": "closure"},
                    expected_generation=original.generation, expected_owner_session_id=original.owner_session_id,
                    expected_completed_by=original.completed_by, expected_updated_at=original.updated_at,
                )
                assert result.task == original and result.event_type == expected_event
            assert queue.list_wakes(task.id) == wakes
            feedback = [row["fields"]["closeout_verification_rejection"] for row in queue.steer_log(task.id)
                        if "closeout_verification_rejection" in row["fields"]]
            assert len(feedback) == 1 and feedback[0]["feedback"] == {"inspect": "closure"}
        else:
            queue.confirm(task.id, actor="fixture-evaluator")
        observed = api.get(url, headers=headers).json()["result"]
        assert observed["acknowledgement"]["finish_action"] == SUBMIT
        assert len(queue.list_verification_requests(task.id)) == 1


def test_awaiting_steer_keeps_original_assignment(ready: Ready) -> None:
    queue, owner, workspace, task, _ = ready
    with queue._connect() as conn:
        conn.execute("UPDATE tasks SET status='suspended',awaiting_steer=1 WHERE id=?", (task.id,))
    relationship = queue.task_relationship(task.id)
    with pytest.raises(TaskError, match="outstanding steering"):
        readiness.prepare(queue, workspace, task)
    assert queue.task_relationship(task.id) == relationship
    assert queue.submission_preparation(task.id) is None
    assert not any(operation == "close" for operation, _ in owner.calls)


@pytest.mark.parametrize("scenario", ["pending-result", "legacy", "rejected", "twice-rejected"])
def test_original_proposal_or_legacy_preparation_can_finish(
    assignment: Assignment, monkeypatch: pytest.MonkeyPatch, scenario: str,
) -> None:
    queue, _, workspace, task = assignment
    cast(Callable[..., object], start_managed)(queue, workspace, task)
    if scenario.endswith("rejected"):
        monkeypatch.setattr(queue_submission_advancement, "observe_owner_state", lambda *_: {})
        for revision in range(2 if scenario == "twice-rejected" else 1):
            original = queue.complete(task.id, workspace.worker_id, result={"revision": revision}, result_ref="evidence")
            queue.reject_submission(task.id, reason="still open", feedback={"revision": revision},
                                    expected_generation=original.generation, expected_owner_session_id=original.owner_session_id,
                                    expected_completed_by=original.completed_by, expected_updated_at=original.updated_at)
    if scenario == "pending-result":
        def unavailable(*_: object) -> None:
            raise TaskError("workspace not ready yet")
        monkeypatch.setattr(queue_submission_advancement, "observe_owner_state", unavailable)
        with pytest.raises(TaskError, match="not ready yet"):
            queue.complete(task.id, workspace.worker_id, result={"review": "original"}, result_ref="evidence")
    cast(Callable[..., object], report)(queue, task.id, scope=bindings._scope(queue, task.id))
    from agent_dispatch import submission_readiness_observation
    monkeypatch.setattr(submission_readiness_observation, "observe", lambda *_: {"ready": True})
    if scenario == "legacy":
        with monkeypatch.context() as legacy:
            legacy.setattr(review_closeout_submission, "prepare", lambda *_: None)
            readiness.prepare(queue, workspace, task)
        assert queue.submission_preparation(task.id) is None
        relationship = queue.task_relationship(task.id)
        assert relationship is not None and relationship["state"] == "closed"
    readiness.prepare(queue, workspace, task)
    monkeypatch.setattr(pool_decline_observation, "observe", lambda *_: {
        "status": "finalized", "creation_nonce": workspace.creation_nonce,
    })
    current: Ready = (*assignment, [])
    with transport.api(current) as api:
        response = api.post(transport.path(current, "/finish"), headers=transport.headers(current),
                            json={**transport.body(current), "finish_action": SUBMIT})
        assert response.status_code == 200, response.text
        finished = queue.get(task.id)
        assert finished is not None and finished.status == "submitted"
        if scenario == "pending-result":
            assert finished.result == {"review": "original"} and finished.result_ref == "evidence"
        if scenario.endswith("rejected"):
            proposal = queue.submission_preparation(task.id)
            assert proposal is not None and proposal["request"]["resubmission"]["feedback"] == {
                "revision": 1 if scenario == "twice-rejected" else 0,
            }
        assert queue.list_verification_requests(task.id)

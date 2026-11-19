from __future__ import annotations

import pytest

from agent_dispatch import pool_decline_observation, queue_submission_advancement, review_closeout_submission
from agent_dispatch.queue_records import TaskError
from agent_dispatch.review_closeout_submission import SUBMIT
from tests import test_task_relevance_closeout_transport as transport
from tests import test_task_relevance_closeout_readiness as readiness
from tests.test_task_relevance_closeout_readiness import Ready as Ready
from tests.test_task_relevance_binding import Assignment

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


def test_original_worker_submits_only_after_finalization_and_never_twice(finalized: Ready) -> None:
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
        queue.confirm(task.id, actor="fixture-evaluator")
        observed = api.get(url, headers=headers).json()["result"]
        assert observed["acknowledgement"]["finish_action"] == SUBMIT
        assert len(queue.list_verification_requests(task.id)) == 1


@pytest.mark.parametrize("legacy_prepare", [False, True])
def test_original_proposal_or_legacy_preparation_can_finish(
    assignment: Assignment, monkeypatch: pytest.MonkeyPatch, legacy_prepare: bool,
) -> None:
    queue, _, workspace, task = assignment
    readiness.start_managed(queue, workspace, task)
    if not legacy_prepare:
        def unavailable(*_: object) -> None:
            raise TaskError("workspace not ready yet")
        monkeypatch.setattr(queue_submission_advancement, "observe_owner_state", unavailable)
        with pytest.raises(TaskError, match="not ready yet"):
            queue.complete(task.id, workspace.worker_id, result={"review": "original"}, result_ref="evidence")
    readiness.report(queue, task.id, scope=readiness.bindings._scope(queue, task.id))
    from agent_dispatch import submission_readiness_observation
    monkeypatch.setattr(submission_readiness_observation, "observe", lambda *_: {"ready": True})
    if legacy_prepare:
        with monkeypatch.context() as legacy:
            legacy.setattr(review_closeout_submission, "prepare", lambda *_: None)
            readiness.prepare(queue, workspace, task)
        assert queue.submission_preparation(task.id) is None
        assert queue.task_relationship(task.id)["state"] == "closed"
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
        if not legacy_prepare:
            assert finished.result == {"review": "original"} and finished.result_ref == "evidence"
        assert len(queue.list_verification_requests(task.id)) == 1

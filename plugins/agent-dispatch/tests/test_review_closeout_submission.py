from __future__ import annotations

import pytest

from agent_dispatch.review_closeout_submission import SUBMIT
from tests import test_task_relevance_closeout_transport as transport
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

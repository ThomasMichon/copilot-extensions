"""A retained-driver observation is not settlement or authority to restart a goal."""

from collections.abc import Callable
from typing import cast

import pytest

from agent_dispatch import submission_readiness_observation
from agent_dispatch.assignment_workspace import AssignmentWorkspace
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_common import Task
from agent_dispatch.queue_execution_handoff import require_execution_admission
from agent_dispatch.queue_records import Status, TaskError
from agent_dispatch.queue_task_relationships import require_relationship
from agent_dispatch.review_closeout_submission import SUBMIT
from agent_dispatch.task_obligations import read_snapshot
from tests import test_task_relevance_closeout_transport as transport
from tests.test_assignment_relationships import start_managed
from tests.test_task_relevance_binding import Assignment
from tests.test_task_relevance_closeout_readiness import Ready

managed_assignment = transport.managed_assignment
ready = transport.ready
finalized = transport.finalized


@pytest.fixture
def assignment(managed_assignment: Assignment, request: pytest.FixtureRequest) -> Assignment:
    queue, owner, workspace, _ = managed_assignment
    labels = ["pr-review"] if getattr(request, "param", None) == "legacy-review" else []
    task = queue.create(
        "Inspect a result", repo=workspace.project, require_verification=True,
        evaluator_ref="fixture", source="emitter", origin_ref="source", labels=labels,
    )
    assert isinstance(task, Task)
    return queue, owner, workspace, task


@pytest.mark.parametrize("assignment", ["legacy-review"], indirect=True)
def test_legacy_finalized_submission_disputes_without_reopening_driver(finalized: Ready) -> None:
    queue, _, workspace, task, _ = finalized
    with transport.api(finalized) as api:
        refused = api.post(transport.path(finalized, "/observe-provisional"),
                           headers=transport.headers(finalized), json=transport.body(finalized))
        assert refused.status_code == 409
        response = api.post(
            transport.path(finalized, "/finish"),
            headers={**transport.headers(finalized), "X-Dispatch-Closeout-Action": SUBMIT},
            json={**transport.body(finalized), "finish_action": SUBMIT},
        )
        assert response.status_code == 200, response.text
    submitted = queue.get(task.id)
    assert submitted is not None and submitted.status == Status.SUBMITTED
    placement = queue.task_pool_assignment(task.id)
    assert placement is not None
    relationship = queue.task_relationship(task.id)
    assert relationship is not None and relationship["state"] == "closed"
    assert relationship["request"]["reservation_key"] == placement.reservation_key
    assert relationship["request"]["workspace"]["creation_nonce"] == workspace.creation_nonce
    reservation = queue.get_reservation(placement.reservation_key)
    assert reservation is not None and reservation.session_handle == submitted.owner_session_id
    assert not reservation.release_requested and not reservation.conversation_retired
    receipt = queue.relevance_snapshot(task.id)["closeout"]["acknowledgement"]
    assert receipt["observation"]["status"] == "finalized"
    assert receipt["observation"]["checkout_retained"] is True
    with queue._connect() as conn:
        with pytest.raises(TaskError, match="requires owner closeout"):
            require_execution_admission(conn, task.id)
        with pytest.raises(TaskError, match="relationship protection is unconfirmed"):
            require_relationship(conn, task.id)

    before_wakes = queue.list_wakes(task.id)
    outcome = queue.reject_submission(
        task.id, reason="correction requested", expected_generation=submitted.generation,
        expected_owner_session_id=submitted.owner_session_id,
        expected_completed_by=submitted.completed_by, expected_updated_at=submitted.updated_at,
    )
    assert queue.task_pool_assignment(task.id) == placement
    assert outcome.event_type == "task.closeout_disputed"
    assert outcome.task == submitted and queue.list_wakes(task.id) == before_wakes


def test_observation_rechecks_custody_after_owner_io(
    ready: Ready, monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue, _, _, task, calls = ready
    with transport.api(ready) as api:
        url = transport.path(ready, "/observe-provisional")
        denied = api.post(url, headers=transport.headers(ready, owner_session_id="foreign"),
                          json=transport.body(ready))
        assert denied.status_code == 403 and not calls
        observe = submission_readiness_observation.observe

        def held(request: dict[str, object], allocation: dict[str, object],
                 ref: str) -> dict[str, object]:
            evidence = observe(request, allocation, ref)
            queue.set_hold(task.id, reason="changed during observation", actor="fixture")
            return evidence

        monkeypatch.setattr(submission_readiness_observation, "observe", held)
        refused = api.post(url, headers=transport.headers(ready), json=transport.body(ready))
        assert refused.status_code == 409 and len(calls) == 1
    assert queue.relevance_snapshot(task.id)["closeout"]["readiness"] is None


def test_still_authorized_result_revision_keeps_driver_until_confirmation(
    managed_assignment: Assignment, monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue, _, workspace, _ = managed_assignment
    objective = "Revise only the unit field of the submitted measurement using evaluator feedback."
    task = queue.create("Measurement result revision", repo=workspace.project,
                        goal=objective, require_verification=True, evaluator_ref="units")
    assert isinstance(task, Task)
    start = cast(Callable[[TaskQueue, AssignmentWorkspace, Task], Task], start_managed)
    start(queue, workspace, task)
    relationship = queue.task_relationship(task.id)
    placement = queue.task_pool_assignment(task.id)
    assert placement is not None and relationship is not None and relationship["state"] == "bound"
    monkeypatch.setattr(submission_readiness_observation, "observe", lambda *_: {"ready": True})
    submitted = queue.complete(task.id, workspace.worker_id, result={"value": 7, "unit": "?"})
    assert submitted.status == Status.SUBMITTED
    feedback = {"field": "unit", "expected": "seconds"}
    rejected = queue.reject_submission(
        task.id, reason="wrong measurement unit", feedback=feedback,
        expected_generation=submitted.generation,
        expected_owner_session_id=submitted.owner_session_id,
        expected_completed_by=submitted.completed_by, expected_updated_at=submitted.updated_at,
    )
    assert rejected.event_type == "task.rejected" and rejected.task.goal == objective
    assert (rejected.task.owner, rejected.task.owner_session_id) == (workspace.worker_id, "native")
    assert queue.task_relationship(task.id) == relationship
    assert queue.task_pool_assignment(task.id) == placement
    revised = queue.complete(task.id, workspace.worker_id, result={"value": 7, "unit": "seconds"})
    assert revised.status == Status.SUBMITTED
    before = read_snapshot(queue, task.id)
    assert before is not None and before["release_outcome"] is None
    assert queue.confirm(task.id, actor="units").status == Status.COMPLETED
    after = read_snapshot(queue, task.id)
    assert after is not None and after["release_outcome"] == "completed"


def test_provisional_observation_keeps_driver_bound_and_obsolete_goal_fenced(ready: Ready) -> None:
    queue, owner, _, task, calls = ready
    original = queue.task_relationship(task.id)
    assert original is not None and original["state"] == "bound"
    placement = queue.task_pool_assignment(task.id)
    assert placement is not None
    with transport.api(ready) as api:
        discovery = api.get(transport.path(ready, "/observe-provisional"),
                            headers=transport.headers(ready))
        assert discovery.json()["result"]["provisional_observation_versions"] == [1]
        for _ in range(2):
            response = api.post(transport.path(ready, "/observe-provisional"),
                                headers=transport.headers(ready), json=transport.body(ready))
            assert response.status_code == 200, response.text
            assert response.json()["result"] == {
                "ready": True, "assignment_event_id": original["request"]["event_id"],
                "request_id": "closed-source", "generation": placement.task_generation,
            }
    assert len(calls) == 2 and not any(op == "close" for op, _ in owner.calls)
    assert queue.task_relationship(task.id) == original
    assert queue.submission_preparation(task.id) is None
    assert queue.relevance_snapshot(task.id)["closeout"]["readiness"] is None
    with queue._connect() as conn:
        require_relationship(conn, task.id)
        with pytest.raises(TaskError, match="requires owner closeout"):
            require_execution_admission(conn, task.id)


def test_unresolved_closeout_send_blocks_preview_before_owner_read(ready: Ready) -> None:
    queue, _, _, task, calls = ready
    wake = queue.claim_due_wake()
    assert wake is not None
    assert queue.admit_execution_wake(wake, provenance=None).send_required
    with transport.api(ready) as api:
        response = api.post(transport.path(ready, "/observe-provisional"),
                            headers=transport.headers(ready), json=transport.body(ready))
    assert response.status_code == 409 and "unresolved" in response.text
    assert not calls and queue.relevance_snapshot(task.id)["closeout"]["readiness"] is None

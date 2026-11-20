"""Logical placement survives worker suspension and finalized-review submission."""

import argparse
import json
from collections.abc import Callable
from dataclasses import asdict
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from agent_dispatch import __main__ as cli
from agent_dispatch.assignment_workspace import AssignmentWorkspace
from agent_dispatch.coordinator_execution_wakes import deliver_execution_wake
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_common import Task
from agent_dispatch.queue_records import TaskError
from agent_dispatch.review_closeout_submission import SUBMIT
from agent_dispatch.task_lifecycle_cli import _cmd_claimant
from tests import test_review_closeout_submission as review
from tests import test_task_relevance_closeout_transport as transport
from tests.test_assignment_relationships import start_managed
from tests.test_pool_placement import place, pool, publish
from tests.test_pool_placement import task as new_task
from tests.test_pool_placement_client import connected
from tests.test_task_relevance import report
from tests.test_task_relevance_binding import Assignment, _scope
from tests.test_task_relevance_closeout_readiness import Ready

managed_assignment = review.managed_assignment
assignment = review.assignment
ready = review.ready
finalized = review.finalized


start_worker = cast(Callable[[TaskQueue, AssignmentWorkspace, Task], Task], start_managed)


def claimant(
    api: TestClient, task_id: str, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> dict[str, Any]:
    monkeypatch.setattr(cli, "_client", lambda _: connected(api, [], control=False))
    assert _cmd_claimant(argparse.Namespace(task_id=task_id)) == 0
    result = json.loads(capsys.readouterr().out)
    assert isinstance(result, dict)
    return result


def cool(queue: TaskQueue, key: str) -> None:
    cooling = queue.claim_spawn_cooling(
        key, startup_revision=1, session_handle="native", state="spawned",
    )
    cold = queue.record_cold(key, startup_revision=1, cooling_token=cooling.cooling_token)
    assert cold.session_handle == "native" and not cold.conversation_retired


def test_pool_claim_before_workspace_does_not_confirm_a_target_pin(
    assignment: Assignment, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    queue, _, _, _ = assignment
    publish(queue, pool())
    task = new_task(queue, target_worktree="pin")
    place(queue, task)
    with transport.api((*assignment, [])) as api:
        with connected(api, [], control=False) as client:
            placement = client.get(task.id)["task_obligation"]["pool_placement"]
        found = claimant(api, task.id, monkeypatch, capsys)
    assert placement["workspace"] is None
    assert found["pool_assignment"] == placement["assignment"]
    assert not found["claimed"]
    assert (found["resolved_from"], found["worktree"]) == ("target", "pin")


def test_suspended_pool_placement_is_not_a_slot(
    assignment: Assignment, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    queue, _, workspace, task = assignment
    start_worker(queue, workspace, task)
    original = queue.task_pool_assignment(task.id)
    assert original is not None
    queue.suspend(task.id, workspace.worker_id, reason="awaiting update", cooldown_seconds=None)
    waiting = new_task(queue)
    with pytest.raises(TaskError, match="capacity"):
        place(queue, waiting)
    cool(queue, original.reservation_key)
    other, _ = place(queue, waiting)
    assert other.key != original.reservation_key
    assert queue.task_pool_assignment(task.id) == original
    relationship = queue.task_relationship(task.id)
    assert relationship is not None and relationship["request"]["workspace"] == asdict(workspace)
    with transport.api((*assignment, [])) as api:
        found = claimant(api, task.id, monkeypatch, capsys)
        queue.release_suspended(task.id, workspace.worker_id, reason="explicit reassignment")
        released = claimant(api, task.id, monkeypatch, capsys)
    assert found["status"] == "suspended" and found["owner_session_id"] == "native"
    assert found["worktree"] == workspace.worktree and found["resolved_from"] == "assignment"
    assert found["pool_assignment"] == {
        "registration_id": original.registration_id,
        "machine": original.machine, "env": original.env,
    }
    assert released["status"] == "queued" and released["worktree"] is None
    assert released["pool_assignment"] is None
    history = queue.task_relationship(task.id)
    assert history is not None
    assert history["request"]["reservation_key"] == original.reservation_key


@pytest.mark.parametrize("closed", [False, True], ids=["update", "closure"])
def test_original_assignment_addresses_cold_wake(assignment: Assignment, closed: bool) -> None:
    queue, _, workspace, task = assignment
    start_worker(queue, workspace, task)
    placement = queue.task_pool_assignment(task.id)
    assert placement is not None
    original = queue.task_relationship(task.id)
    queue.suspend(task.id, workspace.worker_id, reason="awaiting update", cooldown_seconds=None)
    cool(queue, placement.reservation_key)
    if closed:
        cast(Callable[..., object], report)(queue, task.id, scope=_scope(queue, task.id))
    else:
        queue.resume(task.id, workspace.worker_id, wake_requested=True)
    wake = queue.claim_due_wake()
    assert wake is not None and wake.delivery_token is not None
    assert (wake.owner, wake.owner_session_id) == (workspace.worker_id, "native")
    assert deliver_execution_wake(queue, wake, None, lambda: True)
    queue.finish_wake(wake.id, wake.delivery_token, delivered=True)
    reservation = queue.get_reservation(placement.reservation_key)
    assert reservation is not None and reservation.state == "spawned"
    current = queue.get(task.id)
    assert current is not None and current.status == "suspended"
    if not closed:
        started = queue.start(
            task.id, workspace.worker_id, owner_session_id="native",
            expected_owner_session_id=wake.owner_session_id,
            expected_generation=placement.task_generation,
            pool_recovery_revision=wake.pool_recovery_revision,
        )
        assert started.status == "started"
    assert queue.task_pool_assignment(task.id) == placement
    assert queue.task_relationship(task.id) == original


def test_finalized_review_remains_assigned_during_evaluation(
    finalized: Ready, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    queue, _, workspace, task, _ = finalized
    original = queue.task_pool_assignment(task.id)
    assert original is not None
    with transport.api(finalized) as api:
        headers = {**transport.headers(finalized), "X-Dispatch-Closeout-Action": SUBMIT}
        response = api.post(transport.path(finalized, "/finish"), headers=headers,
                            json={**transport.body(finalized), "finish_action": SUBMIT})
        assert response.status_code == 200, response.text
        current = queue.get(task.id)
        assert current is not None and current.owner is None
        assert queue.task_pool_assignment(task.id) == original
        relationship = queue.task_relationship(task.id)
        assert relationship is not None and relationship["state"] == "closed"
        assert relationship["request"]["workspace"] == asdict(workspace)
        found = claimant(api, task.id, monkeypatch, capsys)
        assert found["status"] == "submitted" and found["owner_session_id"] == "native"
        assert not found["claimed"] and found["resolved_from"] == "assignment"
        assert (found["machine"], found["worktree"]) == (workspace.machine, workspace.worktree)
        assert found["pool_assignment"] == {
            "registration_id": original.registration_id,
            "machine": original.machine, "env": original.env,
        }
        queue.confirm(task.id, actor="fixture-evaluator")
        terminal = claimant(api, task.id, monkeypatch, capsys)
        assert terminal["worktree"] is None and not terminal.get("pool_assignment")
        history = queue.task_relationship(task.id)
        assert history is not None and history["request"] == relationship["request"]

"""Readiness settles the original claim without retiring its conversation."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch import __main__ as core
from agent_dispatch.assignment_workspace import AssignmentWorkspace
from agent_dispatch.client import DispatchClient
from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import Task, TaskError, TaskQueue
from agent_dispatch.task_obligations import read_snapshot, valid_snapshot
from tests import test_task_relevance_binding as bindings
from tests.test_assignment_relationships import GroundOwner, start_managed
from tests.test_task_relevance import report

assignment = bindings.assignment
managed_assignment = bindings.managed_assignment
Ready = tuple[TaskQueue, GroundOwner, AssignmentWorkspace, Task, list[tuple[dict, dict, str]]]


def prepare(queue: TaskQueue, workspace: AssignmentWorkspace, task: Task) -> dict:
    return queue.prepare_relevance_closeout(
        task.id, request_id="closed-source", worker_id=workspace.worker_id,
        native_id="native", expected_generation=queue.get(task.id).generation,
    )


@pytest.fixture
def ready(assignment: bindings.Assignment, monkeypatch: pytest.MonkeyPatch) -> Ready:
    from agent_dispatch import submission_readiness_observation

    queue, owner, workspace, task = assignment
    start_managed(queue, workspace, task)
    report(queue, task.id, scope=bindings._scope(queue, task.id))
    calls: list[tuple[dict, dict, str]] = []

    def observe(request: dict, allocation: dict, claim_ref: str) -> dict:
        # A concurrent reader/writer must not encounter a queue transaction held over IPC.
        with queue._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
        calls.append((deepcopy(request), deepcopy(allocation), claim_ref))
        return {"ready": True, "finalized": False, "process_retirement": False}

    monkeypatch.setattr(submission_readiness_observation, "observe", observe)
    return queue, owner, workspace, task, calls


def test_readiness_closes_only_original_claim_while_task_and_native_stay_retained(
    ready: Ready,
) -> None:
    queue, owner, workspace, task, calls = ready
    before_task = queue.get(task.id)
    original = queue.task_relationship(task.id)
    before_reservation = queue.get_reservation(original["request"]["reservation_key"])
    result = prepare(queue, workspace, task)
    assert result["relationship"] == {
        "event_id": original["request"]["event_id"], "state": "closed",
    }
    assert result["receipt"]["observation"]["ready"] is True
    assert queue.task_relationship(task.id)["state"] == "closed"
    assert queue.get(task.id) == before_task
    assert queue.get_reservation(before_reservation.key) == before_reservation
    assert not queue.get_reservation(before_reservation.key).release_requested
    value = read_snapshot(queue, task.id)
    assert valid_snapshot(value, task_id=task.id)
    assert value["release_outcome"] == "relevance_closeout_ready"
    assert value["bindings"]["native"] and value["bindings"]["spawn_reservation"]
    assert len(calls) == 1
    assert calls[0][0]["owner_session_id"] == "native"
    assert calls[0][2] == original["receipt"]["ref"]
    assert [operation for operation, _ in owner.calls].count("close") == 1
    with pytest.raises(TaskError, match=r"closeout|relationship protection"):
        queue.start(task.id, workspace.worker_id, owner_session_id="native")


def test_restart_and_lost_close_reply_converge_without_reobserving_or_releasing_native(
    ready: Ready,
) -> None:
    queue, owner, workspace, task, calls = ready
    owner.lose_reply = "close"
    first = prepare(queue, workspace, task)
    assert first["relationship"]["state"] == "retained"
    restarted = TaskQueue(queue.db_path, relationship_writer=owner, relationship_authority="local")
    owner.queue = restarted
    second = prepare(restarted, workspace, task)
    assert second["relationship"]["state"] == "closed"
    assert second["receipt"] == first["receipt"]
    assert len(calls) == 1
    assert restarted.get(task.id).status == "started"
    key = restarted.task_relationship(task.id)["request"]["reservation_key"]
    assert not restarted.get_reservation(key).release_requested


@pytest.mark.parametrize("change", ["hold", "generation", "native", "reservation", "relationship"])
def test_changed_incarnation_during_independent_read_retains_claim(
    ready: Ready, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    from agent_dispatch import submission_readiness_observation

    queue, _, workspace, task, calls = ready
    observe = submission_readiness_observation.observe

    def interleaved(*args: object) -> dict:
        result = observe(*args)
        with queue._connect() as conn:
            if change == "hold":
                conn.execute("UPDATE tasks SET hold_reason='operator' WHERE id=?", (task.id,))
            elif change == "generation":
                conn.execute(
                    "UPDATE task_relevance_closeouts SET generation=generation+1 WHERE task_id=?",
                    (task.id,),
                )
            elif change == "native":
                conn.execute("UPDATE tasks SET owner_session_id='foreign' WHERE id=?", (task.id,))
            elif change == "reservation":
                conn.execute(
                    "UPDATE spawn_reservations SET release_requested=1 WHERE task_id=?",
                    (task.id,),
                )
            else:
                conn.execute(
                    "UPDATE task_events SET task_relationship=json_set(task_relationship,"
                    " '$.state','pending') WHERE task_id=? AND task_relationship IS NOT NULL",
                    (task.id,),
                )
        return result

    monkeypatch.setattr(submission_readiness_observation, "observe", interleaved)
    with pytest.raises(TaskError, match="closeout"):
        prepare(queue, workspace, task)
    assert queue.task_relationship(task.id)["state"] != "closed"
    assert read_snapshot(queue, task.id)["release_outcome"] is None
    assert len(calls) == 1


def test_readiness_failure_is_not_an_acknowledgement(
    ready: Ready, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_dispatch import submission_readiness_observation

    queue, _, workspace, task, _ = ready

    def unavailable(*_: object) -> dict:
        raise TaskError("open PR retains custody")

    monkeypatch.setattr(submission_readiness_observation, "observe", unavailable)
    with pytest.raises(TaskError, match="open PR"):
        prepare(queue, workspace, task)
    assert queue.task_relationship(task.id)["state"] == "bound"
    assert read_snapshot(queue, task.id)["release_outcome"] is None


@pytest.mark.parametrize("field,value", [
    ("request_id", "foreign"), ("worker_id", "foreign/worker"),
    ("native_id", "foreign"), ("expected_generation", True),
])
def test_caller_cannot_select_another_closeout_or_native(
    ready: Ready, field: str, value: str | bool,
) -> None:
    queue, _, workspace, task, calls = ready
    arguments = {
        "request_id": "closed-source", "worker_id": workspace.worker_id,
        "native_id": "native", "expected_generation": queue.get(task.id).generation,
    }
    arguments[field] = value
    with pytest.raises(TaskError, match="closeout"):
        queue.prepare_relevance_closeout(task.id, **arguments)
    assert not calls
    assert queue.task_relationship(task.id)["state"] == "bound"


@pytest.mark.parametrize("column,value", [
    ("release_requested", 1), ("conversation_retired", 1), ("native_release_pending", 1),
    ("cooling_token", "cooling"), ("startup_revision", 99),
])
def test_committed_readiness_never_overrides_later_reservation_custody(
    ready: Ready, column: str, value: str | int,
) -> None:
    queue, _, workspace, task, _ = ready
    prepare(queue, workspace, task)
    with queue._connect() as conn:
        conn.execute(
            f"UPDATE spawn_reservations SET {column}=? WHERE task_id=?",  # noqa: S608 -- fixed test columns
            (value, task.id),
        )
    snapshot = read_snapshot(queue, task.id)
    assert snapshot["release_outcome"] is None and snapshot["closeout"] is None
    with pytest.raises(TaskError):
        prepare(queue, workspace, task)


def test_existing_exact_assignment_callback_carries_readiness_and_refuses_prior_evidence(
    ready: Ready, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    queue, _, workspace, task, _ = ready
    original = queue.task_relationship(task.id)
    prepare(queue, workspace, task)
    reference = original["receipt"]["ref"].split(":", 1)[1]
    with TestClient(create_app(queue, enable_mcp=False, verification_interval=0)) as api:
        def handle(request: httpx.Request) -> httpx.Response:
            response = api.get(request.url.path, params=dict(request.url.params))
            return httpx.Response(response.status_code, json=response.json())

        transport = httpx.MockTransport(handle)
        with DispatchClient("http://coordinator.invalid", transport=transport) as client:
            @contextmanager
            def selected(*_: object, **options: bool) -> Iterator[DispatchClient]:
                assert options["read_only"] is True and options["ensure"] is False
                yield client

            monkeypatch.setattr(core, "_client", selected)
            assert core.main(["--local", "claim-status", reference]) == 0
    proof = json.loads(capsys.readouterr().out)["relationship_verification"]
    assert proof["retired"] and proof["quiescent"]
    assert proof["subject"] == original["receipt"]["ref"]
    value = read_snapshot(queue, task.id)
    value.pop("closeout")
    assert not valid_snapshot(value, task_id=task.id)
    value["release_outcome"] = None
    assert valid_snapshot(value, task_id=task.id)
    assert read_snapshot(
        queue, task.id, assignment_event_id=original["request"]["event_id"] + 1,
    )["release_outcome"] is None

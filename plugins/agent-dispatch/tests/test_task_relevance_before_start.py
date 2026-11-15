"""A proved original pool body can close out without accepting its obsolete goal."""

from __future__ import annotations

import pytest

from agent_dispatch.queue import TaskError, TaskQueue
from agent_dispatch.task_obligations import read_snapshot
from tests import test_task_relevance_binding as bindings
from tests.test_pool_placement import place, pool, publish
from tests.test_task_relevance import report, request

assignment = bindings.assignment
managed_assignment = bindings.managed_assignment


@pytest.fixture
def before_start(
    assignment: bindings.Assignment,
) -> bindings.Assignment:
    queue, _, workspace, task = assignment
    publish(queue, pool())
    reservation, _ = place(queue, task)
    queue.record_spawn_worktree(
        reservation.key, workspace.worktree, ownership="created",
        execution_machine=workspace.machine, allocation_project=workspace.project,
        worktree_path=workspace.path, driver="cli", creation_nonce=workspace.creation_nonce,
    )
    queue.record_spawn(
        reservation.key, session_handle="native", worktree=workspace.worktree,
        startup_complete=True, startup_revision=reservation.startup_revision,
    )
    return assignment


def test_source_closure_targets_proved_original_body_without_fabricating_start(
    before_start: bindings.Assignment,
) -> None:
    from agent_dispatch.queue_pool_placement import require_pool_start

    queue, owner, workspace, task = before_start
    before = queue.get(task.id)
    relationship = queue.task_relationship(task.id)
    owner_calls = list(owner.calls)
    with queue._connect() as conn:
        assert require_pool_start(
            conn, before, workspace.worker_id, "native", before.generation,
        )
    accepted = report(queue, task.id, scope=bindings._scope(queue, task.id))
    closeout = queue.relevance_snapshot(task.id)["closeout"]
    assert closeout["owner"] == workspace.worker_id
    assert closeout["owner_session_id"] == "native"
    assert closeout["resource_binding"]["state"] == "captured"
    assert accepted["wake_operation_id"] is not None
    [wake] = queue.list_wakes(task.id)
    assert (wake.owner, wake.owner_session_id) == (workspace.worker_id, "native")
    assert queue.get(task.id).status == "claimed"
    assert queue.get(task.id).owner is None
    assert queue.get(task.id).owner_session_id is None
    assert queue.task_relationship(task.id) == relationship
    assert not queue.attachment_history(task.id)
    assert not any(event["to_status"] == "started" for event in queue.events(task.id))
    assert owner.calls == owner_calls
    with pytest.raises(TaskError, match="allocator claim"):
        queue.abandon(task.id, worker_id=workspace.worker_id, permitted=True)


def test_unaccepted_body_can_deliver_prepare_and_acknowledge_without_start_or_native_release(
    before_start: bindings.Assignment, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_dispatch import pool_decline_observation, submission_readiness_observation

    queue, _, workspace, task = before_start
    before_task = queue.get(task.id)
    relationship = queue.task_relationship(task.id)
    reservation = queue.get_reservation(relationship["request"]["reservation_key"])
    report(queue, task.id, scope=bindings._scope(queue, task.id))
    wake = queue.claim_due_wake()
    assert wake is not None
    admission = queue.admit_execution_wake(wake, provenance=None)
    assert admission.send_required is True
    queue.settle_execution_wake(wake, admission, outcome="sent", detail="original closeout")
    assert queue.finish_wake(wake.id, wake.delivery_token, delivered=True)
    observations = []

    def observe_ready(request: dict, allocation: dict, claim_ref: str) -> dict:
        observations.append(("ready", request["owner_session_id"]))
        return {"ready": True, "finalized": False, "process_retirement": False}

    def observe_finalized(task_row: dict, allocation: dict, native: dict) -> dict:
        assert task_row["owner"] is task_row["owner_session_id"] is None
        observations.append(("finalized", native["native_id"]))
        return {
            "operation": "observe-finalized", "creation_nonce": workspace.creation_nonce,
            "status": "finalized", "checkout_retained": True, "process_retirement": False,
        }

    monkeypatch.setattr(submission_readiness_observation, "observe", observe_ready)
    monkeypatch.setattr(pool_decline_observation, "observe", observe_finalized)
    arguments = {
        "request_id": "closed-source", "worker_id": workspace.worker_id,
        "native_id": "native", "expected_generation": before_task.generation,
    }
    with pytest.raises(TaskError, match="closeout"):
        queue.start(
            task.id, workspace.worker_id, owner_session_id="native",
            expected_generation=before_task.generation,
        )
    prepared = queue.prepare_relevance_closeout(task.id, **arguments)
    assert prepared["relationship"]["state"] == "closed"
    assert read_snapshot(queue, task.id)["release_outcome"] == "relevance_closeout_ready"
    result = queue.finish_relevance_closeout(task.id, **arguments)
    assert result.event_type == "task.abandoned"
    assert result.task.status == "abandoned"
    assert result.task.owner is result.task.owner_session_id is None
    assert queue.get_reservation(reservation.key) == reservation
    assert observations == [("ready", "native"), ("finalized", "native")]
    assert queue.finish_relevance_closeout(task.id, **arguments).event_type is None
    assert len(observations) == 2
    assert queue.attachment_history(task.id) == []
    assert not any(event["to_status"] == "started" for event in queue.events(task.id))


@pytest.mark.parametrize(
    ("table", "column", "value"),
    [
        ("spawn_reservations", "startup_complete", 0),
        ("spawn_reservations", "startup_fenced", 1),
        ("spawn_reservations", "release_requested", 1),
        ("spawn_reservations", "conversation_retired", 1),
        ("spawn_reservations", "cooling_token", "pending"),
        ("spawn_reservations", "conclusion_state", "pending"),
        ("spawn_reservations", "worktree_ownership", "targeted"),
        ("task_pool_assignments", "assignment_released", 1),
        ("task_pool_assignments", "exhausted_at", 1),
        ("task_pool_assignments", "task_generation", 999),
    ],
)
def test_unproved_preacceptance_body_remains_pending_without_guessing_an_owner(
    before_start: bindings.Assignment, table: str, column: str, value: str | int,
) -> None:
    queue, _, _, task = before_start
    with queue._connect() as conn:
        conn.execute(
            f"UPDATE {table} SET {column}=? WHERE task_id=?",  # noqa: S608 -- fixed test columns
            (value, task.id),
        )
    report(queue, task.id, scope=bindings._scope(queue, task.id))
    closeout = queue.relevance_snapshot(task.id)["closeout"]
    assert closeout["owner"] is closeout["owner_session_id"] is None
    assert closeout["resource_binding"]["state"] == "unconfirmed"
    assert queue.list_wakes(task.id) == []
    assert queue.get(task.id).status == "claimed"


def test_preacceptance_closeout_replays_original_target_across_coordinator_restart(
    before_start: bindings.Assignment,
) -> None:
    queue, owner, workspace, task = before_start
    arguments = request(queue, task.id, scope=bindings._scope(queue, task.id))
    accepted = queue.reconcile_task_relevance(task.id, **arguments)
    closeout = queue.relevance_snapshot(task.id)["closeout"]
    restarted = TaskQueue(
        queue.db_path, relationship_writer=owner, relationship_authority="local",
    )
    owner.queue = restarted
    assert restarted.reconcile_task_relevance(task.id, **arguments) == accepted
    assert restarted.relevance_snapshot(task.id)["closeout"] == closeout
    [wake] = restarted.list_wakes(task.id)
    assert (wake.owner, wake.owner_session_id) == (workspace.worker_id, "native")
    assert restarted.get(task.id).owner is restarted.get(task.id).owner_session_id is None


def test_scoped_original_body_can_use_closeout_http_without_generic_task_authority(
    before_start: bindings.Assignment, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_dispatch import pool_decline_observation, submission_readiness_observation
    from tests import test_task_relevance_closeout_transport as transport

    queue, owner, workspace, task = before_start
    report(queue, task.id, scope=bindings._scope(queue, task.id))
    monkeypatch.setattr(
        submission_readiness_observation, "observe",
        lambda *_: {"ready": True, "finalized": False, "process_retirement": False},
    )
    monkeypatch.setattr(
        pool_decline_observation, "observe",
        lambda *_: {
            "operation": "observe-finalized", "creation_nonce": workspace.creation_nonce,
            "status": "finalized", "checkout_retained": True, "process_retirement": False,
        },
    )
    value = (queue, owner, workspace, task, [])
    client, auth = transport.api(value), transport.headers(value)
    assert client.get(f"/tasks/{task.id}", headers=auth).status_code == 403
    response = client.get(transport.path(value), headers=auth)
    assert response.status_code == 200, response.text
    assert response.json()["result"]["resource_binding"]["owner_phase"] == "before_start"
    response = client.post(
        transport.path(value, "/prepare"), headers=auth, json=transport.body(value),
    )
    assert response.status_code == 200, response.text
    response = client.post(
        transport.path(value, "/finish"), headers=auth, json=transport.body(value),
    )
    assert response.status_code == 200, response.text
    assert set(response.json()["result"]) == {"id", "status", "generation"}
    assert response.json()["result"]["status"] == "abandoned"
    assert queue.get(task.id).owner_session_id is None
    assert queue.attachment_history(task.id) == []


@pytest.mark.parametrize("change", ["hold", "retirement", "missing-phase"])
def test_later_custody_changes_do_not_inherit_before_start_readiness(
    before_start: bindings.Assignment, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    from agent_dispatch import submission_readiness_observation

    queue, _, workspace, task = before_start
    report(queue, task.id, scope=bindings._scope(queue, task.id))
    if change == "hold":
        queue.set_hold(task.id, reason="independent hold", actor="operator")
    else:
        with queue._connect() as conn:
            if change == "retirement":
                conn.execute(
                    "UPDATE spawn_reservations SET native_release_pending=1 WHERE task_id=?",
                    (task.id,),
                )
            else:
                conn.execute(
                    "UPDATE task_relevance_closeouts SET resource_binding="
                    "json_remove(resource_binding,'$.owner_phase') WHERE task_id=?", (task.id,),
                )
    monkeypatch.setattr(
        submission_readiness_observation, "observe",
        lambda *_: pytest.fail("changed original custody reached independent readiness"),
    )
    with pytest.raises(TaskError, match="closeout"):
        queue.prepare_relevance_closeout(
            task.id, request_id="closed-source", worker_id=workspace.worker_id,
            native_id="native", expected_generation=queue.get(task.id).generation,
        )
    assert queue.task_relationship(task.id)["state"] == "bound"

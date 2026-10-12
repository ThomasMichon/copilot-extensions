"""Routing assignment provenance and API tests."""

from __future__ import annotations

import sqlite3
from dataclasses import asdict

import pytest
from fastapi.testclient import TestClient

from agent_dispatch.coordinator import create_app
from agent_dispatch.queue import TaskError, spawn_key
from tests._helpers import TEST_REPO
from tests._helpers import RepoDefaultingQueue as TaskQueue


@pytest.fixture
def q(tmp_path):
    return TaskQueue(tmp_path / "tasks.db")


def _reservation(q, title: str = "work"):
    task = q.create(title)
    reservation, reserved = q.reserve_spawn(task.id)
    assert reserved is True
    return task, reservation


def _assignment(**overrides):
    value = {
        "purpose": "coding",
        "selected_model": "example-code-model",
        "eligibility_state": "demonstrated",
        "selection_reason": "lowest-cost-demonstrated",
        "execution_surface": "restricted-container",
        "decision_ref": "decision:sha256:abc123",
        "containment_profile_ref": "restricted-code",
        "coordinator_session_ref": "session:coordinator-1",
    }
    value.update(overrides)
    return value


def test_record_assignment_is_idempotent_and_immutable(q):
    task, reservation = _reservation(q)

    first, created = q.record_routing_assignment(
        reservation.key,
        _assignment(),
        now=10,
    )
    repeated, repeated_created = q.record_routing_assignment(
        reservation.key,
        _assignment(),
        now=20,
    )

    assert created is True
    assert repeated_created is False
    assert repeated == first
    assert first.assignment_id == spawn_key(task.id, 1)
    assert first.state == "assigned"
    with pytest.raises(TaskError, match="different facts"):
        q.record_routing_assignment(
            reservation.key,
            _assignment(selected_model="different-model"),
        )


def test_candidate_assignment_requires_trial_reference(q):
    _task, reservation = _reservation(q)

    with pytest.raises(TaskError, match="require trial_ref"):
        q.record_routing_assignment(
            reservation.key,
            _assignment(
                eligibility_state="candidate",
                selected_model="candidate-model",
            ),
        )

    recorded, _ = q.record_routing_assignment(
        reservation.key,
        _assignment(
            eligibility_state="candidate",
            selected_model="candidate-model",
            trial_ref="trial:example-1",
        ),
    )
    assert recorded.trial_ref == "trial:example-1"


def test_new_assignment_requires_reserving_state(q):
    _task, reservation = _reservation(q)
    q.fail_spawn(reservation.key)

    with pytest.raises(TaskError, match="requires a reserving"):
        q.record_routing_assignment(reservation.key, _assignment())

    _task2, active = _reservation(q, "active")
    recorded, _ = q.record_routing_assignment(active.key, _assignment())
    q.record_spawn(active.key, session_handle="session:worker")
    repeated, created = q.record_routing_assignment(active.key, _assignment())
    assert created is False
    assert repeated == recorded


def test_absolute_paths_are_not_public_safe_tokens(q):
    _task, reservation = _reservation(q)

    with pytest.raises(TaskError, match="must not be an absolute path"):
        q.record_routing_assignment(
            reservation.key,
            _assignment(decision_ref="C:/Users/example/private/decision"),
        )


def test_repair_is_a_separate_linked_assignment(q):
    _task, original_reservation = _reservation(q, "original")
    original, _ = q.record_routing_assignment(
        original_reservation.key,
        _assignment(),
    )
    q.fail_spawn(original_reservation.key)
    _task2, repair_reservation = _reservation(q, "repair")

    repair, _ = q.record_routing_assignment(
        repair_reservation.key,
        _assignment(parent_assignment_id=original.assignment_id),
    )

    assert repair.parent_assignment_id == original.assignment_id
    assert repair.assignment_id != original.assignment_id


def test_assignment_lifecycle_and_terminal_compare_and_set(q):
    _task, reservation = _reservation(q)
    assignment, _ = q.record_routing_assignment(reservation.key, _assignment())

    admitted, changed = q.transition_routing_assignment(
        assignment.assignment_id,
        "admitted",
        "supervisor",
        now=20,
    )
    assert changed is True
    assert admitted.state == "admitted"
    launched, _ = q.transition_routing_assignment(
        assignment.assignment_id,
        "launched",
        "supervisor",
        worker_session_ref="session:worker-1",
        now=30,
    )
    assert launched.worker_session_ref == "session:worker-1"
    q.transition_routing_assignment(
        assignment.assignment_id,
        "running",
        "worker",
        now=40,
    )
    terminal, _ = q.transition_routing_assignment(
        assignment.assignment_id,
        "terminal",
        "evaluator",
        terminal_disposition="accepted",
        reason_code="product-gate-passed",
        now=50,
    )
    assert terminal.state == "terminal"
    assert terminal.terminal_disposition == "accepted"

    repeated, repeated_changed = q.transition_routing_assignment(
        assignment.assignment_id,
        "terminal",
        "evaluator",
        terminal_disposition="accepted",
        reason_code="product-gate-passed",
        now=60,
    )
    assert repeated_changed is False
    assert repeated == terminal
    with pytest.raises(TaskError, match="conflicting terminal"):
        q.transition_routing_assignment(
            assignment.assignment_id,
            "terminal",
            "evaluator",
            terminal_disposition="rejected",
            reason_code="review-failed",
        )


def test_invalid_lifecycle_transition_is_rejected(q):
    _task, reservation = _reservation(q)
    assignment, _ = q.record_routing_assignment(reservation.key, _assignment())

    with pytest.raises(TaskError, match="cannot transition"):
        q.transition_routing_assignment(
            assignment.assignment_id,
            "running",
            "worker",
        )


def test_denial_is_terminal(q):
    _task, reservation = _reservation(q)
    assignment, _ = q.record_routing_assignment(reservation.key, _assignment())

    denied, _ = q.transition_routing_assignment(
        assignment.assignment_id,
        "denied",
        "supervisor",
        reason_code="trial-not-armed",
    )

    assert denied.state == "terminal"
    assert denied.terminal_disposition == "denied"
    with pytest.raises(TaskError, match="cannot transition"):
        q.transition_routing_assignment(
            assignment.assignment_id,
            "launched",
            "supervisor",
        )


def test_denial_after_admission_or_launch_is_rejected(q):
    _task, reservation = _reservation(q)
    assignment, _ = q.record_routing_assignment(reservation.key, _assignment())
    q.transition_routing_assignment(
        assignment.assignment_id,
        "admitted",
        "supervisor",
    )
    q.transition_routing_assignment(
        assignment.assignment_id,
        "launched",
        "supervisor",
    )
    q.transition_routing_assignment(
        assignment.assignment_id,
        "running",
        "worker",
    )

    with pytest.raises(TaskError, match="only before admission"):
        q.transition_routing_assignment(
            assignment.assignment_id,
            "denied",
            "supervisor",
        )


def test_provider_billing_reference_is_globally_unique(q):
    _task, first_reservation = _reservation(q, "first")
    first, _ = q.record_routing_assignment(first_reservation.key, _assignment())
    q.fail_spawn(first_reservation.key)
    _task2, second_reservation = _reservation(q, "second")
    second, _ = q.record_routing_assignment(second_reservation.key, _assignment())

    assert q.record_routing_billing_ref(
        first.assignment_id,
        "billing:event-1",
        "example-provider",
        "opaque:event-1",
        "worker",
        occurred_at=25,
    )
    assert not q.record_routing_billing_ref(
        first.assignment_id,
        "billing:event-1",
        "example-provider",
        "opaque:event-1",
        "worker",
        occurred_at=25,
    )
    with pytest.raises(TaskError, match="already assigned"):
        q.record_routing_billing_ref(
            second.assignment_id,
            "billing:event-2",
            "example-provider",
            "opaque:event-1",
            "worker",
        )


def test_billing_event_id_cannot_block_lifecycle_event(q):
    _task, reservation = _reservation(q)
    assignment, _ = q.record_routing_assignment(reservation.key, _assignment())
    caller_event_id = f"{assignment.assignment_id}:admitted"

    q.record_routing_billing_ref(
        assignment.assignment_id,
        caller_event_id,
        "example-provider",
        "opaque:collision-test",
        "worker",
        occurred_at=10,
    )
    admitted, changed = q.transition_routing_assignment(
        assignment.assignment_id,
        "admitted",
        "supervisor",
        now=10,
    )

    assert changed is True
    assert admitted.state == "admitted"
    events = q.routing_assignment_events(assignment.assignment_id)
    assert [event["event_type"] for event in events] == [
        "assigned",
        "billing-linked",
        "admitted",
    ]


def test_equal_timestamp_lifecycle_events_keep_append_order(q):
    _task, reservation = _reservation(q)
    assignment, _ = q.record_routing_assignment(
        reservation.key,
        _assignment(),
        now=10,
    )
    q.transition_routing_assignment(
        assignment.assignment_id,
        "admitted",
        "supervisor",
        now=10,
    )

    events = q.routing_assignment_events(assignment.assignment_id)
    assert [event["event_type"] for event in events] == ["assigned", "admitted"]


def test_list_and_events_are_bounded_public_safe_records(q):
    task, reservation = _reservation(q)
    assignment, _ = q.record_routing_assignment(reservation.key, _assignment())

    listed = q.list_routing_assignments(task_id=task.id, limit=1)
    assert listed == [assignment]
    events = q.routing_assignment_events(assignment.assignment_id)
    assert [event["event_type"] for event in events] == ["assigned"]
    serialized = asdict(assignment)
    assert "prompt" not in serialized
    assert "payload" not in serialized
    assert "cost" not in serialized


def test_migration_is_additive_for_existing_queue(tmp_path):
    db = tmp_path / "tasks.db"
    q = TaskQueue(db)
    q.create("legacy")

    reopened = TaskQueue(db)

    with sqlite3.connect(db) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "routing_assignments" in tables
    assert "routing_assignment_events" in tables
    assert reopened.list_routing_assignments() == []


@pytest.fixture
def api(tmp_path):
    return TestClient(create_app(TaskQueue(tmp_path / "tasks.db")))


def test_http_routing_provenance_round_trip(api):
    task_id = api.post(
        "/tasks",
        json={"title": "work", "repo": TEST_REPO},
    ).json()["id"]
    reservation = api.post(
        "/spawn-reservations",
        json={"task_id": task_id},
    ).json()["reservation"]
    key = reservation["key"]

    recorded = api.post(
        f"/spawn-reservations/{key}/routing-assignment",
        json=_assignment(),
    )
    assert recorded.status_code == 200
    assert recorded.json()["created"] is True
    admitted = api.post(
        f"/routing-assignments/{key}/transition",
        json={"event_type": "admitted", "actor_role": "supervisor"},
    )
    assert admitted.status_code == 200
    billing = api.post(
        f"/routing-assignments/{key}/billing-ref",
        json={
            "event_id": "billing:http-1",
            "provider": "example-provider",
            "provider_billing_event_ref": "opaque:http-1",
            "actor_role": "worker",
        },
    )
    assert billing.status_code == 200

    assert api.get(f"/routing-assignments/{key}").json()["state"] == "admitted"
    assert len(api.get(f"/routing-assignments/{key}/events").json()) == 3
    assert api.get(
        "/routing-assignments",
        params={"task_id": task_id},
    ).json()[0]["assignment_id"] == key


def test_http_rejects_unknown_assignment_and_extra_fields(api):
    missing = api.get("/routing-assignments/dispatch-task:missing:1")
    assert missing.status_code == 404

    task_id = api.post(
        "/tasks",
        json={"title": "work", "repo": TEST_REPO},
    ).json()["id"]
    key = api.post(
        "/spawn-reservations",
        json={"task_id": task_id},
    ).json()["reservation"]["key"]
    extra = api.post(
        f"/spawn-reservations/{key}/routing-assignment",
        json={**_assignment(), "prompt": "must not be stored"},
    )
    assert extra.status_code == 422

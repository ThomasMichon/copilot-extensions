"""Deterministic accounting only; no process launch or external transport."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier

import pytest

from agent_dispatch.execution_effect_adapter import EffectRevocation, ExecutionScope
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_execution_custody import ExecutionSendIdentity
from agent_dispatch.queue_execution_handoff import ExecutionAdmissionClosed
from agent_dispatch.queue_records import TaskError
from tests._helpers import TEST_REPO


@pytest.fixture
def setup(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    producer = queue.handoff_producer_scope(
        TEST_REPO, "scheduled", producer_id="producer-a", expected_generation=0
    )
    task = queue.create(
        "task",
        repo=TEST_REPO,
        source="scheduled",
        producer_scope={"repo": TEST_REPO, "source": "scheduled"},
        producer_id="producer-a",
        producer_generation=1,
        producer_capability=producer.producer_capability,
        producer_request_id="task",
    )
    reservation, acquired = queue.reserve_spawn(task.id)
    assert acquired
    with queue._connect() as conn:
        coordinator = conn.execute(
            "SELECT identity FROM workspace_queue_identity WHERE singleton = 1"
        ).fetchone()["identity"]
    scope = ExecutionScope("installation-a", coordinator, TEST_REPO, "registration-a")
    identity = ExecutionSendIdentity(
        scope, "send-a", "runtime-a", reservation.key, task.generation, "launch"
    )
    return queue, identity, task


def admit(queue, identity, **kwargs):
    return queue.admit_execution_send(
        identity, source="scheduled", expected_producer_generation=1, **kwargs
    )


def close(queue, identity):
    return queue.close_execution_admission(
        EffectRevocation(identity.scope, "transition-a", 1, "automatic", "manual"),
        source="scheduled",
        expected_producer_generation=1,
    )


def test_lost_reply_is_observed_after_restart_and_closure_not_resent(setup):
    queue, identity, _ = setup
    first = admit(queue, identity, now=10)
    assert first.replayed is False
    assert first.state == "pending"
    close(queue, identity)
    restarted = TaskQueue(queue.db_path)
    replay = admit(restarted, identity, now=20)
    assert replay.replayed is True
    assert replay.created_at == replay.updated_at == 10
    assert replay.identity == identity
    inventory = restarted.execution_closure_inventory(identity.scope)
    assert len(inventory["sends"]) == 1
    assert inventory["sends"][0]["state"] == "pending"
    assert inventory["acknowledged"] is False
    assert inventory["blockers"]


def test_closure_before_send_refuses_and_retains_reservation(setup):
    queue, identity, task = setup
    close(queue, identity)
    with pytest.raises(ExecutionAdmissionClosed):
        admit(queue, identity)
    inventory = queue.execution_closure_inventory(identity.scope)
    assert inventory["sends"] == []
    assert inventory["reservations"][0]["key"] == identity.reservation_key
    assert queue.get(task.id).status == "queued"


@pytest.mark.parametrize("outcome", ["sent", "not_sent"])
def test_ambiguous_transport_outcome_survives_and_requires_explicit_settlement(setup, outcome):
    queue, identity, task = setup
    admit(queue, identity, now=1)
    queue.settle_execution_send(identity, outcome="unknown", detail="response lost", now=2)
    close(queue, identity)
    restarted = TaskQueue(queue.db_path)
    assert admit(restarted, identity).state == "unknown"
    assert (
        restarted.execution_closure_inventory(identity.scope)["sends"][0]["detail"]
        == "response lost"
    )
    settled = restarted.settle_execution_send(
        identity, outcome=outcome, detail="authoritative transport observation", now=3
    )
    assert settled.state == outcome
    replay = restarted.settle_execution_send(
        identity, outcome=outcome, detail="authoritative transport observation", now=4
    )
    assert replay.replayed is True and replay.updated_at == 3
    with pytest.raises(TaskError, match="conflicts with its settled"):
        restarted.settle_execution_send(identity, outcome="unknown", detail="late timeout", now=5)
    assert restarted.get(task.id).status == "queued"
    assert restarted.execution_closure_inventory(identity.scope)["acknowledged"] is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("runtime_id", "runtime-b"),
        ("kind", "deliver"),
        ("task_generation", 2),
    ],
)
def test_send_identity_cannot_be_rebound(setup, field, value):
    queue, identity, _ = setup
    admit(queue, identity)
    changed = replace(identity, **{field: value})
    with pytest.raises(TaskError, match="conflicts with the retained identity"):
        admit(queue, changed)
    with pytest.raises(TaskError, match="no matching retained custody"):
        queue.settle_execution_send(changed, outcome="not_sent", detail="not this sender")


@pytest.mark.parametrize("state", ["pending", "unknown", "sent", "not_sent"])
def test_new_send_id_does_not_replay_an_existing_assignment_stage(setup, state):
    queue, identity, _ = setup
    admit(queue, identity)
    if state != "pending":
        queue.settle_execution_send(identity, outcome=state, detail="transport evidence")
    with pytest.raises(TaskError, match="already has custody"):
        admit(queue, replace(identity, send_id="send-b", runtime_id="runtime-b"))


@pytest.mark.parametrize("field", ["installation", "producer_registration"])
def test_closure_cannot_hide_previously_admitted_custody(setup, field):
    queue, identity, _ = setup
    admit(queue, identity)
    foreign = replace(identity, scope=replace(identity.scope, **{field: "other"}))
    with pytest.raises(TaskError, match="custody conflicts"):
        close(queue, foreign)
    with pytest.raises(TaskError, match="custody conflicts"):
        admit(queue, replace(foreign, send_id="send-b"))
    close(queue, identity)
    assert len(queue.execution_closure_inventory(identity.scope)["sends"]) == 1


@pytest.mark.parametrize("generation", [0, True, 2])
def test_send_requires_exact_producer_generation(setup, generation):
    queue, identity, _ = setup
    with pytest.raises(TaskError):
        queue.admit_execution_send(
            identity, source="scheduled", expected_producer_generation=generation
        )


@pytest.mark.parametrize("change", ["coordinator", "task", "reservation", "release"])
def test_send_requires_current_owned_queue_and_reservation(setup, change):
    queue, identity, task = setup
    if change == "coordinator":
        identity = replace(identity, scope=replace(identity.scope, coordinator="other"))
    elif change == "task":
        identity = replace(identity, task_generation=task.generation + 1)
    elif change == "reservation":
        identity = replace(identity, reservation_key="missing")
    else:
        queue.fail_spawn(identity.reservation_key, detail="failed")
    with pytest.raises(TaskError):
        admit(queue, identity)


def test_send_custody_is_retained_after_terminal_task(setup):
    queue, identity, task = setup
    admit(queue, identity)
    queue.claim_one("worker", task_id=task.id)
    queue.start(task.id, "worker")
    queue.complete(task.id, "worker")
    close(queue, identity)
    inventory = queue.execution_closure_inventory(identity.scope)
    assert inventory["tasks"][0]["status"] == "completed"
    assert inventory["sends"][0]["state"] == "pending"


def test_other_qualified_source_can_admit_while_first_scope_is_closed(setup):
    queue, identity, _ = setup
    admit(queue, identity)
    close(queue, identity)
    producer = queue.handoff_producer_scope(
        TEST_REPO, "other", producer_id="producer-b", expected_generation=0
    )
    task = queue.create(
        "other",
        repo=TEST_REPO,
        source="other",
        producer_scope={"repo": TEST_REPO, "source": "other"},
        producer_id="producer-b",
        producer_generation=1,
        producer_capability=producer.producer_capability,
        producer_request_id="other",
    )
    reservation, _ = queue.reserve_spawn(task.id)
    other = replace(
        identity,
        scope=replace(identity.scope, producer_registration="registration-b"),
        send_id="send-b",
        reservation_key=reservation.key,
        task_generation=task.generation,
    )
    assert (
        queue.admit_execution_send(other, source="other", expected_producer_generation=1).replayed
        is False
    )
    assert len(queue.execution_closure_inventory(identity.scope)["sends"]) == 1


def test_same_qualified_scope_cannot_close_another_source_and_hide_custody(setup):
    queue, identity, _ = setup
    admit(queue, identity)
    queue.handoff_producer_scope(
        TEST_REPO, "other", producer_id="producer-b", expected_generation=0
    )
    with pytest.raises(TaskError, match="custody conflicts"):
        queue.close_execution_admission(
            EffectRevocation(identity.scope, "transition-a", 1, "automatic", "manual"),
            source="other",
            expected_producer_generation=1,
        )
    close(queue, identity)
    assert len(queue.execution_closure_inventory(identity.scope)["sends"]) == 1


@pytest.mark.parametrize("outcome", ["pending", "unknown", "sent", "not_sent"])
def test_producer_rollover_preserves_old_custody_without_blocking_current_scope(setup, outcome):
    queue, identity, _ = setup
    admit(queue, identity)
    if outcome != "pending":
        queue.settle_execution_send(identity, outcome=outcome, detail="transport evidence")
    producer = queue.handoff_producer_scope(
        TEST_REPO, "scheduled", producer_id="producer-b", expected_generation=1
    )
    task = queue.create(
        "new",
        repo=TEST_REPO,
        source="scheduled",
        producer_scope={"repo": TEST_REPO, "source": "scheduled"},
        producer_id="producer-b",
        producer_generation=2,
        producer_capability=producer.producer_capability,
        producer_request_id="new",
    )
    reservation, _ = queue.reserve_spawn(task.id)
    current = replace(
        identity,
        send_id="send-b",
        reservation_key=reservation.key,
        task_generation=task.generation,
    )
    assert (
        queue.admit_execution_send(
            current, source="scheduled", expected_producer_generation=2
        ).replayed
        is False
    )
    queue.close_execution_admission(
        EffectRevocation(identity.scope, "transition-a", 1, "automatic", "manual"),
        source="scheduled",
        expected_producer_generation=2,
    )
    records = queue.execution_closure_inventory(identity.scope)["sends"]
    by_id = {row["send_id"]: row for row in records}
    assert by_id["send-a"]["producer_generation"] == 1
    assert by_id["send-a"]["state"] == outcome
    assert by_id["send-b"]["producer_generation"] == 2
    assert admit(queue, identity).replayed is True
    assert queue.execution_closure_inventory(identity.scope)["acknowledged"] is False


def test_closure_first_without_custody_cannot_rebind_qualified_scope_to_other_source(setup):
    queue, identity, _ = setup
    close(queue, identity)
    producer = queue.handoff_producer_scope(
        TEST_REPO, "other", producer_id="producer-b", expected_generation=0
    )
    task = queue.create(
        "other",
        repo=TEST_REPO,
        source="other",
        producer_scope={"repo": TEST_REPO, "source": "other"},
        producer_id="producer-b",
        producer_generation=1,
        producer_capability=producer.producer_capability,
        producer_request_id="other",
    )
    reservation, _ = queue.reserve_spawn(task.id)
    foreign = replace(
        identity,
        send_id="send-b",
        reservation_key=reservation.key,
        task_generation=task.generation,
    )
    with pytest.raises(TaskError, match="custody conflicts"):
        queue.admit_execution_send(foreign, source="other", expected_producer_generation=1)
    inventory = queue.execution_closure_inventory(identity.scope)
    assert inventory["sends"] == []
    assert all(row["id"] != task.id for row in inventory["tasks"])


def test_close_and_send_have_one_serial_order(setup):
    queue, identity, _ = setup
    gate = Barrier(2)

    def send():
        gate.wait()
        try:
            return admit(queue, identity)
        except ExecutionAdmissionClosed:
            return None

    def revoke():
        gate.wait()
        return close(queue, identity)

    with ThreadPoolExecutor(max_workers=2) as pool:
        sent = pool.submit(send)
        closed = pool.submit(revoke)
        result = sent.result()
        closed.result()
    inventory = queue.execution_closure_inventory(identity.scope)
    assert len(inventory["sends"]) == (0 if result is None else 1)
    assert inventory["acknowledged"] is False


@pytest.mark.parametrize("outcome,detail", [("invalid", "explain"), ("unknown", "")])
def test_invalid_settlement_cannot_erase_pending_send(setup, outcome, detail):
    queue, identity, _ = setup
    admit(queue, identity)
    with pytest.raises(TaskError):
        queue.settle_execution_send(identity, outcome=outcome, detail=detail)
    assert admit(queue, identity).state == "pending"

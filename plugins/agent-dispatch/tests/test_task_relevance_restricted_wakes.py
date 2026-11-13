"""Restricted closeout resolves both target reads against the fenced original worker."""

import json

import pytest

from agent_dispatch import owner_wake_delivery as delivery
from agent_dispatch.coordinator_execution_wakes import deliver_execution_wake
from agent_dispatch.queue import Status, TaskError
from agent_dispatch.queue_relevance import RelevanceScope
from tests.test_restricted_supervisor import rig as rig
from tests.test_task_relevance import request


def closeout(rig, status):
    queue, task_id = rig.queue, rig.task.id
    assert rig.supervisor.poll_once() == [task_id]
    original = queue.get(task_id)
    if status != Status.CLAIMED:
        queue.start(task_id, original.owner, owner_session_id=original.owner_session_id)
    if status == Status.SUSPENDED:
        queue.suspend(task_id, original.owner, reason="waiting")
    elif status == Status.SUBMITTED:
        with queue._connect() as conn:
            conn.execute("UPDATE tasks SET require_verification=1 WHERE id=?", (task_id,))
        queue.complete(task_id, original.owner)
    with queue._connect() as conn:
        conn.execute(
            "UPDATE tasks SET source='emitter', origin_ref='review-source' WHERE id=?", (task_id,),
        )
    snapshot = queue.relevance_snapshot(task_id)
    queue.reconcile_task_relevance(task_id, **request(
        queue, task_id, scope=RelevanceScope(snapshot["repo"], snapshot["source"], snapshot["origin_ref"]),
    ))
    return queue.claim_due_wake(), original


@pytest.mark.parametrize("status", [
    Status.CLAIMED, Status.STARTED, Status.SUSPENDED, Status.SUBMITTED,
])
def test_restricted_closeout_uses_fenced_worker_without_reassigning_task(rig, monkeypatch, status):
    queue = rig.queue
    wake, original = closeout(rig, status)
    sent = []

    def send(bridge, native, prompt, **kwargs):
        sent.append((bridge, native, prompt, kwargs))
        assert queue.list_wakes(wake.task_id)[-1].execution_send_state == "pending"
        return True

    monkeypatch.setattr(delivery, "native_prompt", send)
    assert deliver_execution_wake(queue, wake, None, lambda: delivery.deliver_owner_wake(
        queue, wake.owner, wake.owner_session_id, wake.task_id, wake.message, wake.id,
    ))
    assert sent[0][:2] == ("bridge-worker", "native-worker")
    assert sent[0][3]["idempotency_key"] == wake.id
    assert "do not start or resume the goal" in sent[0][2]
    current = queue.get(wake.task_id)
    assert current.status == status
    assert current.owner == (None if status == Status.SUBMITTED else original.owner)
    assert current.owner_session_id == original.owner_session_id
    assert queue.take_steer(wake.task_id, original.owner)["fields"]["task_relevance"]


def test_unassigned_restricted_closeout_keeps_captured_native_on_both_target_reads(rig, monkeypatch):
    queue = rig.queue
    wake, original = closeout(rig, Status.STARTED)
    queue.yield_task(wake.task_id, original.owner, release_spawn=False)
    sent = []
    monkeypatch.setattr(
        delivery, "native_prompt",
        lambda bridge, native, *_args, **kwargs: sent.append((bridge, native, kwargs)) or True,
    )
    assert deliver_execution_wake(queue, wake, None, lambda: delivery.deliver_owner_wake(
        queue, wake.owner, wake.owner_session_id, wake.task_id, wake.message, wake.id,
    ))
    queue.finish_wake(wake.id, wake.delivery_token, delivered=True)
    assert sent[0][:2] == ("bridge-worker", "native-worker")
    assert sent[0][2]["idempotency_key"] == wake.id
    assert queue.list_wakes(wake.task_id)[-1].execution_send_state == "sent"
    assert queue.take_steer(wake.task_id, original.owner)["fields"]["task_relevance"]
    current = queue.get(wake.task_id)
    assert current.status == Status.QUEUED
    assert current.owner is current.owner_session_id is current.lease_expires_at is None


@pytest.mark.parametrize("field,value", [
    ("worker_id", "another-worker"), ("native_session_id", "another-native"), ("task_generation", 999),
])
@pytest.mark.parametrize("unassigned", [False, True])
def test_submitted_closeout_cannot_bypass_restricted_receipt_identity(
    rig, monkeypatch, field, value, unassigned,
):
    queue = rig.queue
    wake, original = closeout(rig, Status.STARTED if unassigned else Status.SUBMITTED)
    if unassigned:
        queue.yield_task(wake.task_id, original.owner, release_spawn=False)
    reservation = queue.latest_reservation(wake.task_id)
    info = {**reservation.agent_info, field: value}
    with queue._connect() as conn:
        conn.execute(
            "UPDATE spawn_reservations SET agent_info=? WHERE key=?",
            (json.dumps(info), reservation.key),
        )
    monkeypatch.setattr(delivery, "native_prompt", lambda *_args, **_kwargs: pytest.fail("stale send"))
    with pytest.raises(TaskError, match="restricted wake"):
        delivery.deliver_owner_wake(
            queue, wake.owner, wake.owner_session_id, wake.task_id, wake.message, wake.id,
        )
    assert queue.get(wake.task_id).owner is None


def test_submitted_worker_drift_between_target_reads_prevents_transport(rig, monkeypatch):
    queue = rig.queue
    wake, _original = closeout(rig, Status.SUBMITTED)
    target_identity = delivery._target_identity
    calls = []

    def target(*args):
        result = target_identity(*args)
        calls.append(result)
        with queue._connect() as conn:
            conn.execute("UPDATE tasks SET completed_by='another-worker' WHERE id=?", (wake.task_id,))
        return result

    monkeypatch.setattr(delivery, "_target_identity", target)
    monkeypatch.setattr(delivery, "native_prompt", lambda *_args, **_kwargs: pytest.fail("stale send"))
    with pytest.raises(TaskError, match="original linked owner wake"):
        delivery.deliver_owner_wake(
            queue, wake.owner, wake.owner_session_id, wake.task_id, wake.message, wake.id,
        )
    assert calls == [("bridge-worker", "native-worker")]
    assert queue.get(wake.task_id).owner is None

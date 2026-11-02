"""Reconciliation observes the original provider; missing history never becomes absence."""

import json
import subprocess

import pytest

from agent_dispatch import coordinator_execution_sender as sender
from agent_dispatch.queue import TaskError
from agent_dispatch.queue_execution_wakes import wake_table
from agent_dispatch.task_wake_recovery import reconcile_wake
from agent_dispatch.bridge_command_delivery import native_prompt_receipt
from tests.test_execution_wake_sends import _qualify
from tests.test_native_owner_wake import hosted as hosted_fixture, NATIVE, BRIDGE, OWNER

hosted = hosted_fixture


@pytest.fixture(params=["owner", "waiter"])
def retained(hosted, monkeypatch, request):
    queue, task_id = hosted
    if request.param == "owner":
        queue.resume(task_id, OWNER, wake_requested=True, wake_message="original prompt")
    else:
        waiter = queue.register_run_waiter(
            task_id, pid=123, host="host-a", start_token="waiter-start",
            resume_worktree=OWNER, command=["synthetic-wait"],
        )
        queue.recover_dead_run_waiter(
            task_id, generation=waiter["generation"], reason="external",
            message="original prompt", sender="recovery",
        )
    _qualify(queue, queue.get(task_id), monkeypatch)
    wake = queue.claim_due_wake() if request.param == "owner" else queue.claim_due_run_waiter_wake()
    provenance = sender.CoordinatorExecutionProvenance(
        queue, queue.get(task_id), "application-a",
    ).prepare()
    admission = queue.admit_execution_wake(wake, provenance=provenance)
    queue.settle_execution_wake(wake, admission, outcome="unknown", detail="response lost")
    return queue, task_id, (
        queue.list_wakes(task_id)[0] if request.param == "owner"
        else queue.list_run_waiter_wakes(task_id)[0]
    )


def current(queue, task_id, wake):
    return (
        queue.list_wakes(task_id)[0] if wake_table(wake) == "wake_outbox"
        else queue.list_run_waiter_wakes(task_id)[0]
    )


def receipt(wake):
    return {
        "version": 1, "session_id": BRIDGE, "native_session_id": NATIVE,
        "invocation_key": wake.id, "turn_index": 3,
        "queued": False, "admission": "accepted",
    }


def reconcile(queue, task_id, wake, observe):
    return reconcile_wake(
        queue, task_id, outbox=wake_table(wake), wake_id=wake.id,
        expected_revision=queue.task_diagnostics(task_id)["revision"],
        actor="operator", reason="inspect original acceptance", observe=observe,
    )


def test_positive_original_receipt_settles_without_send_or_lease_replacement(retained):
    queue, task_id, wake = retained
    with queue._connect() as conn:
        original_send = conn.execute(
            f"SELECT execution_send FROM {wake_table(wake)} WHERE id=?", (wake.id,),
        ).fetchone()["execution_send"]
    calls = []

    def observe(bridge, native, prompt, **kwargs):
        calls.append((bridge, native, prompt, kwargs))
        return receipt(wake)

    result = reconcile(queue, task_id, wake, observe)
    assert result["outcome"] == "sent"
    assert calls[0][:3] == (BRIDGE, NATIVE, "original prompt")
    assert calls[0][3]["idempotency_key"] == wake.id
    after = current(queue, task_id, wake)
    with queue._connect() as conn:
        assert conn.execute(
            f"SELECT execution_send FROM {wake_table(wake)} WHERE id=?", (wake.id,),
        ).fetchone()["execution_send"] == original_send
    assert after.delivery_token == wake.delivery_token
    assert after.attempts == wake.attempts
    assert after.execution_send_state == "sent"
    assert reconcile(queue, task_id, wake, observe)["replayed"]


@pytest.mark.parametrize("change", [
    {"session_id": "other-bridge"}, {"native_session_id": "other-native"},
    {"invocation_key": "other-wake"}, {"queued": True}, {"turn_index": True},
    {"admission": "missing"}, {"version": True},
])
def test_wrong_or_incomplete_receipt_preserves_unknown(retained, change):
    queue, task_id, wake = retained
    with pytest.raises(TaskError, match="does not prove"):
        reconcile(queue, task_id, wake, lambda *a, **k: {**receipt(wake), **change})
    assert current(queue, task_id, wake).execution_send_state == "unknown"


def test_missing_receipt_or_unavailable_transport_is_not_not_sent(retained):
    queue, task_id, wake = retained
    with pytest.raises(TaskError, match="does not prove"):
        reconcile(queue, task_id, wake, lambda *a, **k: None)
    assert current(queue, task_id, wake).execution_send_state == "unknown"


def test_same_generation_mutation_while_observing_refuses_settlement(retained):
    queue, task_id, wake = retained

    def observe(*args, **kwargs):
        queue.detach(task_id)
        return receipt(wake)

    with pytest.raises(TaskError, match="changed during"):
        reconcile(queue, task_id, wake, observe)
    assert current(queue, task_id, wake).execution_send_state == "unknown"


def test_legacy_identity_is_refused_before_any_provider_query(retained):
    queue, task_id, wake = retained
    with queue._connect() as conn:
        conn.execute(
            f"UPDATE {wake_table(wake)} SET execution_send=NULL, execution_send_state=NULL WHERE id=?",
            (wake.id,),
        )
    with pytest.raises(TaskError, match="legacy outcome remains unknown"):
        reconcile(queue, task_id, wake, lambda *a, **k: pytest.fail("no qualified identity"))


def test_original_send_token_cannot_be_substituted(retained):
    queue, task_id, wake = retained
    with queue._connect() as conn:
        row = conn.execute(
            f"SELECT execution_send FROM {wake_table(wake)} WHERE id=?", (wake.id,),
        ).fetchone()
        identity = json.loads(row["execution_send"])
        identity["owner_session_id"] = "other-native"
        conn.execute(
            f"UPDATE {wake_table(wake)} SET execution_send=? WHERE id=?",
            (json.dumps(identity), wake.id),
        )
    with pytest.raises(TaskError, match="conflicting"):
        reconcile(queue, task_id, wake, lambda *a, **k: pytest.fail("mismatched original identity"))


def test_native_observer_invokes_receipt_only_once_without_mutation_fallback(hosted, monkeypatch):
    from agent_dispatch import bridge
    queue, task_id = hosted
    queue.resume(task_id, OWNER, wake_requested=True, wake_message="original prompt")
    wake = queue.list_wakes(task_id)[0]
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, json.dumps(receipt(wake)), "")

    monkeypatch.setattr(bridge.subprocess, "run", run)
    result = native_prompt_receipt(
        BRIDGE, NATIVE, "original prompt", idempotency_key=wake.id, machine="host-a",
    )
    assert result == receipt(wake)
    assert len(calls) == 1
    assert "--receipt-only" in calls[0][0]
    assert calls[0][0][-1] == "--no-wait"
    assert calls[0][1]["input"] == "original prompt"

    calls.clear()
    monkeypatch.setattr(
        bridge.subprocess, "run",
        lambda argv, **kwargs: calls.append(argv) or subprocess.CompletedProcess(argv, 69, "", "missing"),
    )
    with pytest.raises(TaskError, match="remains unknown"):
        native_prompt_receipt(
            BRIDGE, NATIVE, "original prompt", idempotency_key=wake.id, machine="host-a",
        )
    assert len(calls) == 1
    assert "--receipt-only" in calls[0]


def test_retired_native_assignment_detaches_without_unpinning(hosted):
    queue, task_id = hosted
    reservation = queue.latest_reservation(task_id)
    queue.request_spawn_release(reservation.key, disposition="settled")
    queue.retire_spawn(reservation.key, exact_absence=True)
    assert not queue.latest_reservation(task_id).conversation_retired
    snapshot = queue.task_diagnostics(task_id)
    assert snapshot["actions"]["detach_agent"]["allowed"]
    result = queue.recover_task(
        task_id, operation="detach_agent", expected_revision=snapshot["revision"],
        request_id="detach-1", actor="operator", reason="retirement already qualified",
    )
    assert result["task"]["owner_session_id"] is None
    assert result["task"]["status"] == "queued"
    [attachment] = queue.attachment_history(task_id)
    assert attachment.detached_at is not None
    assert attachment.detach_reason.startswith("detach_agent:")
    assert queue.latest_reservation(task_id).release_requested


@pytest.mark.parametrize("unsupported", ["not_sent", "cli", "restricted-bridge", "identity"])
def test_diagnostics_and_reconciliation_share_exact_queue_eligibility(retained, unsupported):
    queue, task_id, wake = retained
    with queue._connect() as conn:
        if unsupported == "not_sent":
            conn.execute(
                f"UPDATE {wake_table(wake)} SET execution_send_state='not_sent' WHERE id=?",
                (wake.id,),
            )
        elif unsupported == "identity":
            conn.execute(
                f"UPDATE {wake_table(wake)} SET execution_send='{{}}' WHERE id=?",
                (wake.id,),
            )
        else:
            conn.execute(
                "UPDATE spawn_reservations SET driver=? WHERE task_id=?",
                (unsupported, task_id),
            )
    snapshot = queue.task_diagnostics(task_id)
    [reported] = snapshot["wakes"]
    assert reported["qualified_custody"]
    assert not reported["reconcile_supported"]
    assert reported["reconcile_reasons"]
    assert not snapshot["actions"]["reconcile_wake"]["allowed"]
    with pytest.raises(TaskError) as refused:
        reconcile(queue, task_id, wake, lambda *a, **k: pytest.fail("ineligible provider query"))
    assert str(refused.value) in reported["reconcile_reasons"]

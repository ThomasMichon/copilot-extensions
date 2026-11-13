"""Final wake sends keep the existing outbox and retry authority."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_dispatch import bridge_command_delivery as transport
from agent_dispatch import coordinator_execution_sender as sender
from agent_dispatch.coordinator_execution_wakes import deliver_execution_wake
from agent_dispatch.events import EventBus
from agent_dispatch.execution_effect_adapter import EffectRevocation
from agent_dispatch.execution_scope_binding import resolve_execution_installation
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_execution_runtimes import CoordinatorExecutionRuntime
from agent_dispatch.queue_execution_scopes import ExecutionScopeBinding
from agent_dispatch.queue_execution_wakes import Wake, WakeSendAdmission, wake_table
from agent_dispatch.queue_records import TaskError
from agent_dispatch.run_waiter_wake import drain_run_waiter_wakes
from agent_dispatch.wake import drain_wake_outbox
from tests.test_execution_scope_binding import _governance, _state

REPO = "github.com/example/project"
SOURCE = "repair"
OWNER = "host/worktree"
INCARNATION = "application-a"
Cohort = tuple[TaskQueue, Wake, ExecutionScopeBinding]


@pytest.fixture(params=["owner", "waiter"])
def cohort(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> Cohort:
    queue = TaskQueue(tmp_path / "tasks.db")
    task = queue.create("resume", repo=REPO, source=SOURCE)
    queue.claim_one(OWNER, task_id=task.id)
    queue.start(task.id, OWNER, owner_session_id="session-a")
    queue.suspend(task.id, OWNER, reason="external")
    if request.param == "owner":
        queue.resume(task.id, OWNER, wake_requested=True, wake_message="continue")
        [wake] = queue.list_wakes(task.id)
    else:
        waiter = queue.register_run_waiter(
            task.id, pid=123, host="host", start_token="waiter-start",
            resume_worktree="host/worktree", command=["synthetic-wait"],
        )
        queue.recover_dead_run_waiter(
            task.id, generation=waiter["generation"], reason="external",
            message="continue", sender="recovery",
        )
        [wake] = queue.list_run_waiter_wakes(task.id)
    binding = _qualify(queue, queue.get(task.id), monkeypatch)
    return queue, wake, binding


def _qualify(queue: TaskQueue, task, monkeypatch: pytest.MonkeyPatch) -> ExecutionScopeBinding:
    queue.handoff_producer_scope(task.repo, task.source, producer_id="producer-a", expected_generation=0)
    binding = queue.bind_execution_scope(
        resolve_execution_installation(_governance()),
        repo=task.repo, source=task.source, expected_producer_generation=1,
    )
    monkeypatch.setattr(sender, "LoopGovernance", _governance)
    monkeypatch.setattr(
        sender.runtime, "_own_process",
        lambda incarnation: CoordinatorExecutionRuntime(incarnation, 1234, "coordinator-start"),
    )
    return binding


def _claim(queue: TaskQueue, wake: Wake, **kwargs: Any) -> Wake | None:
    return (
        queue.claim_due_wake(**kwargs) if wake_table(wake) == "wake_outbox"
        else queue.claim_due_run_waiter_wake(**kwargs)
    )


def _recover(queue: TaskQueue, wake: Wake, **kwargs: Any) -> int:
    return (
        queue.recover_inflight_wakes(**kwargs) if wake_table(wake) == "wake_outbox"
        else queue.recover_inflight_run_waiter_wakes(**kwargs)
    )


def _finish(queue: TaskQueue, wake: Wake, **kwargs: Any) -> Wake:
    finish = queue.finish_wake if wake_table(wake) == "wake_outbox" else queue.finish_run_waiter_wake
    return finish(wake.id, wake.delivery_token, **kwargs)


def _row(queue: TaskQueue, wake: Wake) -> dict[str, Any]:
    with queue._connect() as conn:
        return dict(conn.execute(f"SELECT * FROM {wake_table(wake)} WHERE id = ?", (wake.id,)).fetchone())


def _close(queue: TaskQueue, binding: ExecutionScopeBinding) -> None:
    queue.close_execution_admission(
        EffectRevocation(binding.scope, "close-a", 1, "automatic", "manual"),
        source=SOURCE, expected_producer_generation=1,
    )


def test_actual_wake_admission_precedes_transport_and_inventory_retains_identity(cohort: Cohort) -> None:
    queue, pending, binding = cohort
    wake = _claim(queue, pending)

    def deliver() -> bool:
        row = _row(queue, wake)
        assert row["execution_send_state"] == "pending"
        identity = json.loads(row["execution_send"])
        assert identity["delivery_token"] == wake.delivery_token
        assert identity["wake_id"] == wake.id
        with queue._connect() as conn:
            runtime = conn.execute("SELECT * FROM execution_runtime_enrollments").fetchone()
        assert identity["runtime_id"] == runtime["runtime_id"]
        assert json.loads(runtime["process"])["incarnation"] == INCARNATION
        queue.create("independent", repo="example/other")
        _close(queue, binding)
        return True

    assert deliver_execution_wake(queue, wake, INCARNATION, deliver)
    assert _row(queue, wake)["execution_send_state"] == "sent"
    assert _finish(queue, wake, delivered=True).status == "delivered"
    inventory = queue.execution_closure_inventory(binding.scope)
    records = inventory["wakes" if wake_table(wake) == "wake_outbox" else "run_waiter_wakes"]
    assert records[0]["execution_send_state"] == "sent"
    assert inventory["acknowledged"] is False


@pytest.mark.parametrize("failure", ["false", "exception", "settlement"])
def test_ambiguous_wake_never_recovers_or_resends_after_lease_expiry(
    cohort: Cohort, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    queue, pending, binding = cohort
    wake = _claim(queue, pending)
    calls = []

    def deliver() -> bool:
        calls.append(wake.id)
        if failure == "exception":
            raise OSError("reply lost")
        return failure == "settlement"

    if failure == "settlement":
        def fail(*args: Any, **kwargs: Any) -> None:
            raise sqlite3.OperationalError("settlement unavailable")
        monkeypatch.setattr(queue, "settle_execution_wake", fail)
    if failure == "false":
        assert deliver_execution_wake(queue, wake, INCARNATION, deliver) is False
    else:
        with pytest.raises((OSError, sqlite3.OperationalError)):
            deliver_execution_wake(queue, wake, INCARNATION, deliver)
    assert _finish(queue, wake, delivered=False, max_attempts=1).status == "delivering"
    restarted = TaskQueue(queue.db_path)
    future = wake.delivery_expires_at + 100
    assert _recover(restarted, wake, now=future) == 0
    assert _claim(restarted, wake, now=future) is None
    with pytest.raises(TaskError, match="unresolved transport"):
        deliver_execution_wake(queue, wake, INCARNATION, deliver)
    assert calls == [wake.id]
    _close(queue, binding)
    assert _row(restarted, wake)["execution_send_state"] == ("pending" if failure == "settlement" else "unknown")


@pytest.fixture
def native_transport(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    reply = SimpleNamespace(
        code=75,
        payload={
            "outcome": "refused_busy", "target": "bridge-a", "retryable": True,
            "reason": "busy", "error": "original native is running a turn",
        },
        calls=[],
    )

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        reply.calls.append((argv, kwargs["input"]))
        payload = reply.payload if len(reply.calls) == 1 else {}
        return subprocess.CompletedProcess(
            argv, reply.code if len(reply.calls) == 1 else 0,
            payload if isinstance(payload, str) else json.dumps(payload), "",
        )

    monkeypatch.setattr(transport.remote_dispatch, "local_machine", lambda: "host")
    monkeypatch.setattr(transport.subprocess, "run", run)

    def send(wake: Wake) -> bool:
        return transport.native_prompt(
            "bridge-a", "session-a", "continue", idempotency_key=wake.id, machine="host",
            launch_prefix=lambda: ["selected-bridge"],
            run_ssh=lambda *a, **k: pytest.fail("native wake changed transport"),
        )

    reply.send = send
    return reply


def test_typed_pre_admission_busy_retries_only_the_original_wake(
    cohort: Cohort, native_transport: SimpleNamespace,
) -> None:
    queue, pending, _binding = cohort
    before = queue.get(pending.task_id)
    wake = _claim(queue, pending)
    assert wake is not None
    assert deliver_execution_wake(
        queue, wake, INCARNATION, lambda: native_transport.send(wake),
    ) is False
    assert _row(queue, wake)["execution_send_state"] == "not_sent"
    original_send = json.loads(_row(queue, wake)["execution_send"])
    with pytest.raises(TaskError, match="observation only"):
        deliver_execution_wake(
            queue, wake, INCARNATION, lambda: pytest.fail("same delivery token retried"),
        )
    retry = _finish(queue, wake, delivered=False, retry_base=0.01)
    assert retry.status == "pending"
    queue = TaskQueue(queue.db_path)
    next_wake = _claim(queue, pending, now=retry.not_before + 0.001)
    assert next_wake is not None and next_wake.id == wake.id
    assert next_wake.delivery_token != wake.delivery_token
    def send_after_history() -> bool:
        with queue._connect() as conn:
            notes = conn.execute(
                "SELECT note FROM task_events WHERE task_id=?", (wake.task_id,),
            ).fetchall()
        [retained] = [
            json.loads(row["note"].split(": ", 1)[1])
            for row in notes if row["note"].startswith("wake known-not-sent retry: ")
        ]
        assert retained["identity"] == original_send
        assert retained["refusal"]["native_session_id"] == wake.owner_session_id
        assert retained["next_delivery_token"] == next_wake.delivery_token
        return native_transport.send(next_wake)

    assert deliver_execution_wake(queue, next_wake, INCARNATION, send_after_history)
    assert _finish(queue, next_wake, delivered=True).status == "delivered"
    assert _row(queue, next_wake)["execution_send_state"] == "sent"
    assert len(native_transport.calls) == 2
    assert native_transport.calls[0] == native_transport.calls[1]
    current = queue.get(pending.task_id)
    assert (current.owner, current.owner_session_id, current.generation) == (
        before.owner, before.owner_session_id, before.generation,
    )


@pytest.mark.parametrize("change", ["malformed", "target", "retryable", "reason", "exit"])
def test_unconfirmed_busy_shape_remains_unknown_without_retry(
    cohort: Cohort, native_transport: SimpleNamespace, change: str,
) -> None:
    queue, pending, _binding = cohort
    if change == "malformed":
        native_transport.payload = "not-json"
    elif change == "target":
        native_transport.payload["target"] = "other-bridge"
    elif change == "retryable":
        native_transport.payload["retryable"] = 1
    elif change == "reason":
        native_transport.payload["reason"] = "acceptance_unknown"
    else:
        native_transport.code = 70
    wake = _claim(queue, pending)
    assert wake is not None
    assert deliver_execution_wake(
        queue, wake, INCARNATION, lambda: native_transport.send(wake),
    ) is False
    assert _row(queue, wake)["execution_send_state"] == "unknown"
    assert _finish(queue, wake, delivered=False, max_attempts=1).status == "delivering"
    future = wake.delivery_expires_at + 100
    assert _recover(queue, wake, now=future) == 0
    assert _claim(queue, wake, now=future) is None
    assert len(native_transport.calls) == 1


def test_legacy_not_sent_without_typed_refusal_does_not_authorize_another_send(
    cohort: Cohort,
) -> None:
    queue, pending, _binding = cohort
    wake = _claim(queue, pending)
    assert wake is not None
    task = queue.get(wake.task_id)
    provenance = sender.CoordinatorExecutionProvenance(queue, task, INCARNATION).prepare()
    admission = queue.admit_execution_wake(wake, provenance=provenance)
    queue.settle_execution_wake(wake, admission, outcome="not_sent", detail="legacy refusal")
    retry = _finish(queue, wake, delivered=False, retry_base=0.01)
    next_wake = _claim(queue, pending, now=retry.not_before + 0.001)
    assert next_wake is not None
    with pytest.raises(TaskError, match="observation only"):
        deliver_execution_wake(
            queue, next_wake, INCARNATION, lambda: pytest.fail("untyped refusal retried"),
        )


@pytest.mark.parametrize("mismatch", ["key", "native"])
def test_refusal_for_another_native_request_cannot_release_original_wake(
    cohort: Cohort, mismatch: str,
) -> None:
    queue, pending, _binding = cohort
    wake = _claim(queue, pending)
    assert wake is not None

    def deliver() -> bool:
        raise transport.NativePromptNotSent(
            "bridge-a", "other-native" if mismatch == "native" else wake.owner_session_id,
            "other-key" if mismatch == "key" else wake.id,
        )

    with pytest.raises(TaskError, match="original wake identity"):
        deliver_execution_wake(queue, wake, INCARNATION, deliver)
    assert _row(queue, wake)["execution_send_state"] == "unknown"
    assert _finish(queue, wake, delivered=False, max_attempts=1).status == "delivering"


@pytest.mark.parametrize("race", ["closure", "token", "generation", "session", "expired"])
def test_final_admission_rechecks_queue_fences(
    cohort: Cohort, monkeypatch: pytest.MonkeyPatch, race: str,
) -> None:
    queue, pending, binding = cohort
    wake = _claim(queue, pending)
    original = queue.admit_execution_wake

    def admit(*args: Any, **kwargs: Any) -> Any:
        if race == "closure":
            _close(queue, binding)
        else:
            with queue._connect() as conn:
                if race == "token":
                    conn.execute(f"UPDATE {wake_table(wake)} SET delivery_token = 'other' WHERE id = ?", (wake.id,))
                elif race == "expired":
                    conn.execute(f"UPDATE {wake_table(wake)} SET delivery_expires_at = 1 WHERE id = ?", (wake.id,))
                else:
                    field, value = ("generation", 9) if race == "generation" else ("owner_session_id", "other")
                    conn.execute(f"UPDATE tasks SET {field} = ? WHERE id = ?", (value, wake.task_id))
                conn.commit()
        return original(*args, **kwargs)

    monkeypatch.setattr(queue, "admit_execution_wake", admit)
    with pytest.raises(TaskError):
        deliver_execution_wake(queue, wake, INCARNATION, lambda: pytest.fail("stale transport"))
    assert _row(queue, wake)["execution_send"] is None


@pytest.mark.parametrize("drift", ["incarnation", "legacy", "generation"])
def test_qualified_wake_cannot_fall_back_after_provenance_loss(
    cohort: Cohort, monkeypatch: pytest.MonkeyPatch, drift: str,
) -> None:
    queue, pending, _binding = cohort
    wake = _claim(queue, pending)
    incarnation = INCARNATION
    if drift == "incarnation":
        incarnation = None
    elif drift == "legacy":
        state = _state()
        state["baseline"]["actualMode"] = "legacy"
        monkeypatch.setattr(sender, "LoopGovernance", lambda: _governance(state))
    else:
        original = queue.admit_execution_wake

        def admit(*args: Any, **kwargs: Any) -> Any:
            queue.handoff_producer_scope(REPO, SOURCE, producer_id="producer-b", expected_generation=1)
            return original(*args, **kwargs)
        monkeypatch.setattr(queue, "admit_execution_wake", admit)
    with pytest.raises((TaskError, ValueError)):
        deliver_execution_wake(queue, wake, incarnation, lambda: pytest.fail("unqualified transport"))
    assert _row(queue, wake)["execution_send"] is None


def test_pending_wake_custody_replay_is_observation_only(cohort: Cohort) -> None:
    queue, pending, _binding = cohort
    wake = _claim(queue, pending)
    provenance = sender.CoordinatorExecutionProvenance(queue, queue.get(wake.task_id), INCARNATION).prepare()
    admission = queue.admit_execution_wake(wake, provenance=provenance)
    with pytest.raises(TaskError, match="unresolved transport"):
        queue.admit_execution_wake(wake, provenance=provenance)
    with pytest.raises(TaskError, match="original delivery token"):
        queue.settle_execution_wake(
            replace(wake, delivery_token="other"), admission, outcome="sent", detail="accepted",
        )
    queue.settle_execution_wake(wake, admission, outcome="sent", detail="accepted")
    with pytest.raises(TaskError, match="conflicts"):
        queue.settle_execution_wake(wake, admission, outcome="not_sent", detail="contradiction")


def test_accepted_wake_recovery_observes_without_transport_after_closure(cohort: Cohort) -> None:
    queue, pending, binding = cohort
    wake = _claim(queue, pending)
    assert deliver_execution_wake(queue, wake, INCARNATION, lambda: True)
    _close(queue, binding)
    future = wake.delivery_expires_at + 100
    assert _recover(queue, wake, now=future) == 1
    recovered = _claim(queue, wake, now=future)
    assert recovered is not None and recovered.delivery_token != wake.delivery_token
    assert deliver_execution_wake(queue, recovered, None, lambda: pytest.fail("accepted retry"))
    assert _finish(queue, recovered, delivered=True).status == "delivered"


def test_both_actual_drainers_mediate_qualified_wakes(cohort: Cohort) -> None:
    queue, pending, _binding = cohort
    calls = []

    def deliver(*args: Any) -> bool:
        calls.append(args)
        assert _row(queue, pending)["execution_send_state"] == "pending"
        return True

    async def scenario() -> None:
        drain = (
            drain_wake_outbox(queue, EventBus(), deliver=deliver, interval=0.01, execution_incarnation=INCARNATION)
            if wake_table(pending) == "wake_outbox"
            else drain_run_waiter_wakes(queue, deliver=deliver, interval=0.01, execution_incarnation=INCARNATION)
        )
        loop = asyncio.create_task(drain)
        try:
            for _ in range(100):
                if _row(queue, pending)["status"] == "delivered":
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("qualified wake did not drain")
        finally:
            loop.cancel()
            with pytest.raises(asyncio.CancelledError):
                await loop

    asyncio.run(scenario())
    assert len(calls) == 1
    assert _row(queue, pending)["execution_send_state"] == "sent"


def test_detached_claim_release_retry_never_repeats_accepted_owner_delivery(cohort: Cohort) -> None:
    queue, pending, binding = cohort
    if wake_table(pending) != "run_waiter_wakes":
        pytest.skip("detached cleanup only")
    deliveries = []
    releases = []

    def deliver(*args: Any) -> bool:
        deliveries.append(args)
        return True

    def release(*args: Any) -> dict[str, bool] | None:
        releases.append(args)
        _close(queue, binding)
        return None if len(releases) == 1 else {"released": True}

    async def scenario() -> None:
        loop = asyncio.create_task(drain_run_waiter_wakes(
            queue, interval=0.01, retry_base=0.01, deliver=deliver, release_claim=release,
            execution_incarnation=INCARNATION,
        ))
        try:
            for _ in range(100):
                if _row(queue, pending)["status"] == "delivered":
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("claim release retry did not finish")
        finally:
            loop.cancel()
            with pytest.raises(asyncio.CancelledError):
                await loop

    asyncio.run(scenario())
    assert len(deliveries) == 1 and len(releases) == 2
    assert _row(queue, pending)["execution_send_state"] == "sent"


def test_unknown_wake_blocks_other_outbox_and_reservation_recovery(cohort: Cohort) -> None:
    queue, pending, _binding = cohort
    wake = _claim(queue, pending)
    assert deliver_execution_wake(queue, wake, INCARNATION, lambda: False) is False
    with queue._connect() as conn:
        from agent_dispatch.queue_execution_custody import require_execution_send_reconciliation

        with pytest.raises(TaskError, match="unresolved execution wake"):
            require_execution_send_reconciliation(conn, wake.task_id, exclusive_key=None)
    # A new natural wake is retained, but cannot hide the old transport ambiguity.
    queue.submit_steer(wake.task_id, fields={"decision": "new"}, wake_requested=True)
    assert queue.claim_due_wake() is None
    assert queue.claim_due_run_waiter_wake() is None
    assert _row(queue, wake)["execution_send_state"] == "unknown"


def test_wake_custody_cannot_be_hidden_by_shared_execution_key(cohort: Cohort) -> None:
    queue, pending, _binding = cohort
    wake = _claim(queue, pending)
    with queue._connect() as conn:
        conn.execute("UPDATE tasks SET exclusive_key = 'shared-lane' WHERE id = ?", (wake.task_id,))
        conn.commit()
    assert deliver_execution_wake(queue, wake, INCARNATION, lambda: False) is False
    other = queue.create("replacement", repo=REPO, exclusive_key="shared-lane")
    with queue._connect() as conn:
        from agent_dispatch.queue_execution_custody import require_execution_send_reconciliation

        with pytest.raises(TaskError, match="unresolved execution wake"):
            require_execution_send_reconciliation(conn, other.id, exclusive_key="shared-lane")


def test_settled_absence_is_retained_not_another_send_permission(cohort: Cohort) -> None:
    queue, pending, _binding = cohort
    wake = _claim(queue, pending)
    provenance = sender.CoordinatorExecutionProvenance(queue, queue.get(wake.task_id), INCARNATION).prepare()
    admission = queue.admit_execution_wake(wake, provenance=provenance)
    queue.settle_execution_wake(wake, admission, outcome="not_sent", detail="synthetic transport absence")
    _finish(queue, wake, delivered=False, retry_base=0.01)
    retry = _claim(queue, wake, now=wake.delivery_expires_at + 100)
    assert retry is not None
    with pytest.raises(TaskError, match="observation only"):
        deliver_execution_wake(queue, retry, INCARNATION, lambda: pytest.fail("settled resend"))
    assert _row(queue, retry)["execution_send"] == admission.identity


def test_wake_history_keeps_qualification_when_binding_records_are_missing(
    cohort: Cohort, monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue, pending, _binding = cohort
    wake = _claim(queue, pending)
    assert deliver_execution_wake(queue, wake, INCARNATION, lambda: False) is False
    with queue._connect() as conn:
        for table in ("execution_scope_bindings", "execution_runtime_enrollments"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    state = _state()
    state["baseline"]["actualMode"] = "legacy"
    monkeypatch.setattr(sender, "LoopGovernance", lambda: _governance(state))
    with pytest.raises((TaskError, ValueError)):
        deliver_execution_wake(queue, wake, INCARNATION, lambda: pytest.fail("historical fallback"))


def test_no_reservation_unknown_wake_fences_replacement_logical_workspace(cohort: Cohort) -> None:
    queue, pending, _binding = cohort
    wake = _claim(queue, pending)
    with queue._connect() as conn:
        conn.execute("UPDATE tasks SET dedup_key = 'logical-lane' WHERE id = ?", (wake.task_id,))
        conn.commit()
    assert queue.latest_reservation(wake.task_id) is None
    assert deliver_execution_wake(queue, wake, INCARNATION, lambda: False) is False
    queue.abandon(wake.task_id, permitted=True, reason="synthetic terminal bookkeeping")
    replacement = queue.create("replacement", repo=REPO, dedup_key="logical-lane")
    # Same dedup text in another repository is a separate logical workspace.
    other = queue.create("other", repo="example/other", dedup_key="logical-lane")
    assert queue.reserve_spawn(other.id)[1] is True
    with pytest.raises(TaskError, match="unresolved execution wake"):
        queue.reserve_spawn(replacement.id)
    row = _row(queue, wake)
    queue.settle_execution_wake(
        wake, WakeSendAdmission(row["execution_send"], True),
        outcome="not_sent", detail="synthetic authoritative absence",
    )
    assert queue.reserve_spawn(replacement.id)[1] is True


@pytest.mark.parametrize("next_kind", ["owner", "waiter"])
@pytest.mark.parametrize("outcome", ["pending", "unknown"])
def test_shared_key_refusal_preserves_unsent_wake_retry_budget(
    cohort: Cohort, next_kind: str, outcome: str,
) -> None:
    queue, pending, _binding = cohort
    wake = _claim(queue, pending)
    with queue._connect() as conn:
        conn.execute("UPDATE tasks SET exclusive_key = 'blocked-lane' WHERE id = ?", (wake.task_id,))
        conn.commit()
    provenance = sender.CoordinatorExecutionProvenance(queue, queue.get(wake.task_id), INCARNATION).prepare()
    admission = queue.admit_execution_wake(wake, provenance=provenance)
    if outcome == "unknown":
        queue.settle_execution_wake(wake, admission, outcome="unknown", detail="synthetic lost response")
    next_wake = _next_shared_wake(queue, next_kind)
    for _ in range(10):
        assert _claim(queue, next_wake) is None
    assert _row(queue, next_wake)["attempts"] == 0
    assert _row(queue, next_wake)["status"] == "pending"
    queue.settle_execution_wake(wake, admission, outcome="sent", detail="synthetic accepted response")
    current = _claim(queue, next_wake)
    assert current is not None and current.id == next_wake.id
    assert deliver_execution_wake(queue, current, None, lambda: True)
    assert _finish(queue, current, delivered=True).status == "delivered"


def _next_shared_wake(
    queue: TaskQueue, kind: str, *, exclusive_key: str | None = "blocked-lane",
) -> Wake:
    task = queue.create("next", repo=REPO, exclusive_key=exclusive_key)
    queue.claim_one("host/next", task_id=task.id)
    queue.start(task.id, "host/next", owner_session_id="session-next")
    queue.suspend(task.id, "host/next", reason="external")
    if kind == "owner":
        queue.resume(task.id, "host/next", wake_requested=True)
        [next_wake] = queue.list_wakes(task.id)
    else:
        waiter = queue.register_run_waiter(
            task.id, pid=456, host="host", start_token="next-start",
            resume_worktree="host/next", command=["synthetic-wait"],
        )
        queue.recover_dead_run_waiter(
            task.id, generation=waiter["generation"], reason="external",
            message="continue", sender="recovery",
        )
        [next_wake] = queue.list_run_waiter_wakes(task.id)
    return next_wake


@pytest.mark.parametrize("next_kind", ["owner", "waiter"])
def test_custody_appearing_after_claim_does_not_spend_transport_budget(
    cohort: Cohort, monkeypatch: pytest.MonkeyPatch, next_kind: str,
) -> None:
    queue, pending, _binding = cohort
    wake = _claim(queue, pending)
    with queue._connect() as conn:
        conn.execute("UPDATE tasks SET exclusive_key = 'blocked-lane' WHERE id = ?", (wake.task_id,))
        conn.commit()
    next_wake = _next_shared_wake(queue, next_kind)
    provenance = sender.CoordinatorExecutionProvenance(queue, queue.get(wake.task_id), INCARNATION).prepare()
    original = queue.admit_execution_wake
    admissions = []
    deliveries = []

    def admit(candidate: Wake, **kwargs: Any) -> WakeSendAdmission:
        if candidate.id == next_wake.id and not admissions:
            admissions.append(original(wake, provenance=provenance))
        return original(candidate, **kwargs)

    monkeypatch.setattr(queue, "admit_execution_wake", admit)

    def deliver(*args: Any) -> bool:
        deliveries.append(args)
        return True

    async def scenario() -> None:
        drain = (
            drain_wake_outbox(queue, EventBus(), deliver=deliver, interval=0.01, max_attempts=1)
            if next_kind == "owner"
            else drain_run_waiter_wakes(queue, deliver=deliver, interval=0.01, max_attempts=1)
        )
        loop = asyncio.create_task(drain)
        try:
            for _ in range(100):
                row = _row(queue, next_wake)
                if admissions and row["status"] == "pending":
                    assert row["attempts"] == 0 and deliveries == []
                    queue.settle_execution_wake(
                        wake, admissions[0], outcome="sent", detail="synthetic late acceptance",
                    )
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("unsent wake did not defer")
            for _ in range(100):
                if _row(queue, next_wake)["status"] == "delivered":
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("reconciled wake was not deliverable")
        finally:
            loop.cancel()
            with pytest.raises(asyncio.CancelledError):
                await loop

    asyncio.run(scenario())
    assert len(deliveries) == 1

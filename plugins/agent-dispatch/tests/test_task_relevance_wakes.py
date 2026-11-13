"""Relevance closeout uses the original outbox/native, never a goal restart."""

import asyncio
import json
from dataclasses import replace

import pytest

from agent_dispatch.coordinator_execution_wakes import deliver_execution_wake
from agent_dispatch.owner_wake_delivery import deliver_owner_wake
from agent_dispatch.queue import Status, TaskError
from agent_dispatch.queue_task_recovery import _captured, _revision
from agent_dispatch.task_wake_recovery import reconcile_wake
from tests._helpers import RepoDefaultingQueue as TaskQueue
from tests.test_native_owner_wake import hosted as hosted, OWNER, NATIVE, BRIDGE, _capture, _expected
from tests.test_task_relevance import q as q, task, report, request


def bound(q, *, suspended=False):
    t = task(q)
    q.claim_one(OWNER, task_id=t.id)
    q.start(t.id, OWNER, owner_session_id=NATIVE)
    if suspended:
        q.suspend(t.id, OWNER, reason="waiting")
    return t.id


@pytest.mark.parametrize("suspended", [False, True])
def test_closeout_steer_takes_priority_without_resuming_or_consuming_old_goal(q, suspended):
    task_id = bound(q, suspended=suspended)
    if not suspended:
        q.submit_steer(task_id, fields={"old_goal": "continue"})
    before = q.get(task_id)
    accepted = report(q, task_id)
    steers = q.take_steer(task_id, OWNER, all_pending=True)
    assert [s["id"] for s in steers] == [accepted["steer_id"]]
    assert q.take_steer(task_id, OWNER, all_pending=True) == []
    current = q.get(task_id)
    assert (current.status, current.generation, current.owner_session_id, current.resume_requested) == (
        before.status, before.generation, NATIVE, before.resume_requested,
    )
    if suspended:
        assert current.lease_expires_at is None
    else:
        assert q.steer_log(task_id)[0]["taken"] is False
    if suspended:
        with pytest.raises(TaskError):
            q.start(task_id, OWNER, owner_session_id=NATIVE)
    else:
        assert q.start(task_id, OWNER, owner_session_id=NATIVE) == current
    with pytest.raises(TaskError, match="owned by"):
        q.take_steer(task_id, "other/worktree")


def test_retry_restart_and_separate_report_keep_one_closeout_wake_and_receipt(q):
    task_id = bound(q, suspended=True)
    original = request(q, task_id)
    seen = []
    q.set_wake_notifier(lambda: seen.append(q.list_wakes(task_id)))
    accepted = q.reconcile_task_relevance(task_id, **original)
    assert len(seen) == 1 and len(seen[0]) == 1
    restarted = TaskQueue(q.db_path)
    assert restarted.reconcile_task_relevance(task_id, **original) == accepted
    report(restarted, task_id, request_id="second-observation")
    [wake] = restarted.list_wakes(task_id)
    assert wake.id == accepted["wake_operation_id"]
    assert len(restarted.steer_log(task_id)) == 1
    receipt = restarted.relevance_receipt(task_id, original["request_id"])
    assert receipt["wake"]["id"] == wake.id and receipt["wake"]["status"] == "pending"
    assert receipt["steer_consumed_at"] is None


@pytest.mark.parametrize("transition", ["yield", "reset", "requeue"])
def test_original_closeout_survives_unassignment_and_restart(q, transition):
    task_id = bound(q)
    original = request(q, task_id)
    accepted = q.reconcile_task_relevance(task_id, **original)
    q.yield_task(task_id, OWNER)
    if transition != "yield":
        q.reset(task_id)
        if transition == "requeue":
            q.approve(task_id)
    restarted = TaskQueue(q.db_path)
    before = restarted.get(task_id)
    assert before.owner is before.owner_session_id is before.lease_expires_at is None
    wake = restarted.claim_due_wake()
    assert wake is not None and wake.id == accepted["wake_operation_id"]
    assert (wake.owner, wake.owner_session_id) == (OWNER, NATIVE)
    sent = []
    assert deliver_execution_wake(
        restarted, wake, None, lambda: sent.append((wake.owner, wake.owner_session_id)) or True,
    )
    restarted.finish_wake(wake.id, wake.delivery_token, delivered=True)
    with pytest.raises(TaskError, match="owned by"):
        restarted.take_steer(task_id, "other/worktree")
    steer = restarted.take_steer(task_id, OWNER)
    assert steer["id"] == accepted["steer_id"]
    assert restarted.take_steer(task_id, OWNER) is None
    after = restarted.get(task_id)
    assert (after.status, after.generation, after.owner, after.owner_session_id, after.lease_expires_at) == (
        before.status, before.generation, None, None, None,
    )
    assert sent == [(OWNER, NATIVE)]
    assert restarted.reconcile_task_relevance(task_id, **original) == accepted
    assert restarted.claim_one("other/worktree", task_id=task_id) is None
    with pytest.raises(TaskError, match="requires owner closeout"):
        restarted.reserve_spawn(task_id)


def test_unassigned_closeout_native_transport_keeps_retained_target(hosted, monkeypatch):
    queue, task_id = hosted
    queue.resume(task_id, OWNER, reuse_session=True)
    accepted = _hosted_report(queue, task_id)
    queue.yield_task(task_id, OWNER, release_spawn=False)
    restarted = TaskQueue(queue.db_path)
    reservation = restarted.latest_reservation(task_id)
    calls = _capture(monkeypatch)
    wake = restarted.claim_due_wake()
    assert wake is not None
    assert deliver_execution_wake(
        restarted, wake, None, lambda: deliver_owner_wake(
            restarted, wake.owner, wake.owner_session_id, task_id, wake.message, wake.id,
        ),
    )
    restarted.finish_wake(wake.id, wake.delivery_token, delivered=True)
    assert [argv for argv, _ in calls] == [_expected(accepted["wake_operation_id"])]
    assert "do not start or resume the goal" in calls[0][1]["input"]
    assert restarted.latest_reservation(task_id) == reservation
    assert restarted.get(task_id).status == Status.QUEUED
    assert restarted.get(task_id).owner is restarted.get(task_id).lease_expires_at is None


@pytest.mark.parametrize("change", ["owner", "native", "generation", "regressed_generation", "terminal"])
def test_unassigned_closeout_refuses_foreign_or_changed_task_episode(q, change):
    task_id = bound(q)
    report(q, task_id)
    q.yield_task(task_id, OWNER)
    wake = q.claim_due_wake()
    assert wake is not None
    before = q.get(task_id)
    column, value = {
        "owner": ("owner", "other/worktree"),
        "native": ("owner_session_id", "other-native"),
        "generation": ("generation", before.generation + 1),
        "regressed_generation": ("generation", before.generation - 1),
        "terminal": ("status", Status.ABANDONED),
    }[change]
    with q._connect() as conn:
        conn.execute(f"UPDATE tasks SET {column}=? WHERE id=?", (value, task_id))
    with pytest.raises(TaskError, match="original unheld owner"):
        q.take_steer(task_id, OWNER)
    with pytest.raises(TaskError, match="fence advanced"):
        q.admit_execution_wake(wake, provenance=None)
    assert q.list_wakes(task_id)[0].execution_send_state is None


@pytest.mark.parametrize("boundary", ["hold", "release", "retired", "cooling"])
def test_unassigned_closeout_keeps_actual_execution_guards(hosted, boundary):
    queue, task_id = hosted
    queue.resume(task_id, OWNER, reuse_session=True)
    _hosted_report(queue, task_id)
    queue.yield_task(task_id, OWNER, release_spawn=False)
    wake = queue.claim_due_wake()
    assert wake is not None
    if boundary == "hold":
        queue.set_hold(task_id, reason="preserve work", actor="operator")
        with pytest.raises(TaskError, match="original unheld owner"):
            queue.take_steer(task_id, OWNER)
    else:
        column, value = {
            "release": ("release_requested", 1),
            "retired": ("conversation_retired", 1),
            "cooling": ("cooling_token", "unresolved-cooling"),
        }[boundary]
        with queue._connect() as conn:
            conn.execute(f"UPDATE spawn_reservations SET {column}=? WHERE task_id=?", (value, task_id))
    with pytest.raises(TaskError):
        queue.admit_execution_wake(wake, provenance=None)
    assert queue.list_wakes(task_id)[0].execution_send_state is None


@pytest.mark.parametrize("boundary", ["valid", "missing_provenance", "closed_scope"])
def test_closeout_does_not_bypass_qualified_runtime_or_scope_closure(q, monkeypatch, boundary):
    from agent_dispatch.execution_effect_adapter import EffectRevocation
    from tests.test_execution_wake_sends import _qualify, INCARNATION
    task_id = bound(q, suspended=True)
    binding = _qualify(q, q.get(task_id), monkeypatch)
    report(q, task_id)
    wake = q.claim_due_wake()
    if boundary == "valid":
        assert deliver_execution_wake(q, wake, INCARNATION, lambda: True)
        assert q.get(task_id).status == Status.SUSPENDED
        return
    if boundary == "closed_scope":
        q.close_execution_admission(
            EffectRevocation(binding.scope, "manual-owner", 1, "automatic", "manual"),
            source=q.get(task_id).source, expected_producer_generation=1,
        )
    with pytest.raises(TaskError):
        deliver_execution_wake(
            q, wake, None if boundary == "missing_provenance" else INCARNATION,
            lambda: pytest.fail("unqualified closeout must not send"),
        )
    assert q.list_wakes(task_id)[-1].execution_send_state is None


def test_outbox_failure_rolls_back_closeout_steer_audit_and_notification(q, monkeypatch):
    task_id = bound(q)
    before = q.get(task_id), q.events(task_id)
    seen = []
    q.set_wake_notifier(lambda: seen.append("wake"))

    def fail(*args, **kwargs):
        raise TaskError("outbox unavailable")

    monkeypatch.setattr(q, "_enqueue_wake", fail)
    with pytest.raises(TaskError, match="outbox unavailable"):
        report(q, task_id)
    assert (q.get(task_id), q.events(task_id)) == before
    assert q.relevance_snapshot(task_id)["closeout"] is None
    assert q.steer_log(task_id) == q.list_wakes(task_id) == seen == []


def test_hold_keeps_closeout_pending_and_clear_hold_never_resumes_goal(q):
    task_id = bound(q, suspended=True)
    q.set_hold(task_id, reason="operator hold", actor="operator")
    assert report(q, task_id)["delivery"] == "held"
    assert q.claim_due_wake() is None
    [pending] = q.list_wakes(task_id)
    assert pending.status == "pending" and pending.attempts == 0
    with pytest.raises(TaskError, match="unheld owner"):
        q.take_steer(task_id, OWNER)
    q.clear_hold(task_id)
    wake = q.claim_due_wake()
    assert wake.id == pending.id and q.get(task_id).status == Status.SUSPENDED
    q.set_hold(task_id, reason="hold after claim", actor="operator")
    with pytest.raises(TaskError, match="requires owner closeout"):
        q.admit_execution_wake(wake, provenance=None)
    assert q.list_wakes(task_id)[0].execution_send_state is None


@pytest.mark.parametrize("change", ["native", "generation", "steer_link"])
def test_changed_owner_or_link_cannot_admit_the_closeout_wake(q, change):
    task_id = bound(q, suspended=True)
    report(q, task_id)
    wake = q.claim_due_wake()
    with q._connect() as conn:
        if change == "steer_link":
            conn.execute("UPDATE task_steer SET wake_operation_id=NULL WHERE task_id=?", (task_id,))
        else:
            column, value = ("owner_session_id", "other") if change == "native" else ("generation", 99)
            conn.execute(f"UPDATE tasks SET {column}=? WHERE id=?", (value, task_id))
    with pytest.raises(TaskError, match="fence advanced"):
        q.admit_execution_wake(wake, provenance=None)
    assert q.list_wakes(task_id)[0].execution_send_state is None


def test_unrelated_wake_and_original_resume_never_inherit_closeout_admission(q):
    task_id = bound(q)
    q.submit_steer(task_id, fields={"old_goal": "continue"}, wake_requested=True)
    [old] = q.list_wakes(task_id)
    report(q, task_id)
    wake = q.claim_due_wake()
    assert old.id != wake.id and q.list_wakes(task_id)[0].status == "stale"
    with pytest.raises(TaskError, match="retained outbox operation"):
        q.admit_execution_wake(replace(wake, message="restart original goal"), provenance=None)
    before = q.get(task_id), q.list_wakes(task_id)
    q.resume(task_id, OWNER, wake_requested=True)
    assert (q.get(task_id), q.list_wakes(task_id)) == before
    assert q.admit_execution_wake(wake, provenance=None).identity is not None


def test_event_note_after_closeout_claim_cannot_supersede_the_durable_closeout(q):
    task_id = bound(q)
    report(q, task_id)
    wake = q.claim_due_wake()
    q.append_event_note(task_id, sender="source", note="Object is closed", wake_agent=True)
    assert q.get(task_id).wake_operation_id != wake.id
    admission = q.admit_execution_wake(wake, provenance=None)
    q.settle_execution_wake(wake, admission, outcome="sent", detail="original closeout accepted")
    assert q.finish_wake(wake.id, wake.delivery_token, delivered=True).status == "delivered"
    assert q.claim_due_wake() is None
    assert [w.status for w in q.list_wakes(task_id)] == ["delivered", "stale"]
    before = q.list_wakes(task_id)
    assert report(q, task_id, request_id="later-observation")["wake_operation_id"] == wake.id
    assert q.list_wakes(task_id) == before


@pytest.mark.parametrize("status", [Status.STARTED, Status.SUSPENDED, Status.SUBMITTED])
def test_actual_native_transport_keeps_original_conversation_and_closeout_prompt(
    hosted, monkeypatch, status,
):
    queue, task_id = hosted
    if status != Status.SUSPENDED:
        queue.resume(task_id, OWNER, reuse_session=True)
    if status == Status.SUBMITTED:
        with queue._connect() as conn:
            conn.execute("UPDATE tasks SET require_verification=1 WHERE id=?", (task_id,))
        queue.complete(task_id, OWNER)
    accepted = _hosted_report(queue, task_id)
    wake = queue.claim_due_wake()
    reservation = queue.latest_reservation(task_id)
    sent = []

    def send(bridge, native, prompt, **kwargs):
        assert queue.list_wakes(task_id)[-1].execution_send_state == "pending"
        sent.append((bridge, native, prompt, kwargs))
        return True

    monkeypatch.setattr("agent_dispatch.owner_wake_delivery.native_prompt", send)
    deliver = lambda: deliver_owner_wake(
        queue, wake.owner, wake.owner_session_id, task_id, wake.message, wake.id,
    )
    assert deliver_execution_wake(queue, wake, None, deliver)
    queue.finish_wake(wake.id, wake.delivery_token, delivered=True)
    assert sent[0][:2] == (BRIDGE, NATIVE)
    assert sent[0][3]["idempotency_key"] == wake.id
    assert "do not start or resume the goal" in sent[0][2]
    assert "--pool-recovery-revision" not in sent[0][2]
    assert queue.get(task_id).status == status
    assert queue.latest_reservation(task_id) == reservation
    assert queue.relevance_receipt(task_id, "closed-source")["wake"]["status"] == "delivered"
    assert accepted["decision"] == "closeout_pending"
    steer = queue.take_steer(task_id, OWNER)
    assert steer["fields"]["task_relevance"]["request_id"] == "closed-source"
    assert queue.get(task_id).status == status


def _hosted_report(queue, task_id):
    from agent_dispatch.queue_relevance import RelevanceScope
    with queue._connect() as conn:
        conn.execute("UPDATE tasks SET origin_ref='review-source' WHERE id=?", (task_id,))
    snapshot = queue.relevance_snapshot(task_id)
    return queue.reconcile_task_relevance(task_id, **request(
        queue, task_id, scope=RelevanceScope(snapshot["repo"], snapshot["source"], snapshot["origin_ref"]),
    ))


@pytest.mark.parametrize("event_note", [False, True])
def test_actual_default_drainer_delivers_closeout_to_original_native(hosted, monkeypatch, event_note):
    from agent_dispatch.events import EventBus
    from agent_dispatch.wake import drain_wake_outbox
    queue, task_id = hosted
    if event_note:
        queue.resume(task_id, OWNER, reuse_session=True)
    accepted = _hosted_report(queue, task_id)
    if event_note:
        queue.append_event_note(task_id, sender="source", note="Object is closed", wake_agent=True)
        assert queue.get(task_id).wake_operation_id != accepted["wake_operation_id"]
    calls = _capture(monkeypatch)

    async def scenario():
        drain = asyncio.create_task(drain_wake_outbox(
            queue, EventBus(), interval=0.01, retry_base=0.01,
        ))
        try:
            for _ in range(200):
                if all(w.status in {"delivered", "stale"} for w in queue.list_wakes(task_id)):
                    break
                await asyncio.sleep(0.01)
            else:
                pytest.fail("closeout wake did not deliver")
        finally:
            drain.cancel()
            with pytest.raises(asyncio.CancelledError):
                await drain

    asyncio.run(scenario())
    assert [argv for argv, _ in calls] == [_expected(accepted["wake_operation_id"])]
    assert "do not start or resume the goal" in calls[0][1]["input"]
    assert queue.get(task_id).status == (Status.STARTED if event_note else Status.SUSPENDED)
    assert [w.status for w in queue.list_wakes(task_id)] == (
        ["delivered", "stale"] if event_note else ["delivered"]
    )


@pytest.mark.parametrize("outcome", ["lost_response", "accepted"])
def test_closeout_unknown_and_sent_survive_restart_without_transport_replay(
    hosted, monkeypatch, outcome,
):
    queue, task_id = hosted
    _hosted_report(queue, task_id)
    wake = queue.claim_due_wake(lease_seconds=1)
    calls = []

    def deliver():
        calls.append(wake.id)
        if outcome == "lost_response":
            raise OSError("reply lost")
        return True

    if outcome == "lost_response":
        with pytest.raises(OSError, match="reply lost"):
            deliver_execution_wake(queue, wake, None, deliver)
    else:
        assert deliver_execution_wake(queue, wake, None, deliver)
    restarted = TaskQueue(queue.db_path)
    future = wake.delivery_expires_at + 1
    if outcome == "lost_response":
        assert restarted.recover_inflight_wakes(now=future) == 0
        assert restarted.claim_due_wake(now=future) is None
        with queue._connect() as conn:
            revision = _revision(_captured(queue, conn, task_id))
        result = reconcile_wake(
            queue, task_id, outbox="wake_outbox", wake_id=wake.id,
            expected_revision=revision, actor="owner", reason="observe original acceptance",
            observe=lambda *_args, **_kwargs: {
                "version": 1, "session_id": BRIDGE, "native_session_id": NATIVE,
                "invocation_key": wake.id, "admission": "accepted", "queued": False, "turn_index": 1,
            },
        )
        assert result["outcome"] == "sent"
    assert restarted.recover_inflight_wakes(now=future) == 1
    recovered = restarted.claim_due_wake(now=future)
    assert recovered.id == wake.id
    assert deliver_execution_wake(restarted, recovered, None, deliver)
    assert calls == [wake.id]
    restarted.finish_wake(recovered.id, recovered.delivery_token, delivered=True)
    assert restarted.get(task_id).status == Status.SUSPENDED
    with restarted._connect() as conn:
        identity = json.loads(conn.execute(
            "SELECT execution_send FROM wake_outbox WHERE id=?", (wake.id,),
        ).fetchone()["execution_send"])
    assert identity["protocol"] == "task_relevance_closeout/v1"

"""Durable owner-wake outbox semantics."""

from __future__ import annotations

import asyncio

import pytest

from agent_dispatch.events import EventBus
from agent_dispatch.queue import Status, TaskError
from agent_dispatch.wake import _next_wait_interval, drain_wake_outbox
from tests._helpers import RepoDefaultingQueue as TaskQueue


@pytest.fixture
def q(tmp_path):
    return TaskQueue(tmp_path / "tasks.db")


def _suspended(q, *, owner="host-a/wt-1"):
    task = q.create("wait for input")
    q.claim_one(owner, task_id=task.id)
    q.start(task.id, owner, owner_session_id="session-1")
    q.suspend(task.id, owner, reason="waiting")
    return task, owner


def _queued_wake(q):
    task, owner = _suspended(q)
    resumed = q.resume(
        task.id,
        owner,
        wake_requested=True,
        wake_message="continue",
        now=1000.0,
    )
    return resumed, q.list_wakes(task.id)[0]


def test_steer_state_and_wake_are_one_transaction(q, monkeypatch):
    task = q.create("wait for input")
    owner = "host-a/wt-1"
    q.claim_one(owner, task_id=task.id)
    q.start(task.id, owner, owner_session_id="session-1")
    q.set_card(
        task.id,
        owner,
        card={"request_input": [{"name": "decision", "type": "text"}]},
    )
    q.suspend(task.id, owner, reason="waiting")

    def fail_enqueue(*_args, **_kwargs):
        raise TaskError("outbox unavailable")

    monkeypatch.setattr(q, "_enqueue_wake", fail_enqueue)
    with pytest.raises(TaskError, match="outbox unavailable"):
        q.submit_steer(
            task.id,
            fields={"decision": "continue"},
            wake_requested=True,
        )

    unchanged = q.get(task.id)
    assert unchanged.status == Status.SUSPENDED
    assert unchanged.awaiting_steer is True
    assert q.steer_log(task.id) == []
    assert q.list_wakes(task.id) == []


def test_wake_notifier_runs_only_after_commit(q, monkeypatch):
    task, owner = _suspended(q)
    notifications = []
    q.set_wake_notifier(lambda: notifications.append(q.list_wakes(task.id)))

    q.resume(task.id, owner, wake_requested=True, now=1000.0)

    assert len(notifications) == 1
    assert [wake.status for wake in notifications[0]] == ["pending"]

    def fail_enqueue(*_args, **_kwargs):
        raise TaskError("outbox unavailable")

    monkeypatch.setattr(q, "_enqueue_wake", fail_enqueue)
    with pytest.raises(TaskError, match="outbox unavailable"):
        q.submit_steer(
            task.id,
            fields={"decision": "continue"},
            wake_requested=True,
        )
    assert len(notifications) == 1


def test_retry_reuses_operation_id_with_backoff(q):
    task, wake = _queued_wake(q)
    first = q.claim_due_wake(now=1000.0)
    assert first.id == wake.id
    assert first.attempts == 1

    retry = q.finish_wake(
        first.id,
        first.delivery_token,
        delivered=False,
        error="unavailable",
        retry_base=2.0,
        now=1001.0,
    )
    assert retry.status == "pending"
    assert retry.not_before == 1003.0
    assert q.claim_due_wake(now=1002.0) is None

    second = q.claim_due_wake(now=1003.0)
    assert second.id == first.id
    assert second.attempts == 2
    delivered = q.finish_wake(
        second.id,
        second.delivery_token,
        delivered=True,
        now=1004.0,
    )
    assert delivered.status == "delivered"
    assert q.get(task.id).wake_status == "delivered"


def test_restart_recovers_inflight_wake_with_same_id(q):
    task, wake = _queued_wake(q)
    claimed = q.claim_due_wake(now=1000.0)
    assert claimed.status == "delivering"

    restarted = TaskQueue(q.db_path)
    assert restarted.recover_inflight_wakes(now=1059.0) == 0
    assert restarted.claim_due_wake(now=1059.0) is None
    assert restarted.recover_inflight_wakes(now=1060.0) == 1
    recovered = restarted.claim_due_wake(now=1060.0)
    assert recovered.id == wake.id
    assert recovered.attempts == 2
    assert restarted.get(task.id).wake_status == "delivering"


def test_task_advance_fences_stale_wake(q):
    task, wake = _queued_wake(q)
    q.complete(task.id, "host-a/wt-1", result_ref="condition:satisfied")

    assert q.claim_due_wake(now=1000.0) is None
    [stale] = q.list_wakes(task.id)
    assert stale.id == wake.id
    assert stale.status == "stale"
    assert q.get(task.id).wake_status == "stale"


def test_newer_wake_supersedes_older_pending_wake(q):
    task, owner = _suspended(q)
    first = q.submit_steer(
        task.id,
        fields={"decision": "first"},
        wake_requested=True,
        wake_message="first",
        now=1000.0,
    )
    second = q.submit_steer(
        task.id,
        fields={"decision": "second"},
        wake_requested=True,
        wake_message="second",
        now=1001.0,
    )
    assert first.wake_operation_id != second.wake_operation_id

    claimed = q.claim_due_wake(now=1001.0)
    assert claimed.id == second.wake_operation_id
    wakes = q.list_wakes(task.id)
    assert [wake.status for wake in wakes] == ["stale", "delivering"]
    assert claimed.owner == owner


def test_coalesced_wake_drains_every_pending_steer(q):
    task, owner = _suspended(q)
    q.submit_steer(
        task.id,
        fields={"decision": "continue"},
        wake_requested=True,
    )
    q.submit_steer(
        task.id,
        fields={"detail": "use option B"},
        wake_requested=True,
    )

    wake = q.claim_due_wake()

    assert wake is not None and wake.wake_seq == 2
    steers = q.take_steer(task.id, owner, all_pending=True)
    assert [steer["fields"] for steer in steers] == [
        {"decision": "continue"},
        {"detail": "use option B"},
    ]
    assert q.take_steer(task.id, owner, all_pending=True) == []


def test_wake_metrics_surface_pending_and_terminal_counts(q):
    _task, _wake = _queued_wake(q)
    metrics = q.wake_metrics(now=1010.0)
    assert metrics["pending"] == 1
    assert metrics["oldest_pending_age"] == 10.0
    claimed = q.claim_due_wake(now=1010.0)
    q.finish_wake(
        claimed.id,
        claimed.delivery_token,
        delivered=False,
        max_attempts=1,
        now=1011.0,
    )
    metrics = q.wake_metrics(now=1012.0)
    assert metrics["pending"] == 0
    assert metrics["failed"] == 1


def test_drain_loop_retries_with_same_idempotency_key(q):
    task, wake = _queued_wake(q)
    calls = []

    def deliver(owner, owner_session_id, task_id, message, idempotency_key):
        calls.append((owner, owner_session_id, task_id, message, idempotency_key))
        return len(calls) >= 2

    async def scenario():
        loop = asyncio.create_task(
            drain_wake_outbox(
                q,
                EventBus(),
                interval=0.01,
                deliver=deliver,
                max_attempts=3,
                retry_base=0.01,
            )
        )
        try:
            for _ in range(200):
                if q.list_wakes(task.id)[0].status == "delivered":
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("wake outbox did not drain")
        finally:
            loop.cancel()
            with pytest.raises(asyncio.CancelledError):
                await loop

    asyncio.run(scenario())
    assert len(calls) == 2
    assert {call[1] for call in calls} == {"session-1"}
    assert {call[4] for call in calls} == {wake.id}


def test_idle_drainer_wakes_immediately_on_post_commit_signal(q):
    task, owner = _suspended(q)

    async def scenario():
        event_loop = asyncio.get_running_loop()
        signal: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
        delivered = asyncio.Event()

        def signal_wake():
            if signal.empty():
                signal.put_nowait(None)

        q.set_wake_notifier(
            lambda: event_loop.call_soon_threadsafe(signal_wake)
        )

        def deliver(*_args):
            event_loop.call_soon_threadsafe(delivered.set)
            return True

        drain_task = asyncio.create_task(
            drain_wake_outbox(
                q,
                EventBus(),
                interval=0.01,
                idle_interval=10.0,
                wake_signal=signal,
                deliver=deliver,
            )
        )
        try:
            await asyncio.sleep(0.05)
            await asyncio.to_thread(
                q.resume,
                task.id,
                owner,
                wake_requested=True,
                wake_message="continue",
            )
            await asyncio.wait_for(delivered.wait(), timeout=0.5)
            for _ in range(50):
                if q.list_wakes(task.id)[0].status == "delivered":
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("delivered wake was not committed")
        finally:
            drain_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await drain_task

    asyncio.run(scenario())
    assert q.list_wakes(task.id)[0].status == "delivered"


def test_pending_retry_keeps_short_poll_interval():
    assert _next_wait_interval(
        has_pending=True,
        retry_interval=0.25,
        idle_interval=5.0,
    ) == 0.25


def test_empty_outbox_uses_slow_recovery_interval():
    assert _next_wait_interval(
        has_pending=False,
        retry_interval=0.25,
        idle_interval=5.0,
    ) == 5.0


def test_missing_owner_session_fences_wake_stale(q):
    task = q.create("legacy")
    owner = "host-a/wt-1"
    q.claim_one(owner, task_id=task.id)
    q.start(task.id, owner)
    q.suspend(task.id, owner, reason="waiting")
    q.resume(task.id, owner, wake_requested=True, now=1000.0)

    assert q.claim_due_wake(now=1000.0) is None
    [wake] = q.list_wakes(task.id)
    assert wake.status == "stale"


def test_passive_drainer_waits_until_promoted(q):
    task, _wake = _queued_wake(q)
    active = False
    calls = []

    def is_active():
        return active

    def deliver(*args):
        calls.append(args)
        return True

    async def scenario():
        nonlocal active
        loop = asyncio.create_task(
            drain_wake_outbox(
                q, EventBus(), interval=0.01, deliver=deliver, is_active=is_active
            )
        )
        try:
            await asyncio.sleep(0.04)
            assert q.list_wakes(task.id)[0].status == "pending"
            assert calls == []
            active = True
            for _ in range(100):
                if q.list_wakes(task.id)[0].status == "delivered":
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("promoted drainer did not deliver")
        finally:
            loop.cancel()
            with pytest.raises(asyncio.CancelledError):
                await loop

    asyncio.run(scenario())
    assert len(calls) == 1


def test_delivery_exception_does_not_stop_drainer(q):
    first_task, _wake = _queued_wake(q)
    second_task, _owner = _suspended(q, owner="host-b/wt-2")
    q.resume(second_task.id, "host-b/wt-2", wake_requested=True, now=1001.0)
    calls = 0

    def deliver(*_args):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transport failed")
        return True

    async def scenario():
        loop = asyncio.create_task(
            drain_wake_outbox(
                q, EventBus(), interval=0.01, deliver=deliver, retry_base=10.0
            )
        )
        try:
            for _ in range(100):
                if q.list_wakes(second_task.id)[0].status == "delivered":
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("drainer stopped after delivery exception")
        finally:
            loop.cancel()
            with pytest.raises(asyncio.CancelledError):
                await loop

    asyncio.run(scenario())
    assert q.list_wakes(first_task.id)[0].status == "pending"


def test_delivery_token_loss_does_not_stop_drainer(q, monkeypatch):
    first_task, _wake = _queued_wake(q)
    second_task, _owner = _suspended(q, owner="host-b/wt-2")
    q.resume(second_task.id, "host-b/wt-2", wake_requested=True, now=1001.0)
    original_finish = q.finish_wake
    finishes = 0

    def finish_with_one_token_loss(*args, **kwargs):
        nonlocal finishes
        finishes += 1
        if finishes == 1:
            raise TaskError("delivery lease was recovered")
        return original_finish(*args, **kwargs)

    monkeypatch.setattr(q, "finish_wake", finish_with_one_token_loss)

    async def scenario():
        loop = asyncio.create_task(
            drain_wake_outbox(
                q, EventBus(), interval=0.01, deliver=lambda *_args: True
            )
        )
        try:
            for _ in range(100):
                if q.list_wakes(second_task.id)[0].status == "delivered":
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("drainer stopped after token loss")
        finally:
            loop.cancel()
            with pytest.raises(asyncio.CancelledError):
                await loop

    asyncio.run(scenario())
    assert q.list_wakes(first_task.id)[0].status == "delivering"

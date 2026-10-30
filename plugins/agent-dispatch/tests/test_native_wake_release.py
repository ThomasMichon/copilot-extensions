"""Persisted release state and admitted native wakes exclude one another."""

from __future__ import annotations

import asyncio
import subprocess
import threading

import pytest

from agent_dispatch import bridge
from agent_dispatch.coordinator_execution_wakes import deliver_execution_wake
from agent_dispatch.events import EventBus
from agent_dispatch.owner_wake_delivery import deliver_owner_wake
from agent_dispatch.queue import TaskError
from agent_dispatch.run_waiter_wake import drain_run_waiter_wakes
from agent_dispatch.wake import drain_wake_outbox
from tests.test_execution_wake_sends import INCARNATION, _qualify
from tests.test_native_owner_wake import NATIVE, OWNER, _capture, hosted as hosted
from tests.test_native_wake_overlap import _finish, _waiter, _wake_waiter


@pytest.mark.parametrize("operation", ["request", "fail"])
def test_persisted_release_after_initial_snapshot_refuses_native_send(hosted, monkeypatch, operation):
    queue, task_id = hosted
    latest = queue.latest_reservation

    def race(identifier):
        snapshot = latest(identifier)
        if operation == "request":
            queue.request_spawn_release(snapshot.key)
        else:
            queue.fail_spawn(snapshot.key, release_requested=True)
        return snapshot

    monkeypatch.setattr(queue, "latest_reservation", race)
    calls = _capture(monkeypatch)
    with pytest.raises(TaskError, match="hosted wake owner"):
        deliver_owner_wake(queue, OWNER, NATIVE, task_id, "continue", "wake-1")
    assert calls == []
    assert latest(task_id).release_requested is True


def test_admitted_default_waiter_wake_excludes_release_and_cleanup_until_task_completes(hosted, monkeypatch):
    from agent_dispatch.cleanup_recovery import validate_recovery

    queue, task_id = hosted
    waiter = _waiter(queue, task_id)
    _wake_waiter(queue, task_id, waiter)
    reservation = queue.latest_reservation(task_id)
    calls = []

    def run(argv, **kwargs):
        with pytest.raises(TaskError, match="wake delivery excludes physical release"):
            queue.request_spawn_release(reservation.key)
        with pytest.raises(TaskError, match="wake delivery excludes physical release"):
            queue.fail_spawn(reservation.key, release_requested=True)
        with queue._connect() as conn:
            row = conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (reservation.key,)).fetchone()
            with pytest.raises(TaskError, match="wake delivery excludes physical release"):
                validate_recovery(queue, conn, row)
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(bridge.subprocess, "run", run)

    async def scenario():
        loop = asyncio.create_task(drain_run_waiter_wakes(queue, interval=0.01))
        try:
            await _finish(queue, task_id)
        finally:
            loop.cancel()
            with pytest.raises(asyncio.CancelledError):
                await loop

    asyncio.run(scenario())
    assert len(calls) == 1
    assert queue.get_reservation(reservation.key).release_requested is False
    with pytest.raises(TaskError, match="current owner wake excludes physical release"):
        queue.request_spawn_release(reservation.key)
    queue.complete(task_id, OWNER, result_ref="condition:satisfied")
    assert queue.request_spawn_release(reservation.key).release_requested is True


def test_release_winning_first_keeps_default_waiter_wake_unsent_without_spending_attempts(hosted, monkeypatch):
    queue, task_id = hosted
    waiter = _waiter(queue, task_id)
    reservation = queue.latest_reservation(task_id)
    queue.request_spawn_release(reservation.key)
    _wake_waiter(queue, task_id, waiter)
    calls = _capture(monkeypatch)
    checked = threading.Event()
    claim = queue.claim_due_run_waiter_wake

    def observe(**kwargs):
        result = claim(**kwargs)
        if result is None:
            checked.set()
        return result

    monkeypatch.setattr(queue, "claim_due_run_waiter_wake", observe)

    async def scenario():
        loop = asyncio.create_task(drain_run_waiter_wakes(queue, interval=0.01))
        try:
            assert await asyncio.to_thread(checked.wait, 2)
            [pending] = queue.list_run_waiter_wakes(task_id)
            assert pending.status == "pending" and pending.attempts == 0
            assert calls == []
        finally:
            loop.cancel()
            with pytest.raises(asyncio.CancelledError):
                await loop

    asyncio.run(scenario())


def _queue_wake(queue, task_id, kind):
    if kind == "owner":
        queue.resume(
            task_id, OWNER, reuse_session=True, wake_requested=True, wake_message="continue",
        )
        return queue.list_wakes, queue.claim_due_wake, queue.finish_wake, queue.recover_inflight_wakes
    waiter = _waiter(queue, task_id)
    _wake_waiter(queue, task_id, waiter)
    return (
        queue.list_run_waiter_wakes, queue.claim_due_run_waiter_wake,
        queue.finish_run_waiter_wake, queue.recover_inflight_run_waiter_wakes,
    )


async def _pending_attempt(list_wakes, task_id):
    for _ in range(200):
        [wake] = list_wakes(task_id)
        if wake.status == "pending" and wake.attempts:
            return wake
        await asyncio.sleep(0.01)
    pytest.fail("attempted wake did not enter retry backoff")


@pytest.mark.parametrize("kind", ["owner", "waiter"])
def test_code_70_backoff_excludes_release_and_recovers_with_original_key(hosted, monkeypatch, kind):
    from agent_dispatch.cleanup_recovery import validate_recovery

    queue, task_id = hosted
    list_wakes, claim, finish, _recover = _queue_wake(queue, task_id, kind)
    [original] = list_wakes(task_id)
    calls = _capture(monkeypatch, unknown_first=True)

    async def scenario():
        drain = (
            drain_wake_outbox(queue, EventBus(), interval=0.01, retry_base=3600)
            if kind == "owner" else drain_run_waiter_wakes(queue, interval=0.01, retry_base=3600)
        )
        loop = asyncio.create_task(drain)
        try:
            return await _pending_attempt(list_wakes, task_id)
        finally:
            loop.cancel()
            with pytest.raises(asyncio.CancelledError):
                await loop

    pending = asyncio.run(scenario())
    assert pending.id == original.id and pending.execution_send_state is None
    assert len(calls) == 1
    reservation = queue.latest_reservation(task_id)
    with pytest.raises(TaskError, match="current owner wake excludes physical release"):
        queue.request_spawn_release(reservation.key)
    with queue._connect() as conn:
        row = conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (reservation.key,)).fetchone()
        with pytest.raises(TaskError, match="current owner wake excludes physical release"):
            validate_recovery(queue, conn, row)
    retry = claim(now=pending.not_before)
    assert retry.id == original.id
    assert deliver_execution_wake(
        queue, retry, None,
        lambda: deliver_owner_wake(queue, OWNER, NATIVE, task_id, retry.message, retry.id),
    )
    assert finish(retry.id, retry.delivery_token, delivered=True).status == "delivered"
    assert len(calls) == 2
    assert {argv[argv.index("--idempotency-key") + 1] for argv, _ in calls} == {original.id}
    assert queue.get_reservation(reservation.key).release_requested is False


@pytest.mark.parametrize("mode", ["owner-lease", "waiter-lease", "waiter-claim-release"])
def test_accepted_pending_recovery_keeps_release_and_cleanup_excluded(hosted, monkeypatch, mode):
    from agent_dispatch.cleanup_recovery import validate_recovery

    queue, task_id = hosted
    kind = mode.partition("-")[0]
    list_wakes, claim, _finish_wake, recover = _queue_wake(queue, task_id, kind)
    _qualify(queue, queue.get(task_id), monkeypatch)
    calls = _capture(monkeypatch)
    if mode.endswith("lease"):
        wake = claim()
        assert deliver_execution_wake(
            queue, wake, INCARNATION,
            lambda: deliver_owner_wake(queue, OWNER, NATIVE, task_id, wake.message, wake.id),
        )
        assert recover(now=wake.delivery_expires_at + 1) == 1
    else:
        async def scenario():
            loop = asyncio.create_task(drain_run_waiter_wakes(
                queue, interval=0.01, retry_base=3600, execution_incarnation=INCARNATION,
                release_claim=lambda *_args: None,
            ))
            try:
                await _pending_attempt(list_wakes, task_id)
            finally:
                loop.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await loop

        asyncio.run(scenario())
    [pending] = list_wakes(task_id)
    assert pending.status == "pending" and pending.execution_send_state == "sent"
    assert len(calls) == 1
    reservation = queue.latest_reservation(task_id)
    with pytest.raises(TaskError, match="current owner wake excludes physical release"):
        queue.request_spawn_release(reservation.key)
    with pytest.raises(TaskError, match="current owner wake excludes physical release"):
        queue.fail_spawn(reservation.key, release_requested=True)
    with queue._connect() as conn:
        row = conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (reservation.key,)).fetchone()
        with pytest.raises(TaskError, match="current owner wake excludes physical release"):
            validate_recovery(queue, conn, row)

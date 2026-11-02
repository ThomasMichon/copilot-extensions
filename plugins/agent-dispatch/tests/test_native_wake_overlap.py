"""Default native wake delivery excludes real queue cooling in both orders."""

from __future__ import annotations

import asyncio
import subprocess
import threading
from dataclasses import replace

import pytest

from agent_dispatch import bridge
from agent_dispatch.events import EventBus
from agent_dispatch.owner_wake_delivery import deliver_owner_wake
from agent_dispatch.queue import TaskError
from agent_dispatch.run_waiter_wake import drain_run_waiter_wakes
from agent_dispatch.wake import drain_wake_outbox
from tests.test_native_owner_wake import NATIVE, OWNER, _capture, hosted as hosted
from tests.test_restricted_supervisor import rig as rig


def _waiter(queue, task_id, *, pid=101):
    return queue.register_run_waiter(
        task_id, pid=pid, host="host-a", start_token=f"waiter-{pid}",
        resume_worktree=OWNER, command=["synthetic-wait"],
    )


def _wake_waiter(queue, task_id, waiter):
    queue.recover_dead_run_waiter(
        task_id, generation=waiter["generation"], reason="external",
        message="continue", sender="recovery",
    )


def _cool(queue, reservation):
    return queue.claim_spawn_cooling(
        reservation.key, startup_revision=reservation.startup_revision,
        session_handle=reservation.session_handle, state=reservation.state,
    )


async def _finish(queue, task_id):
    for _ in range(200):
        if queue.list_run_waiter_wakes(task_id)[0].status == "delivered":
            return
        await asyncio.sleep(0.01)
    pytest.fail("native waiter wake did not finish")


def test_outstanding_physical_stop_blocks_default_waiter_wake_until_cold_publication(hosted, monkeypatch):
    queue, task_id = hosted
    waiter = _waiter(queue, task_id)
    reservation = queue.latest_reservation(task_id)
    cooling = _cool(queue, reservation)
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
            assert calls == [] and queue.get_reservation(reservation.key).cooling_token == cooling.cooling_token
            queue.record_cold(
                reservation.key, startup_revision=reservation.startup_revision,
                cooling_token=cooling.cooling_token,
            )
            await _finish(queue, task_id)
        finally:
            loop.cancel()
            with pytest.raises(asyncio.CancelledError):
                await loop

    asyncio.run(scenario())
    assert len(calls) == 1
    assert calls[0][0][calls[0][0].index("--expected-native-session-id") + 1] == NATIVE


def test_native_wake_excludes_stop_during_send_and_until_new_waiter_parks(hosted, monkeypatch):
    queue, task_id = hosted
    waiter = _waiter(queue, task_id)
    _wake_waiter(queue, task_id, waiter)
    reservation = queue.latest_reservation(task_id)
    with pytest.raises(TaskError, match="wake excludes physical cooling"):
        _cool(queue, reservation)
    calls = []

    def run(argv, **kwargs):
        with pytest.raises(TaskError, match="wake delivery excludes physical cooling"):
            _cool(queue, reservation)
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
    with pytest.raises(TaskError, match="wake excludes physical cooling"):
        _cool(queue, reservation)
    newer = _waiter(queue, task_id, pid=202)
    assert newer["generation"] > waiter["generation"]
    assert _cool(queue, reservation).cooling_token


def test_default_drainer_preserves_restricted_owner_and_receipt_native_identity(rig, monkeypatch):
    assert rig.supervisor.poll_once() == [rig.task.id]
    task = rig.queue.get(rig.task.id)
    rig.queue.start(task.id, task.owner, owner_session_id=task.owner_session_id)
    rig.queue.suspend(task.id, task.owner, reason="external")
    rig.queue.resume(
        task.id, task.owner, reuse_session=True, wake_requested=True, wake_message="continue",
    )
    [wake] = rig.queue.list_wakes(task.id)
    calls = _capture(monkeypatch)
    monkeypatch.setattr(bridge, "_agent_bridge_launch_prefix", lambda: ["bridge"])
    monkeypatch.setattr(bridge, "resume_steered_owner", lambda *_a, **_k: pytest.fail("not a CLI owner"))

    async def scenario():
        loop = asyncio.create_task(drain_wake_outbox(rig.queue, EventBus(), interval=0.01))
        try:
            for _ in range(200):
                if rig.queue.list_wakes(task.id)[0].status == "delivered":
                    break
                await asyncio.sleep(0.01)
            else:
                pytest.fail("restricted native wake did not deliver")
        finally:
            loop.cancel()
            with pytest.raises(asyncio.CancelledError):
                await loop

    asyncio.run(scenario())
    assert len(calls) == 1
    argv = calls[0][0]
    assert argv[:4] == ["bridge", "--json", "send", "bridge-worker"]
    assert argv[argv.index("--expected-native-session-id") + 1] == "native-worker"
    assert argv[argv.index("--idempotency-key") + 1] == wake.id
    assert rig.queue.get(task.id).owner == task.owner


@pytest.mark.parametrize("change", [
    {"worker_id": "other-owner"}, {"native_session_id": "other-native"},
    {"session_id": "other-bridge"}, {"task_generation": 999},
    {"delivered": False}, {"kind": "ordinary-bridge"},
])
def test_restricted_wake_rejects_changed_receipt_without_cli_fallback(rig, monkeypatch, change):
    assert rig.supervisor.poll_once() == [rig.task.id]
    task = rig.queue.get(rig.task.id)
    original = rig.queue.latest_reservation(task.id)
    monkeypatch.setattr(rig.queue, "latest_reservation", lambda _id: replace(
        original, agent_info={**original.agent_info, **change},
    ))
    calls = _capture(monkeypatch)
    monkeypatch.setattr(bridge, "resume_steered_owner", lambda *_a, **_k: pytest.fail("not a CLI owner"))
    with pytest.raises(TaskError):
        deliver_owner_wake(rig.queue, task.owner, task.owner_session_id, task.id, "continue", "wake-1")
    assert calls == []

"""Durable detached-wait wake delivery semantics."""

from __future__ import annotations

import asyncio

import pytest

from agent_dispatch.run_waiter_wake import drain_run_waiter_wakes
from tests._helpers import RepoDefaultingQueue as TaskQueue

TEST_HOST = "test-host"


@pytest.fixture
def q(tmp_path):
    return TaskQueue(tmp_path / "tasks.db")


def _suspended(q: TaskQueue, *, owner: str = "host-a/wt-1") -> tuple[str, str]:
    task = q.create("wait")
    q.claim_one(owner, task_id=task.id)
    q.start(task.id, owner, owner_session_id="session-1")
    q.suspend(task.id, owner, reason="waiting on external state")
    return task.id, owner


def _queued_run_waiter_wake(q: TaskQueue):
    task_id, owner = _suspended(q)
    waiter = q.register_run_waiter(
        task_id,
        pid=101,
        host=TEST_HOST,
        start_token="token-101",
        resume_worktree="m/wt-1",
        command=["sleep", "1"],
        now=1000.0,
    )
    retired = q.recover_dead_run_waiter(
        task_id,
        generation=waiter["generation"],
        reason="infrastructure failure",
        message="wake up",
        sender="agent-dispatch-run-waiter-recovery",
        now=1001.0,
    )
    [wake] = q.list_run_waiter_wakes(task_id)
    return task_id, owner, retired, wake


def test_run_waiter_wake_drainer_retries_and_releases_claim(q):
    task_id, owner, _retired, wake = _queued_run_waiter_wake(q)
    calls = []
    releases = []

    def deliver(owner_arg, task_id_arg, message, wake_id, sender, owner_session_id):
        calls.append((owner_arg, task_id_arg, message, wake_id, sender, owner_session_id))
        return len(calls) >= 2

    async def scenario():
        loop = asyncio.create_task(
            drain_run_waiter_wakes(
                q,
                interval=0.01,
                deliver=deliver,
                release_claim=lambda task_id, host, worktree, repo: releases.append((task_id, host, worktree, repo)) or {"released": True},
                retry_base=0.01,
            )
        )
        try:
            for _ in range(200):
                if q.list_run_waiter_wakes(task_id)[0].status == "delivered":
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("run waiter wake did not drain")
        finally:
            loop.cancel()
            with pytest.raises(asyncio.CancelledError):
                await loop

    asyncio.run(scenario())
    assert len(calls) == 2
    assert {call[0] for call in calls} == {owner}
    assert {call[1] for call in calls} == {task_id}
    assert {call[3] for call in calls} == {wake.id}
    assert {call[4] for call in calls} == {"agent-dispatch-run-waiter-recovery"}
    assert {call[5] for call in calls} == {"session-1"}
    assert releases == [(task_id, TEST_HOST, "wt-1", "example.com/acme/widget")]


def test_run_waiter_wake_restart_recovers_inflight_lease(q):
    task_id, _owner, _retired, wake = _queued_run_waiter_wake(q)
    claimed = q.claim_due_run_waiter_wake(now=1002.0)
    assert claimed is not None and claimed.status == "delivering"

    restarted = TaskQueue(q.db_path)
    assert restarted.recover_inflight_run_waiter_wakes(now=1062.0) == 1
    recovered = restarted.claim_due_run_waiter_wake(now=1062.0)
    assert recovered is not None
    assert recovered.id == wake.id
    assert recovered.attempts == 2


def test_run_waiter_wake_task_advance_fences_stale_delivery(q):
    task_id, owner, _retired, wake = _queued_run_waiter_wake(q)
    q.complete(task_id, owner, result_ref="condition:satisfied")

    assert q.claim_due_run_waiter_wake(now=1002.0) is None
    [stale] = q.list_run_waiter_wakes(task_id)
    assert stale.id == wake.id
    assert stale.status == "stale"


def test_run_waiter_wake_retries_when_claim_release_fails(q):
    task_id, _owner, _retired, _wake = _queued_run_waiter_wake(q)
    deliveries = []

    def deliver(*args):
        deliveries.append(args)
        return True

    releases = []

    async def scenario():
        loop = asyncio.create_task(
            drain_run_waiter_wakes(
                q,
                interval=0.01,
                deliver=deliver,
                release_claim=lambda task_id, host, worktree, repo: releases.append((task_id, host, worktree, repo)) or None,
                retry_base=0.01,
                max_attempts=2,
            )
        )
        try:
            for _ in range(200):
                status = q.list_run_waiter_wakes(task_id)[0].status
                if status == "failed":
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("run waiter wake did not exhaust retries")
        finally:
            loop.cancel()
            with pytest.raises(asyncio.CancelledError):
                await loop

    asyncio.run(scenario())
    assert len(deliveries) == 2
    assert len(releases) == 2


def test_run_waiter_wake_skips_claim_release_after_newer_waiter_prepares(q):
    task_id, owner, _retired, _wake = _queued_run_waiter_wake(q)
    releases = []
    prepared = []

    def deliver(*_args):
        if not prepared:
            prepared.append(
                q.prepare_run_waiter(
                    task_id,
                    worker_id=owner,
                    resume_worktree="m/wt-1",
                    command=["sleep", "1"],
                    reason="hibernating: sleep 1",
                )
            )
        return True

    async def scenario():
        loop = asyncio.create_task(
            drain_run_waiter_wakes(
                q,
                interval=0.01,
                deliver=deliver,
                release_claim=lambda task_id, host, worktree, repo: releases.append((task_id, host, worktree, repo)) or {"released": True},
                retry_base=0.01,
                max_attempts=1,
            )
        )
        try:
            for _ in range(200):
                status = q.list_run_waiter_wakes(task_id)[0].status
                if status == "stale":
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError("run waiter wake was not fenced stale")
        finally:
            loop.cancel()
            with pytest.raises(asyncio.CancelledError):
                await loop

    asyncio.run(scenario())
    assert releases == []
    assert prepared and prepared[0]["generation"] == 2

from __future__ import annotations

import asyncio

import pytest

from agent_dispatch import coordinator as coordinator_module
from agent_dispatch.supervisor import Supervisor
from tests._helpers import TEST_REPO
from tests._helpers import RepoDefaultingQueue as TaskQueue
from tests.test_supervisor import QueueBackedClient, _ok_spawn
from tests.test_supervisor_daemon import FakeClient, FakeLauncher, FakeLock, _daemon


class _Bus:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def publish(self, payload: dict) -> None:
        self.events.append(payload)


class _Governance:
    def __init__(self, states: list[dict[str, object]]) -> None:
        self._states = iter(states)
        self.calls: list[str] = []

    def recheck(self, checkpoint: str) -> dict[str, object]:
        self.calls.append(checkpoint)
        return next(self._states)


def test_gc_loop_backs_off_at_iteration_boundary_without_mutating(monkeypatch):
    health = coordinator_module.LoopHealth(name="gc", base_interval=30.0)
    governance = _Governance([
        {"status": "backoff", "reason": "maintenance-active"},
    ])
    bus = _Bus()
    runs: list[str] = []
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)
        if len(sleeps) >= 2:
            raise asyncio.CancelledError

    async def fake_cycle(*_args, **_kwargs):
        runs.append("cycle")
        return {}

    monkeypatch.setattr(coordinator_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(coordinator_module, "_run_supervised_cycle", fake_cycle)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            coordinator_module._gc_loop(
                TaskQueue(":memory:"),
                30.0,
                bus,
                health=health,
                governance=governance,
            )
        )

    assert governance.calls == ["iteration-boundary:liveness-gc"]
    assert sleeps == [health.current_interval, coordinator_module._GOVERNANCE_BACKOFF_SECONDS]
    assert runs == []
    assert bus.events == []


def test_gc_loop_rechecks_before_reconcile_and_catches_mid_iteration_maintenance(monkeypatch):
    health = coordinator_module.LoopHealth(name="gc", base_interval=30.0)
    governance = _Governance([
        {"status": "ready", "reason": "baseline-established", "baseline": {}},
        {"status": "backoff", "reason": "maintenance-active"},
    ])
    bus = _Bus()
    runs: list[str] = []
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)
        if len(sleeps) >= 2:
            raise asyncio.CancelledError

    async def fake_cycle(*_args, **_kwargs):
        runs.append("cycle")
        return {}

    monkeypatch.setattr(coordinator_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(coordinator_module, "_run_supervised_cycle", fake_cycle)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            coordinator_module._gc_loop(
                TaskQueue(":memory:"),
                30.0,
                bus,
                health=health,
                governance=governance,
            )
        )

    assert governance.calls == [
        "iteration-boundary:liveness-gc",
        "pre-mutation:reconcile-liveness",
    ]
    assert sleeps == [health.current_interval, coordinator_module._GOVERNANCE_BACKOFF_SECONDS]
    assert runs == []


def test_supervisor_requeues_reserved_task_when_revalidation_fails_before_spawn(monkeypatch, tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    client = QueueBackedClient(queue)
    task = queue.create("work", repo=TEST_REPO)
    sup = Supervisor(client, spawn_fn=_ok_spawn(), repo=TEST_REPO, max_concurrent=5)
    governance = _Governance([
        {"status": "ready", "reason": "baseline-established", "baseline": {}},
        {"status": "revalidation-required", "reason": "generation-changed"},
    ])
    sup.set_governance_recheck_fn(governance.recheck)
    monkeypatch.setattr(
        sup,
        "_prepare_spawn_task",
        lambda task_row, reservation: {
            "id": task_row["id"],
            "spawn_worktree_ownership": "reused",
        },
    )

    assert sup.poll_once() == []

    reservations = client.list_reservations(task_id=task.id)
    assert reservations[-1]["state"] == "failed"
    assert queue.get(task.id).status == "queued"
    assert governance.calls == [
        "pre-mutation:reserve-spawn",
        "pre-mutation:spawn",
    ]


def test_supervisor_proceeds_when_iteration_and_pre_mutation_checks_stay_current(tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    client = QueueBackedClient(queue)
    task = queue.create("work", repo=TEST_REPO)
    spawn = _ok_spawn()
    sup = Supervisor(client, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5)
    governance = _Governance([
        {"status": "ready", "reason": "baseline-established", "baseline": {}},
        {"status": "ready", "reason": "current", "baseline": {}},
        {"status": "ready", "reason": "current", "baseline": {}},
    ])
    sup.set_governance_recheck_fn(governance.recheck)
    waits: list[float] = []

    def wait_for_turn_end(timeout: float) -> bool:
        waits.append(timeout)
        raise KeyboardInterrupt

    sup.wait_for_turn_end = wait_for_turn_end
    sup.serve(interval=30.0)

    reservations = client.list_reservations(task_id=task.id)
    assert reservations[-1]["state"] == "spawned"
    assert spawn.calls == [task.id]
    assert waits == [30.0]
    assert governance.calls == [
        "iteration-boundary",
        "pre-mutation:reserve-spawn",
        "pre-mutation:spawn",
    ]


def test_supervisor_daemon_backs_off_at_iteration_boundary_without_reconciling():
    daemon = _daemon(FakeClient([]), FakeLauncher(), lock=FakeLock(granted=True))
    governance = _Governance([
        {"status": "backoff", "reason": "maintenance-active"},
    ])
    daemon.set_governance_recheck_fn(governance.recheck)
    reconciles: list[str] = []
    daemon.reconcile_once = lambda: reconciles.append("reconcile")  # type: ignore[assignment]
    assert daemon.serve(once=True) == 0
    assert governance.calls == ["iteration-boundary"]
    assert reconciles == []


def test_supervisor_daemon_rechecks_before_self_update_spawn():
    spawned: list[tuple[object, list[str]]] = []
    target = object()
    daemon = _daemon(
        FakeClient([]),
        FakeLauncher(),
        lock=FakeLock(granted=True),
        self_update_enabled=True,
        self_update_argv=["supervise", "serve"],
        self_update_stale_target=lambda _root, _version: target,
        self_update_spawn=lambda result, argv: spawned.append((result, argv)),
    )
    governance = _Governance([
        {"status": "ready", "reason": "baseline-established", "baseline": {}},
        {"status": "ready", "reason": "current", "baseline": {}},
        {"status": "backoff", "reason": "maintenance-active"},
    ])
    daemon.set_governance_recheck_fn(governance.recheck)

    assert daemon.serve(once=True) == 0
    assert spawned == []
    assert governance.calls == [
        "iteration-boundary",
        "pre-mutation:reconcile",
        "pre-mutation:self-update",
    ]

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


@pytest.mark.parametrize("loop_name", [
    "_gc_loop", "_orphan_reap_loop", "_handoff_fallback_loop", "_worktree_status_relay_loop",
])
@pytest.mark.parametrize("unavailable", [False, True])
def test_passive_reconcile_loops_preserve_queue_before_ownership(
    monkeypatch, tmp_path, loop_name, unavailable,
):
    queue = TaskQueue(tmp_path / "tasks.db")
    task = queue.create("unfinished", repo=TEST_REPO)
    reservation, _ = queue.reserve_spawn(task.id)
    before = queue.get(task.id)
    bus = _Bus()
    sleeps = []
    runs = []

    def is_active():
        if unavailable:
            raise OSError("routing unavailable")
        return False

    async def sleep(delay):
        sleeps.append(delay)
        if len(sleeps) == 2:
            raise asyncio.CancelledError

    async def run_cycle(*_args, **_kwargs):
        runs.append("unexpected mutation")
        return {}

    monkeypatch.setattr(coordinator_module.asyncio, "sleep", sleep)
    monkeypatch.setattr(coordinator_module, "_run_supervised_cycle", run_cycle)
    options = {}
    if loop_name == "_worktree_status_relay_loop":
        from agent_dispatch.worktree_status_relay import WorktreeStatusRelayStore

        relay = WorktreeStatusRelayStore(tmp_path / "relay.db")
        monkeypatch.setattr(relay, "prune", lambda **_kwargs: pytest.fail("passive relay mutation"))
        options = {"relay": relay, "signal": asyncio.Queue()}
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(getattr(coordinator_module, loop_name)(
            queue, 0.01, bus,
            health=coordinator_module.LoopHealth(name=loop_name, base_interval=0.01),
            is_active=is_active, **options,
        ))
    assert runs == []
    assert bus.events == []
    assert queue.get(task.id) == before
    assert queue.latest_reservation(task.id).key == reservation.key


def test_gc_demoted_mid_iteration_does_not_reconcile_cooldowns(monkeypatch, tmp_path):
    queue = TaskQueue(tmp_path / "tasks.db")
    ownership = iter([True, True, False])
    calls = []
    sleeps = []

    async def sleep(_delay):
        sleeps.append(None)
        if len(sleeps) == 2:
            raise asyncio.CancelledError

    async def run_cycle(_health, operation, **_kwargs):
        calls.append(operation)
        return {}

    monkeypatch.setattr(coordinator_module.asyncio, "sleep", sleep)
    monkeypatch.setattr(coordinator_module, "_run_supervised_cycle", run_cycle)
    monkeypatch.setattr(queue, "reconcile_cooldowns", lambda: pytest.fail("demoted mutation"))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(coordinator_module._gc_loop(
            queue, 0.01, _Bus(),
            health=coordinator_module.LoopHealth(name="gc", base_interval=0.01),
            is_active=lambda: next(ownership),
        ))
    assert calls == [queue.reconcile_liveness]


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


def test_gc_loop_uses_coordinator_backoff_seam(monkeypatch):
    health = coordinator_module.LoopHealth(name="gc", base_interval=30.0)
    governance = _Governance([
        {"status": "backoff", "reason": "maintenance-active"},
    ])
    bus = _Bus()
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)
        if len(sleeps) >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(coordinator_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(coordinator_module, "_GOVERNANCE_BACKOFF_SECONDS", 0.25)

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

    assert sleeps == [health.current_interval, 0.25]


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


@pytest.mark.parametrize("ownership", ["reused", "created"])
def test_supervisor_requeues_reserved_task_when_revalidation_fails_before_spawn(monkeypatch, tmp_path, ownership):
    queue = TaskQueue(tmp_path / "tasks.db")
    client = QueueBackedClient(queue)
    task = queue.create("work", repo=TEST_REPO)
    spawn = _ok_spawn()
    cleaned = []
    sup = Supervisor(
        client, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=1,
        verdict_fn=lambda *_: "gone", machine="host-a",
        worktree_directory_present_fn=lambda *_: False,
        attempt_conclusion_fn=lambda *args: cleaned.append(args) or {"action": "primed"},
    )
    governance = _Governance([
        {"status": "ready", "reason": "baseline-established", "baseline": {}},
        {"status": "revalidation-required", "reason": "generation-changed"},
    ])
    sup.set_governance_recheck_fn(governance.recheck)
    def prepare(task_row, reservation):
        queue.record_spawn_worktree(
            reservation["key"], "prepared-worktree", ownership=ownership,
            creating_host="host-a", allocation_project="test",
            driver="agent-dispatch",
            worktree_path=str(tmp_path / "prepared-worktree"),
        )
        return {
            "id": task_row["id"],
            "spawn_worktree_ownership": ownership,
        }

    monkeypatch.setattr(sup, "_prepare_spawn_task", prepare)

    assert sup.poll_once() == []

    reservations = client.list_reservations(task_id=task.id)
    assert spawn.calls == []
    if ownership == "created":
        assert reservations[-1]["state"] == "releasing"
        assert reservations[-1]["startup_fenced"] is True
        assert reservations[-1]["startup_complete"] is False
        sup.set_governance_recheck_fn(lambda _: {"status": "ready"})
        assert sup.release_requested_bodies() == 1
        assert len(cleaned) == 1
        assert sup._active_reservations() == []
    assert queue.latest_reservation(task.id).state == "failed"
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

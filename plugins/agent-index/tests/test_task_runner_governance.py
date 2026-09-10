from __future__ import annotations

import asyncio
from types import SimpleNamespace

from agent_index.indexing import runner as runner_module


class _EventBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def publish(self, event: str, payload: dict) -> None:
        self.events.append((event, payload))


class _Store:
    def __init__(self, *, pending: int = 0, task: object | None = None) -> None:
        self.pending = pending
        self.task = task
        self.calls: list[object] = []

    def get_pending_count(self) -> int:
        self.calls.append("pending")
        return self.pending

    def dequeue_next(self):
        self.calls.append("dequeue")
        return self.task

    def release_processing(self, task_id: str) -> bool:
        self.calls.append(("release", task_id))
        return True

    def set_worker(self, task_id: str, pid: int, host: str, version: str) -> None:
        self.calls.append(("set_worker", task_id, pid, host, version))

    @property
    def data_dir(self):
        return None


def _run(coro) -> None:
    asyncio.run(coro)


def test_worker_loop_backs_off_at_iteration_boundary_without_mutating(monkeypatch):
    store = _Store()
    runner = runner_module.TaskRunner(store, _EventBus())
    governance_calls: list[str] = []
    waits: list[float] = []

    async def no_sleep(_delay: float) -> None:
        return None

    async def fake_wait(timeout: float) -> None:
        waits.append(timeout)
        runner._shutdown = True

    runner.set_governance_recheck_fn(
        lambda checkpoint: governance_calls.append(checkpoint)
        or {"status": "backoff", "reason": "maintenance-active"}
    )
    monkeypatch.setattr(runner_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(runner, "_wait_for_wake", fake_wait)

    _run(runner._worker_loop())

    assert governance_calls == ["iteration-boundary"]
    assert waits == [runner_module._GOVERNANCE_BACKOFF_SECONDS]
    assert store.calls == []


def test_worker_loop_rechecks_before_dequeue_and_catches_mid_iteration_maintenance(
    monkeypatch,
):
    store = _Store(pending=1)
    runner = runner_module.TaskRunner(store, _EventBus())
    governance_calls: list[str] = []
    waits: list[float] = []
    states = iter(
        [
            {"status": "ready", "reason": "baseline-established", "baseline": {}},
            {"status": "backoff", "reason": "maintenance-active"},
        ]
    )

    async def no_sleep(_delay: float) -> None:
        return None

    async def fake_wait(timeout: float) -> None:
        waits.append(timeout)
        runner._shutdown = True

    def recheck(checkpoint: str) -> dict[str, object]:
        governance_calls.append(checkpoint)
        return next(states)

    runner.set_governance_recheck_fn(recheck)
    monkeypatch.setattr(runner_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(runner, "_wait_for_wake", fake_wait)

    _run(runner._worker_loop())

    assert governance_calls == ["iteration-boundary", "pre-mutation:dequeue"]
    assert waits == [runner_module._GOVERNANCE_BACKOFF_SECONDS]
    assert store.calls == ["pending"]


def test_worker_loop_detects_stale_generation_before_dequeue(monkeypatch):
    store = _Store(pending=1)
    runner = runner_module.TaskRunner(store, _EventBus())
    governance_calls: list[str] = []
    waits: list[float] = []
    states = iter(
        [
            {"status": "ready", "reason": "baseline-established", "baseline": {}},
            {"status": "revalidation-required", "reason": "generation-changed"},
        ]
    )

    async def no_sleep(_delay: float) -> None:
        return None

    async def fake_wait(timeout: float) -> None:
        waits.append(timeout)
        runner._shutdown = True

    runner.set_governance_recheck_fn(
        lambda checkpoint: governance_calls.append(checkpoint) or next(states)
    )
    monkeypatch.setattr(runner_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(runner, "_wait_for_wake", fake_wait)

    _run(runner._worker_loop())

    assert governance_calls == ["iteration-boundary", "pre-mutation:dequeue"]
    assert waits == [runner_module._GOVERNANCE_BACKOFF_SECONDS]
    assert store.calls == ["pending"]


def test_worker_loop_detects_tombstone_change_before_dequeue(monkeypatch):
    store = _Store(pending=1)
    runner = runner_module.TaskRunner(store, _EventBus())
    governance_calls: list[str] = []
    waits: list[float] = []
    states = iter(
        [
            {"status": "ready", "reason": "baseline-established", "baseline": {}},
            {"status": "revalidation-required", "reason": "tombstone-changed"},
        ]
    )

    async def no_sleep(_delay: float) -> None:
        return None

    async def fake_wait(timeout: float) -> None:
        waits.append(timeout)
        runner._shutdown = True

    runner.set_governance_recheck_fn(
        lambda checkpoint: governance_calls.append(checkpoint) or next(states)
    )
    monkeypatch.setattr(runner_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(runner, "_wait_for_wake", fake_wait)

    _run(runner._worker_loop())

    assert governance_calls == ["iteration-boundary", "pre-mutation:dequeue"]
    assert waits == [runner_module._GOVERNANCE_BACKOFF_SECONDS]
    assert store.calls == ["pending"]


def test_worker_loop_rechecks_before_launch_and_requeues_claimed_task(monkeypatch):
    task = SimpleNamespace(
        id="task-1",
        source="all",
        full=False,
        trigger_source="test",
    )
    store = _Store(pending=1, task=task)
    runner = runner_module.TaskRunner(store, _EventBus())
    governance_calls: list[str] = []
    waits: list[float] = []
    launched: list[str] = []
    states = iter(
        [
            {"status": "ready", "reason": "baseline-established", "baseline": {}},
            {"status": "ready", "reason": "current", "baseline": {}},
            {"status": "backoff", "reason": "maintenance-active"},
        ]
    )

    async def no_sleep(_delay: float) -> None:
        return None

    async def fake_wait(timeout: float) -> None:
        waits.append(timeout)
        runner._shutdown = True

    async def fake_launch(task_record) -> None:
        launched.append(task_record.id)
        runner._shutdown = True

    runner.set_governance_recheck_fn(
        lambda checkpoint: governance_calls.append(checkpoint) or next(states)
    )
    monkeypatch.setattr(runner_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(runner, "_wait_for_wake", fake_wait)
    monkeypatch.setattr(runner, "_launch_worker", fake_launch)

    _run(runner._worker_loop())

    assert governance_calls == [
        "iteration-boundary",
        "pre-mutation:dequeue",
        "pre-mutation:launch",
    ]
    assert waits == [runner_module._GOVERNANCE_BACKOFF_SECONDS]
    assert store.calls == ["pending", "dequeue", ("release", "task-1")]
    assert launched == []


def test_worker_loop_proceeds_when_iteration_and_pre_mutation_checks_stay_current(
    monkeypatch,
):
    task = SimpleNamespace(
        id="task-1",
        source="all",
        full=False,
        trigger_source="test",
    )
    store = _Store(pending=1, task=task)
    runner = runner_module.TaskRunner(store, _EventBus())
    governance_calls: list[str] = []
    launched: list[str] = []
    states = iter(
        [
            {"status": "ready", "reason": "baseline-established", "baseline": {}},
            {"status": "ready", "reason": "current", "baseline": {}},
            {"status": "ready", "reason": "current", "baseline": {}},
        ]
    )

    async def no_sleep(_delay: float) -> None:
        return None

    async def fake_launch(task_record) -> None:
        launched.append(task_record.id)
        runner._shutdown = True

    runner.set_governance_recheck_fn(
        lambda checkpoint: governance_calls.append(checkpoint) or next(states)
    )
    monkeypatch.setattr(runner_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(runner, "_launch_worker", fake_launch)

    _run(runner._worker_loop())

    assert governance_calls == [
        "iteration-boundary",
        "pre-mutation:dequeue",
        "pre-mutation:launch",
    ]
    assert store.calls == ["pending", "dequeue"]
    assert launched == ["task-1"]

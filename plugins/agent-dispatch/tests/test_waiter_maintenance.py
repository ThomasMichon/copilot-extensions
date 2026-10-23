from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock

import pytest

from agent_dispatch.coordinator import DrainGate, create_app
from agent_dispatch.delegated_replay_consumer import ReplayConsumer
from agent_dispatch.run_waiter_recovery import maintain_delegated_recovery
from agent_dispatch.waiter_maintenance import WaiterMaintenance
from tests._delegation_helpers import prepare, task
from tests._helpers import RepoDefaultingQueue as TaskQueue
from tests.test_delegated_replay_consumer import BINDING, Service, intent

pytestmark = pytest.mark.contract("agent_dispatch.delegated_replay")


def owner(tmp_path, *, active=lambda: True, service_type=Service):
    queue = TaskQueue(tmp_path / "tasks.db")
    service = service_type(queue)
    gate = DrainGate()
    clock = [0.0]

    def factory(queue, checkpoint):
        return ReplayConsumer(
            queue, binding=BINDING, client=service,
            request_factory=lambda request_id, issued_at: (request_id, issued_at),
            checkpoint=checkpoint,
        )

    maintenance = WaiterMaintenance(
        queue, AsyncMock(), asyncio.Queue(maxsize=1), gate=gate, is_active=active,
        replay_factory=factory, clock=lambda: clock[0],
    )
    return queue, service, gate, clock, maintenance, factory


async def finish_cycle(maintenance):
    running = maintenance._replay_task
    await running
    await asyncio.sleep(0)
    assert maintenance._replay_task is None


@pytest.mark.timeout(120 if os.name == "nt" else 30)
def test_shared_tick_starts_one_cycle_for_many_waiters(tmp_path):
    async def run():
        queue, service, gate, _, maintenance, _ = owner(tmp_path)
        for index in range(40):
            spec = intent(queue, str(index))
            prepare(queue, task(queue), spec)
        await maintenance.tick()
        first = maintenance._replay_task
        for _ in range(40):
            await maintenance.tick()
            assert maintenance._replay_task is first
        await finish_cycle(maintenance)
        assert gate.claims == 0
        assert maintenance.status["last_result"]["checked"] == 32
        assert len([name for name, _ in service.calls if name == "register"]) == 32
        assert maintenance.status["cycles"] == 1
        await maintenance.close()

    asyncio.run(run())


@pytest.mark.parametrize("loss", ["drain", "demote", "closed", "unknown"])
def test_known_loss_stops_later_effects_and_original_request_reconciles(tmp_path, loss):
    async def run():
        active = [True]

        def is_active():
            if active[0] == "unknown":
                raise OSError("private authority detail")
            return active[0]

        queue, service, gate, clock, maintenance, _ = owner(tmp_path, active=is_active)
        first = task(queue)
        spec = intent(queue)
        prepare(queue, first, spec)
        prepare(queue, task(queue), intent(queue, "later"))

        def disconnect():
            if loss == "drain":
                gate.set_draining(True)
            elif loss == "closed":
                maintenance._closed = True
            else:
                active[0] = False if loss == "demote" else "unknown"

        service.on_register = disconnect
        await maintenance.tick()
        await finish_cycle(maintenance)
        assert maintenance.status["last_result"]["blocked"]
        assert [name for name, _ in service.calls] == ["capabilities", "register"]
        original = queue.list_pending_run_waiters()[0]
        assert original["replay_attempted"]
        assert not original["delegation"].get("registration_id")
        assert gate.claims == 0
        await maintenance.close()

        service.on_register = None
        active[0] = True
        restored = TaskQueue(tmp_path / "tasks.db")

        def factory(queue, checkpoint):
            return ReplayConsumer(
                queue, binding=BINDING, client=service,
                request_factory=lambda request_id, issued_at: (request_id, issued_at),
                checkpoint=checkpoint,
            )

        successor = WaiterMaintenance(
            restored, AsyncMock(), asyncio.Queue(maxsize=1), gate=DrainGate(),
            replay_factory=factory,
        )
        await successor.tick()
        await finish_cycle(successor)
        requests = [request for name, request in service.calls if name == "register"]
        assert requests[:2] == [("request:one", 100.0)] * 2
        assert restored.get_active_run_waiter(first)["delegation"]["registration_id"]
        assert clock == [0.0]
        await successor.close()

    asyncio.run(run())


def test_prepare_rechecks_admission_after_capabilities(tmp_path):
    async def run():
        queue, service, gate, _, maintenance, _ = owner(tmp_path)
        task_id = task(queue)
        service.capabilities = AsyncMock(side_effect=lambda: gate.set_draining(True))
        with pytest.raises(ValueError, match="draining"):
            await maintenance.consumer.prepare(
                task_id, delegation=intent(queue), worker_id="worker", host="host",
                resume_worktree="host/wt", reason="waiting",
            )
        assert queue.get_active_run_waiter(task_id) is None
        assert not queue.list_pending_run_waiters()
        await maintenance.close()

    asyncio.run(run())


def test_backoff_and_passive_ticks_never_emit(tmp_path):
    async def run():
        active = [False]
        _, service, gate, clock, maintenance, _ = owner(tmp_path, active=lambda: active[0])
        await maintenance.tick()
        assert not service.calls
        assert maintenance.status["state"] == "unavailable"
        active[0] = True
        service.fault = "capabilities"
        await maintenance.tick()
        await finish_cycle(maintenance)
        assert maintenance._next_due == 120.0
        clock[0] = 119
        await maintenance.tick()
        assert maintenance._replay_task is None
        clock[0] = 120
        gate.set_draining(True)
        await maintenance.tick()
        assert len(service.calls) == 1
        gate.set_draining(False)
        await maintenance.tick()
        await finish_cycle(maintenance)
        assert maintenance._next_due == 180.0
        await maintenance.close()

    asyncio.run(run())


def test_blocked_remote_io_does_not_block_native_ticks_or_release_drain_early(tmp_path):
    class SlowService(Service):
        async def capabilities(self):
            self.started.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.check_closed()
                self.cancelled.set()

    async def run():
        queue, service, gate, _, maintenance, _ = owner(tmp_path, service_type=SlowService)
        service.started, service.cancelled = asyncio.Event(), asyncio.Event()

        def check_closed():
            with pytest.raises(ValueError, match="draining"):
                maintenance.check_current()

        service.check_closed = check_closed
        await maintenance.tick()
        await service.started.wait()
        assert gate.claims == 1
        ticks, recoveries = [], []

        async def sleep(_interval):
            if len(ticks) == 3:
                raise asyncio.CancelledError

        async def tick():
            ticks.append(True)
            await maintenance.tick()

        class NativeQueue:
            def list_active_run_waiters(self, *, local_only):
                assert local_only
                return [{"kind": "delegated"}]

        async def recover():
            recoveries.append(True)

        with pytest.raises(asyncio.CancelledError):
            await maintain_delegated_recovery(
                NativeQueue(), recover, sleep=sleep, on_tick=tick,
            )
        assert len(ticks) == len(recoveries) == 3
        assert maintenance._replay_task is not None
        assert gate.claims == 1
        await maintenance.close()
        assert service.cancelled.is_set()
        assert gate.claims == 0
        assert maintenance._replay_task is None
        assert not queue.list_pending_run_waiters()

    asyncio.run(run())


def test_factory_must_bind_coordinator_queue_and_checkpoint(tmp_path):
    queue, _, _, _, _, factory = owner(tmp_path)
    other = TaskQueue(tmp_path / "other.db")
    for invalid in (
        lambda _queue, checkpoint: factory(other, checkpoint),
        lambda queue, _checkpoint: factory(queue, None),
    ):
        with pytest.raises(ValueError, match="queue and admission"):
            WaiterMaintenance(
                queue, AsyncMock(), asyncio.Queue(maxsize=1),
                gate=DrainGate(), replay_factory=invalid,
            )
    with pytest.raises(ValueError, match="requires"):
        create_app(queue, replay_consumer_factory=factory, enable_mcp=False)


@pytest.mark.parametrize("interval", [0, -1, float("inf"), float("nan")])
def test_replay_cadence_refuses_disabled_or_unbounded_intervals(tmp_path, interval):
    queue, _, _, _, _, factory = owner(tmp_path)
    with pytest.raises(ValueError, match="interval"):
        WaiterMaintenance(
            queue, AsyncMock(), asyncio.Queue(maxsize=1), gate=DrainGate(), interval=interval,
        )
    with pytest.raises(ValueError, match="requires"):
        create_app(
            queue, replay_consumer_factory=factory, wake_interval=interval,
            wake_is_active=lambda: True, enable_mcp=False,
        )


def test_unexpected_failure_is_sanitized_and_backoff_is_capped_and_reset(tmp_path, caplog):
    async def run():
        _, service, _, clock, maintenance, _ = owner(tmp_path)
        service.capabilities = AsyncMock(side_effect=RuntimeError("private token material"))
        for delay in (120, 240, 480, 900, 900):
            await maintenance.tick()
            with pytest.raises(RuntimeError):
                await maintenance._replay_task
            await asyncio.sleep(0)
            assert maintenance._replay_task is None
            assert maintenance.status["last_result"] is None
            assert maintenance._next_due == clock[0] + delay
            assert maintenance.status["state"] == "blocked"
            clock[0] = maintenance._next_due
        service.capabilities = AsyncMock()
        await maintenance.tick()
        await finish_cycle(maintenance)
        assert maintenance.status["cycles"] == 6
        assert maintenance.status["failures"] == 5
        assert maintenance._next_due == clock[0] + 60
        assert "private token material" not in caplog.text
        await maintenance.close()

    asyncio.run(run())


def test_actual_coordinator_lifespan_owns_replay_and_closes_checkpoint(tmp_path, monkeypatch):
    async def run():
        queue, service, _, _, _, factory = owner(tmp_path)
        task_id = task(queue)
        prepare(queue, task_id, intent(queue))
        app = create_app(
            queue, enable_mcp=False, wake_interval=0.01, verification_interval=0,
            replay_consumer_factory=factory, wake_is_active=lambda: True,
        )
        async with app.router.lifespan_context(app):
            maintenance = app.state.waiter_maintenance
            for _ in range(100):
                if maintenance.status["cycles"]:
                    break
                await asyncio.sleep(0.01)
            assert maintenance.status["cycles"] == 1
            assert queue.get_active_run_waiter(task_id)["delegation"]["registration_id"]
            assert len(service.records) == 1
        assert maintenance.status["state"] == "closed"
        assert all(task.done() for task in maintenance._tasks)
        assert maintenance._replay_task is None
        with pytest.raises(ValueError, match="draining"):
            maintenance.check_current()
        assert not queue.list_run_waiter_wakes(task_id)

    monkeypatch.setenv("AGENT_DISPATCH_SELF_RETIRE", "0")
    monkeypatch.setenv("AGENT_DISPATCH_ABANDONED_PASSIVE_REAP", "0")
    asyncio.run(run())

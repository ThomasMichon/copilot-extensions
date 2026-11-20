"""Real Gateway discovery rejects unsupported watches before durable suspension."""

from __future__ import annotations

import asyncio
from importlib import import_module
import json
from pathlib import Path
import time
from types import SimpleNamespace

import pytest

from agent_pull_requests.watch_admission import TERMINAL_ADMISSION
from agent_pull_requests.watch_replay_client import ReplayRequest

pytestmark = pytest.mark.contract("agent_dispatch.delegated_replay.transport")


@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.parametrize("legacy_client", [False, True])
def test_watch_only_terminal_admission_is_checked_before_suspension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scoped: bool, legacy_client: bool,
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "fleet-manager" / "tests"))
    helpers = import_module("tests.test_delegated_replay_transport")
    fixtures = import_module("gateway_service_transport_fixtures")
    transport = import_module("test_gateway_service_handler")

    async def scenario() -> None:
        store, gateway = helpers.service_owner(tmp_path, scoped=scoped)
        store.registration_admission = TERMINAL_ADMISSION
        queue = helpers.TaskQueue(tmp_path / "tasks.db")
        task_id = helpers.task(queue)
        async with fixtures.running(tmp_path) as rig:
            await transport.connect_handler(rig, gateway.handle)
            consumer, _ = helpers.new_consumer(queue, rig, scoped=scoped)
            capabilities = await consumer.client.capabilities()
            assert capabilities.registration_admission == TERMINAL_ADMISSION
            current_capabilities = consumer.client.capabilities

            async def legacy_capabilities() -> SimpleNamespace:
                current = await current_capabilities()
                return SimpleNamespace(**{
                    key: value for key, value in vars(current).items()
                    if key != "registration_admission"
                })

            if legacy_client:
                monkeypatch.setattr(consumer.client, "capabilities", legacy_capabilities)
            spec = helpers.identity(queue)
            issued_at = time.time()
            spec.update(until=["review_changed"], timeout=300.0, replay={
                "binding": consumer.binding, "request_id": "original",
                "issued_at": issued_at, "deadline_at": issued_at + 300,
            })
            with pytest.raises(ValueError, match=(
                "admission capabilities unsupported" if legacy_client else "terminal predicates"
            )):
                await consumer.prepare(
                    task_id, delegation=spec, worker_id="worker",
                    resume_worktree="host/wt", host="host", reason="waiting",
                )
            assert queue.get_active_run_waiter(task_id) is None
            assert not queue.list_pending_run_waiters()
            document = json.loads(store.path.read_bytes())
            assert not (document["replay"] if scoped else document)["records"]
            monkeypatch.setattr(consumer.client, "capabilities", current_capabilities)
            spec["until"] = ["merged"]
            await consumer.prepare(
                task_id, delegation=spec, worker_id="worker",
                resume_worktree="host/wt", host="host", reason="waiting",
            )
            assert len(queue.list_pending_run_waiters()) == 1
            if legacy_client:
                monkeypatch.setattr(consumer.client, "capabilities", legacy_capabilities)
                assert not (await consumer.cycle())["blocked"]
                queue.supersede_run_waiter_with_wake(
                    task_id, reason="cancelled", message="recheck", sender="test",
                )
                for _ in range(2):
                    assert not (await consumer.cycle())["blocked"]
                assert not queue.list_replay_waiters(consumer.binding)
            assert rig.backends[0].requests == []
            await gateway.drain(timeout=3)

    asyncio.run(scenario())


@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.parametrize("attempt", ["unattempted", "unknown", "accepted"])
def test_policy_transition_recovers_prepared_waiter_without_replaying_unknown_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scoped: bool, attempt: str,
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "fleet-manager" / "tests"))
    helpers = import_module("tests.test_delegated_replay_transport")
    fixtures = import_module("gateway_service_transport_fixtures")
    transport = import_module("test_gateway_service_handler")

    async def scenario() -> None:
        store, gateway = helpers.service_owner(tmp_path, scoped=scoped)
        queue = helpers.TaskQueue(tmp_path / "tasks.db")
        task_id = helpers.task(queue)
        async with fixtures.running(tmp_path) as rig:
            await transport.connect_handler(rig, gateway.handle)
            consumer, _ = helpers.new_consumer(queue, rig, scoped=scoped)
            spec = helpers.identity(queue)
            issued_at = time.time()
            spec.update(until=["review_changed"], timeout=300.0, replay={
                "binding": consumer.binding, "request_id": "original-policy-transition",
                "issued_at": issued_at, "deadline_at": issued_at + 300,
            })
            await consumer.prepare(
                task_id, delegation=spec, worker_id="worker",
                resume_worktree="host/wt", host="host", reason="waiting",
            )
            waiter = queue.list_replay_waiters(consumer.binding)[0]
            if attempt != "unattempted":
                queue.replay_waiter_progress(waiter, attempt=True)
            original = ReplayRequest(spec["replay"]["request_id"], issued_at)
            if attempt == "accepted":
                await consumer.client.register(
                    original, number=spec["number"], until=tuple(spec["until"]),
                    deadline_at=spec["replay"]["deadline_at"],
                )
            store.registration_admission = TERMINAL_ADMISSION

            async def no_register(*args: object, **kwargs: object) -> None:
                pytest.fail("changed admission must not transmit a registration")

            monkeypatch.setattr(consumer.client, "register", no_register)
            result = await consumer.cycle()
            assert result["blocked"] is (attempt == "unknown")
            assert queue.get_active_run_waiter(task_id) is None
            assert len(queue.list_run_waiter_wakes(task_id)) == 1
            remaining = queue.list_replay_waiters(consumer.binding)
            if attempt == "unknown":
                assert len(remaining) == 1 and remaining[0]["replay_attempted"]
                assert remaining[0]["delegation"]["replay"] == spec["replay"]
            else:
                assert not remaining
            if attempt == "accepted":
                assert (await consumer.client.inspect(original)).status == "cancelled"
            for _ in range(2):
                await consumer.cycle()
            assert len(queue.list_run_waiter_wakes(task_id)) == 1
            assert rig.backends[0].requests == []
            await gateway.drain(timeout=3)

    asyncio.run(scenario())

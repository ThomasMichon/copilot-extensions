"""Optional Dispatch consumer over real TLS/PoP, connector and both disk owners."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
import threading
import time

import pytest
from agent_procutil import no_window_kwargs
from fleet_manager.gateway_service_caller import ServiceCaller
from fleet_manager.gateway_service_channel import TransportError

from agent_pull_requests.watch_gateway import WatchGateway, WatchScopeBinding
from agent_pull_requests.watch_replay import ConsumerScope
from agent_pull_requests.watch_replay_store import ReplayServiceIdentity, ReplayStore
from agent_dispatch.delegated_replay_consumer import create_replay_consumer
from agent_dispatch.coordinator import create_app
from tests._delegation_helpers import identity, task
from tests._helpers import RepoDefaultingQueue as TaskQueue

pytestmark = pytest.mark.contract("agent_dispatch.delegated_replay.transport")


def service_binding():
    return {
        "fleet": "fleet-a", "gateway": "gateway-a", "slot": "service-a",
        "namespace": "fleet/service/installation", "principal": "caller-a", "target": "project",
        "consumer": "caller-a", "account": "dispatch", "repo": "example/project",
    }


def service_owner(tmp_path, *, scoped=False):
    store = ReplayStore(
        (tmp_path / "replay").resolve(), "fleet/service/installation",
        service_identity=ReplayServiceIdentity("fleet-a", "gateway-a", "service-a") if scoped else None,
    )
    store.provision()
    return store, WatchGateway(
        store, fleet="fleet-a", gateway="gateway-a", slot="service-a",
        bindings=(WatchScopeBinding(
            "caller-a", "project", ConsumerScope("caller-a", "dispatch", "example/project"),
        ),),
    )


def new_consumer(queue, rig, checkpoint=None, *, scoped=False):
    async def access():
        return rig.access("caller-a", 3)
    caller = ServiceCaller(
        gateway_origin=rig.origin, gateway_ca=rig.ca, access_token_callback=access,
        proof_key=rig.h.keys[3].get_op_key("sign"),
    )
    return create_replay_consumer(
        queue, caller, route_prefix="/api", target="project",
        repo="example/project", caller_scope="caller-a:dispatch",
        checkpoint=checkpoint,
        service_binding=service_binding() if scoped else None,
    ), caller


def test_default_import_does_not_load_optional_packages():
    code = """
import importlib.abc
import sys
class RefuseOptional(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, *args):
        if fullname.split('.')[0] in ('agent_pull_requests', 'fleet_manager'):
            raise AssertionError('default import loaded an optional distribution')
sys.meta_path.insert(0, RefuseOptional())
import agent_dispatch
import agent_dispatch.delegated_replay_consumer
from agent_dispatch.config import Config
from agent_dispatch.server import build_app
from pathlib import Path
import tempfile
with tempfile.TemporaryDirectory() as root:
    build_app(Config(db_path=str(Path(root) / 'server.db')))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True,
        timeout=15, check=False, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr


async def prepare_intent(queue, owner, task_id, *, timeout=1.0):
    spec = identity(queue)
    issued_at = time.time() - 2
    spec.update(timeout=timeout, replay={
        "binding": owner.binding, "request_id": "original",
        "issued_at": issued_at, "deadline_at": issued_at + timeout,
    })
    await owner.prepare(
        task_id, delegation=spec, worker_id="worker", resume_worktree="host/wt",
        host="host", reason="waiting",
    )
    return spec


@pytest.mark.parametrize("lost_operation", ["register", "acknowledge", "cancel"])
@pytest.mark.parametrize("scoped", [False, True])
def test_actual_consumer_restarts_after_lost_committed_result(tmp_path, monkeypatch, lost_operation, scoped):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "fleet-manager" / "tests"))
    from gateway_service_transport_fixtures import running
    from test_gateway_service_handler import connect_handler

    async def scenario():
        store, owner = service_owner(tmp_path, scoped=scoped)
        database = tmp_path / "tasks.db"
        queue = TaskQueue(database)
        task_id = task(queue)
        async with running(tmp_path) as rig:
            await connect_handler(rig, owner.handle)
            consumer, caller = new_consumer(queue, rig, scoped=scoped)
            spec = await prepare_intent(
                queue, consumer, task_id, timeout=300.0 if lost_operation == "cancel" else 1.0,
            )
            if lost_operation == "cancel":
                assert not (await consumer.cycle())["blocked"]
                queue.supersede_run_waiter_with_wake(
                    task_id, reason="cancelled", message="recheck", sender="test",
                )
                assert (await consumer.cycle())["checked"] == 0
            original = caller.request
            lost = []

            async def request(method, target, **kwargs):
                response = await original(method, target, **kwargs)
                if target.endswith("/" + lost_operation) and not lost:
                    lost.append(True)
                    raise TransportError("outcome-unknown", 502)
                return response

            monkeypatch.setattr(caller, "request", request)
            assert (await consumer.cycle())["blocked"]
            assert len(queue.list_run_waiter_wakes(task_id)) == int(lost_operation != "register")
            await owner.drain(timeout=3)
            rig.tasks[-1].cancel()
            await asyncio.gather(rig.tasks[-1], return_exceptions=True)
            owner = WatchGateway(
                store, fleet="fleet-a", gateway="gateway-a", slot="service-a",
                bindings=(WatchScopeBinding(
                    "caller-a", "project", ConsumerScope("caller-a", "dispatch", "example/project"),
                ),),
            )
            await connect_handler(rig, owner.handle)
            restored = TaskQueue(database)
            consumer, _ = new_consumer(restored, rig, scoped=scoped)
            result = await consumer.cycle()
            assert not result["blocked"]
            assert len(restored.list_run_waiter_wakes(task_id)) == 1
            assert not restored.list_replay_waiters(consumer.binding)
            state = json.loads(store.path.read_bytes())
            records = (state["replay"] if scoped else state)["records"]
            assert len(records) == 1
            assert (await consumer.client.pending()).events == ()
            assert (spec["replay"]["deadline_at"] < time.time()) == (lost_operation != "cancel")
            assert rig.backends[0].requests == []
            await owner.drain(timeout=3)

    asyncio.run(scenario())


@pytest.mark.parametrize("loss", ["policy", "channel", "cancel"])
def test_actual_consumer_reconciles_original_after_commit_in_flight(tmp_path, monkeypatch, loss):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "fleet-manager" / "tests"))
    from gateway_service_transport_fixtures import eventually, running
    from test_gateway_service_handler import connect_handler
    from agent_pull_requests import watch_replay_store

    async def scenario():
        store, owner = service_owner(tmp_path)
        queue = TaskQueue(tmp_path / "tasks.db")
        task_id = task(queue)
        started, release = threading.Event(), threading.Event()
        original_write = watch_replay_store._atomic_write_json

        def commit(path, state):
            if not started.is_set():
                started.set()
                assert release.wait(10)
            original_write(path, state)

        async with running(tmp_path) as rig:
            await connect_handler(rig, owner.handle)
            consumer, _ = new_consumer(queue, rig)
            await prepare_intent(queue, consumer, task_id, timeout=300.0)
            monkeypatch.setattr(watch_replay_store, "_atomic_write_json", commit)
            pending = asyncio.create_task(consumer.cycle())
            try:
                assert await asyncio.to_thread(started.wait, 3)
                callers = rig.h.document["callers"]
                if loss == "policy":
                    rig.h.document["callers"] = []
                    await rig.update()
                elif loss == "channel":
                    await rig.state.channels["service-a"].close()
                else:
                    pending.cancel()
                if loss == "cancel":
                    with pytest.raises(asyncio.CancelledError):
                        await pending
                else:
                    assert (await pending)["blocked"]
                assert owner._tasks
                assert queue.list_pending_run_waiters()[0]["replay_attempted"]
                assert not queue.list_run_waiter_wakes(task_id)
                release.set()
                await eventually(lambda: not owner._tasks)
                if loss == "policy":
                    rig.h.document["callers"] = callers
                    await rig.update()
                elif loss == "channel":
                    await connect_handler(rig, owner.handle)
                consumer, _ = new_consumer(TaskQueue(tmp_path / "tasks.db"), rig)
                assert not (await consumer.cycle())["blocked"]
                assert len(json.loads(store.path.read_bytes())["records"]) == 1
                assert queue.get_active_run_waiter(task_id)["delegation"]["replay"]["request_id"] == "original"
                assert not queue.list_run_waiter_wakes(task_id)
            finally:
                release.set()
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
                await owner.drain(timeout=3)

    asyncio.run(scenario())


@pytest.mark.parametrize("interrupt", [False, True])
def test_coordinator_scheduler_uses_real_transport_and_recovers_shutdown(
    tmp_path, monkeypatch, interrupt,
):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "fleet-manager" / "tests"))
    monkeypatch.setenv("AGENT_DISPATCH_SELF_RETIRE", "0")
    monkeypatch.setenv("AGENT_DISPATCH_ABANDONED_PASSIVE_REAP", "0")
    from gateway_service_transport_fixtures import eventually, running
    from test_gateway_service_handler import connect_handler
    from agent_pull_requests import watch_replay_store

    async def scenario():
        store, owner = service_owner(tmp_path)
        queue = TaskQueue(tmp_path / "tasks.db")
        task_id = task(queue)
        started, release = threading.Event(), threading.Event()
        original_write = watch_replay_store._atomic_write_json

        def commit(path, state):
            if interrupt and not started.is_set():
                started.set()
                assert release.wait(10)
            original_write(path, state)

        async with running(tmp_path) as rig:
            await connect_handler(rig, owner.handle)
            initial, _ = new_consumer(queue, rig)
            await prepare_intent(queue, initial, task_id)
            monkeypatch.setattr(watch_replay_store, "_atomic_write_json", commit)

            def factory(queue, checkpoint):
                return new_consumer(queue, rig, checkpoint)[0]

            def app_for(queue):
                return create_app(
                    queue, enable_mcp=False, wake_interval=0.01, verification_interval=0,
                    replay_consumer_factory=factory, wake_is_active=lambda: True,
                )

            app = app_for(queue)
            try:
                async with app.router.lifespan_context(app):
                    maintenance = app.state.waiter_maintenance
                    if interrupt:
                        assert await asyncio.to_thread(started.wait, 3)
                        assert app.state.drain_gate.claims == 1
                    else:
                        await eventually(lambda: maintenance.status["cycles"] == 1)
                        assert not queue.list_replay_waiters(initial.binding)
                        assert len(queue.list_run_waiter_wakes(task_id)) == 1
                assert app.state.drain_gate.claims == 0
                assert maintenance.status["state"] == "closed"
                if interrupt:
                    assert owner._tasks  # Cancellation cannot roll back the remote disk commit.
                    assert queue.list_pending_run_waiters()[0]["replay_attempted"]
                    release.set()
                    await eventually(lambda: not owner._tasks)
                    restored = TaskQueue(tmp_path / "tasks.db")
                    successor = app_for(restored)
                    async with successor.router.lifespan_context(successor):
                        await eventually(
                            lambda: successor.state.waiter_maintenance.status["cycles"] == 1,
                        )
                        assert not restored.list_replay_waiters(initial.binding)
                        assert len(restored.list_run_waiter_wakes(task_id)) == 1
                assert len(json.loads(store.path.read_bytes())["records"]) == 1
                assert rig.backends[0].requests == []
            finally:
                release.set()
                await owner.drain(timeout=3)

    asyncio.run(scenario())


@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.timeout(120 if sys.platform == "win32" else 30)
def test_real_watch_service_replays_original_waits_without_unchanged_wakes(tmp_path, monkeypatch, scoped):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "fleet-manager" / "tests"))
    from gateway_service_transport_fixtures import eventually, running
    from agent_pull_requests.watch_observer import NativeObservationBinding
    from agent_pull_requests.watch_replay_client import ReplayClientError
    from agent_pull_requests.watch_service import WatchService

    async def scenario():
        store, _ = service_owner(tmp_path, scoped=scoped)
        database = tmp_path / "tasks.db"
        queue = TaskQueue(database)
        tasks = [task(queue), task(queue)]
        snapshot_path, reads = tmp_path / "snapshot.json", tmp_path / "reads"
        snapshot = {
            "schema": "agent-worktrees.pr-snapshot", "version": 1,
            "repo": "example/project", "pr": 7, "provider": "github",
            "pr_state": "open", "merged": False, "head_sha": "unchanged-head",
            "base_ref": "main", "mergeable": True, "checks_state": "success",
            "title": "Synthetic provider observation", "draft": False,
        }
        snapshot_path.write_text(json.dumps(snapshot))
        native = (
            "import json,pathlib,sys;"
            "assert sys.argv[1:6]==['pr-watch','snapshot','example/project','7','--json'];"
            "assert sys.argv[6]=='--credential-command';"
            f"data=pathlib.Path({str(snapshot_path)!r}).read_text();"
            f"pathlib.Path({str(reads)!r}).open('a').write('read\\n');"
            "print(data)"
        )

        def read_count():
            return len(reads.read_text().splitlines()) if reads.exists() else 0

        async def completed_cycle(consumer):
            for _ in range(100):
                result = await consumer.cycle()
                if not result["blocked"]:
                    return result
                await asyncio.sleep(0.05)
            raise AssertionError("service replay cycle remained blocked")

        async with running(tmp_path) as rig:
            async def service_access():
                return rig.access("service-a", 1)

            def service():
                return WatchService(
                    store, fleet="fleet-a", gateway="gateway-a", slot="service-a",
                    bindings=(WatchScopeBinding(
                        "caller-a", "project",
                        ConsumerScope("caller-a", "dispatch", "example/project"),
                    ),),
                    observations=(NativeObservationBinding(
                        "dispatch", "example/project", sys.executable,
                        prefix=(sys.executable, "-I", "-c", native),
                        credential_command=(sys.executable, "-I", "-m", "fleet_manager"),
                    ),),
                    gateway_origin=rig.origin, gateway_ca=rig.ca,
                    access_token_callback=service_access,
                    proof_key=rig.h.keys[1].get_op_key("sign"),
                    authority=lambda: int(time.time()) + 60, checkpoint=lambda: None,
                    interval=0.1,
                )

            owner = service()
            await owner.start()
            try:
                await eventually(lambda: owner.status()["ready"], timeout=10)
                consumer, caller = new_consumer(queue, rig, scoped=scoped)
                request, last_response = caller.request, []

                async def observed_request(method, target, **kwargs):
                    response = await request(method, target, **kwargs)
                    last_response[:] = [(response.status, response.body)]
                    return response

                monkeypatch.setattr(caller, "request", observed_request)
                originals = {}
                for index, task_id in enumerate(tasks):
                    spec = identity(queue, suffix=str(index))
                    issued_at = time.time()
                    spec.update(until=["merged"], replay={
                        "binding": consumer.binding, "request_id": f"request-{index}",
                        "issued_at": issued_at, "deadline_at": issued_at + spec["timeout"],
                    })
                    prepared = None
                    for _ in range(100):
                        try:
                            prepared = await consumer.prepare(
                                task_id, delegation=spec, worker_id="worker", resume_worktree="host/wt",
                                host="host", reason="waiting",
                            )
                            break
                        except ReplayClientError as error:
                            if error.http_status != 503 or error.outcome_unknown:
                                raise
                            assert queue.get_active_run_waiter(task_id) is None
                            await asyncio.sleep(0.05)
                    assert prepared is not None and prepared["state"] == "preparing", (
                        last_response, owner.status(),
                        [str(unit.exception()) for unit in owner._tasks if unit.done()],
                    )
                    originals[task_id] = spec["replay"]
                for _ in range(60):
                    await consumer.cycle()
                    if all(
                        (queue.get_active_run_waiter(task_id) or {}).get("state") == "active"
                        for task_id in tasks
                    ):
                        break
                    await asyncio.sleep(0.05)
                assert all(queue.get_active_run_waiter(task_id)["state"] == "active" for task_id in tasks)
                before = read_count()
                await eventually(lambda: read_count() >= before + 3, timeout=10)
                for _ in range(3):
                    await completed_cycle(consumer)
                    assert all(not queue.list_run_waiter_wakes(task_id) for task_id in tasks)
                await eventually(
                    lambda: owner._last_cycle is not None and owner._last_cycle["observed"] == 1,
                    timeout=10,
                )  # Two waits share one native target after a completed cycle.
                registration_ids = {
                    task_id: queue.get_active_run_waiter(task_id)["delegation"]["registration_id"]
                    for task_id in tasks
                }
            finally:
                await owner.close(timeout=5)

            assert (await consumer.cycle())["blocked"]
            assert all(not queue.list_run_waiter_wakes(task_id) for task_id in tasks)
            queue = TaskQueue(database)
            successor = service()
            await successor.start()
            try:
                await eventually(lambda: successor.status()["ready"], timeout=10)
                consumer, caller = new_consumer(queue, rig, scoped=scoped)
                await completed_cycle(consumer)
                for task_id in tasks:
                    waiter = queue.get_active_run_waiter(task_id)
                    assert waiter["delegation"]["replay"] == originals[task_id]
                    assert waiter["delegation"]["registration_id"] == registration_ids[task_id]
                    assert not queue.list_run_waiter_wakes(task_id)
                replacement = snapshot_path.with_suffix(".pending")
                replacement.write_text(json.dumps(snapshot | {"pr_state": "closed", "merged": True}))
                replacement.replace(snapshot_path)
                original_request, lost = caller.request, []

                async def lose_ack(method, target, **kwargs):
                    response = await original_request(method, target, **kwargs)
                    if target.endswith("/acknowledge") and response.status == 200 and not lost:
                        lost.append(True)
                        raise TransportError("outcome-unknown", 502)
                    return response

                monkeypatch.setattr(caller, "request", lose_ack)
                for _ in range(100):
                    await consumer.cycle()
                    if lost:
                        break
                    await asyncio.sleep(0.05)
                assert lost
                restored = TaskQueue(database)
                recovered, _ = new_consumer(restored, rig, scoped=scoped)
                for _ in range(100):
                    await recovered.cycle()
                    if not restored.list_replay_waiters(recovered.binding):
                        break
                    await asyncio.sleep(0.05)
                assert not restored.list_replay_waiters(recovered.binding)
                for task_id in tasks:
                    wakes = restored.list_run_waiter_wakes(task_id)
                    assert len(wakes) == 1
                for _ in range(3):
                    assert (await completed_cycle(recovered))["events"] == 0
                assert all(len(restored.list_run_waiter_wakes(task_id)) == 1 for task_id in tasks)
                assert rig.backends[0].requests == []
            finally:
                await successor.close(timeout=5)

    asyncio.run(scenario())


def test_programmatic_server_frontdoor_uses_scoped_factory_and_existing_authority(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "fleet-manager" / "tests"))
    monkeypatch.setenv("AGENT_DISPATCH_SELF_RETIRE", "0")
    monkeypatch.setenv("AGENT_DISPATCH_ABANDONED_PASSIVE_REAP", "0")
    from gateway_service_transport_fixtures import eventually, running
    from test_gateway_service_handler import connect_handler
    from agent_dispatch import server
    from agent_dispatch.config import Config

    active = [False]
    monkeypatch.setattr(server, "_owns_active_route", lambda: active[0])

    async def scenario():
        store, owner = service_owner(tmp_path, scoped=True)
        database = tmp_path / "tasks.db"
        queue = TaskQueue(database)
        task_id = task(queue)
        async with running(tmp_path) as rig:
            await connect_handler(rig, owner.handle)
            initial, _ = new_consumer(queue, rig, scoped=True)
            original = await prepare_intent(queue, initial, task_id)
            constructed = []

            def factory(selected_queue, checkpoint):
                selected = new_consumer(selected_queue, rig, checkpoint, scoped=True)[0]
                constructed.append(selected)
                return selected

            app = server.build_app(Config(db_path=str(database)), replay_consumer_factory=factory)
            try:
                async with app.router.lifespan_context(app):
                    maintenance = app.state.waiter_maintenance
                    await eventually(lambda: maintenance.status["state"] == "unavailable")
                    selected, = constructed
                    assert maintenance.queue is selected.queue
                    assert selected.binding == initial.binding
                    assert not queue.list_run_waiter_wakes(task_id)
                    assert json.loads(store.path.read_bytes())["replay"]["records"] == []
                    with pytest.raises(ValueError, match="not active"):
                        await selected.prepare(
                            task_id, delegation=original, worker_id="worker",
                            resume_worktree="host/wt", host="host", reason="waiting",
                        )
                    active[0] = True
                    await maintenance.tick()
                    await eventually(lambda: maintenance.status["cycles"] == 1)
                    assert not maintenance.status["last_result"]["blocked"]
                    assert not queue.list_replay_waiters(selected.binding)
                    assert len(queue.list_run_waiter_wakes(task_id)) == 1
                    active[0] = False
                    with pytest.raises(ValueError, match="not active"):
                        selected._check_binding()
                assert maintenance.status["state"] == "closed"
                assert app.state.drain_gate.claims == 0
                assert all(unit.done() for unit in maintenance._tasks)
                assert rig.backends[0].requests == []
            finally:
                await owner.drain(timeout=3)
    asyncio.run(scenario())

"""Real disk/lease/thread ownership with a deterministic connector boundary."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from dataclasses import replace

import pytest
from fleet_manager.gateway_service_handler import CallerContext, ServiceRequest
from single_instance_lease import AlreadyRunningError, OperationLock, SingleInstance

from agent_pull_requests import watch_observer, watch_replay_store, watch_service
from agent_pull_requests.watch_contract import PRSnapshot
from agent_pull_requests.watch_gateway import WatchScopeBinding
from agent_pull_requests.watch_observer import NativeObservationBinding
from agent_pull_requests.watch_replay import ConsumerScope
from agent_pull_requests.watch_replay_store import (
    ReplayServiceIdentity, ReplayStore, ReplayStoreInvalid,
)
from agent_pull_requests.watch_service import WatchService

SCOPE = ConsumerScope("consumer-a", "account-a", "example/project")


class Connector:
    def __init__(self, **kwargs):
        self.connected = asyncio.Event()
        self.handler = kwargs["handler"]
        self.entered = asyncio.Event()
        self.exited = asyncio.Event()

    async def run(self, stop):
        self.entered.set()
        self.connected.set()
        try:
            await stop.wait()
        finally:
            self.connected.clear()
            self.exited.set()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(watch_service, "ServiceConnector", Connector)
    store = ReplayStore(tmp_path.resolve(), "fleet/service/installation")
    store.provision()
    revoked = threading.Event()

    def checkpoint():
        if revoked.is_set():
            raise PermissionError("synthetic authority lost")

    async def access():
        return "synthetic"

    def make(**kwargs):
        return WatchService(
            kwargs.pop("store", store), fleet="fleet-a", gateway="gateway-a", slot="service-a",
            bindings=(WatchScopeBinding("caller-a", "project", SCOPE),),
            observations=(NativeObservationBinding(SCOPE.account, SCOPE.repo, sys.executable),),
            gateway_origin="https://gateway.invalid", access_token_callback=access,
            proof_key=object(), authority=lambda: int(time.time()) + 60,
            checkpoint=checkpoint, interval=0.01, **kwargs,
        )

    return store, make, revoked


def request(operation="pending", payload=None):
    return ServiceRequest(
        CallerContext("fleet-a", "gateway-a", "service-a", "caller-a", int(time.time()) + 60),
        "POST", f"/v1/watch/project/{operation}", (("content-type", "application/json"),),
        json.dumps(payload or {}).encode(),
    )


def registration():
    return dict(request_id="original", issued_at=time.time(), number=1, until=["merged"])


async def wait_for(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(0.001)
    await asyncio.wait_for(wait(), 3)


def contender(store):
    return SingleInstance(store.root, service="agent-pull-requests-watch")


@pytest.mark.parametrize("field", ["fleet", "gateway", "slot"])
def test_service_construction_refuses_a_different_bound_identity(setup, field):
    store, make, _ = setup
    store.service_identity = replace(
        ReplayServiceIdentity("fleet-a", "gateway-a", "service-a"), **{field: "other"},
    )
    before = store.path.read_bytes()
    with pytest.raises(ValueError, match="storage binding"):
        make()
    assert store.path.read_bytes() == before


def test_bound_service_refuses_legacy_store_before_starting_owned_units(setup):
    store, make, _ = setup
    store.service_identity = ReplayServiceIdentity("fleet-a", "gateway-a", "service-a")
    before = store.path.read_bytes()

    async def scenario():
        service = make()
        with pytest.raises(ReplayStoreInvalid):
            await service.start()
        assert not service.connector.entered.is_set()
        assert not service.status()["writer_held"]

    asyncio.run(scenario())
    assert store.path.read_bytes() == before


def test_adopts_before_readiness_and_releases_after_both_units_exit(setup):
    store, make, _ = setup

    async def scenario():
        service = make()
        assert not service.status()["ready"]
        await service.start()
        try:
            await wait_for(lambda: service.status()["ready"])
            with pytest.raises(AlreadyRunningError):
                contender(store).acquire()
            service.connector.connected.clear()
            assert not service.status()["ready"]
        finally:
            await service.close(timeout=3)
        assert service.connector.exited.is_set()
        assert service.status() == {
            "ready": False, "writer_held": False, "draining": True, "closed": True,
        }
        with contender(store):
            pass
        with pytest.raises(RuntimeError):
            await service.start()

    asyncio.run(scenario())


@pytest.mark.parametrize("ending", ["stop", "cancel", "failure", "return"])
def test_foreground_lifetime_joins_owned_units_on_every_exit(setup, ending):
    _, make, _ = setup

    async def scenario():
        service = make()
        stop = asyncio.Event()
        if ending in {"failure", "return"}:
            async def stopped(_stop):
                if ending == "failure":
                    raise ValueError("synthetic connector failure")
            service.connector.run = stopped
        running = asyncio.create_task(service.run(stop, drain_timeout=3))
        if ending in {"stop", "cancel"}:
            await wait_for(lambda: service.status()["ready"])
            if ending == "stop":
                stop.set()
                await running
            else:
                running.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await running
        else:
            with pytest.raises(ValueError if ending == "failure" else RuntimeError):
                await running
        assert service.status()["closed"] and not service.status()["writer_held"]
        assert all(task.done() for task in service._tasks)

    asyncio.run(scenario())


@pytest.mark.parametrize("state", ["missing", "corrupt", "foreign", "native"])
def test_start_refuses_without_reprovisioning_or_stale_lease(setup, state):
    store, make, _ = setup
    if state == "missing":
        store.path.unlink()
    elif state == "corrupt":
        store.path.write_text("corrupt")
    elif state == "foreign":
        store.namespace = "other/service/installation"
    else:
        (store.root / "watch-subscriptions.json").write_text("{}")
    before = store.path.read_bytes() if store.path.exists() else None

    async def scenario():
        service = make()
        with pytest.raises((OSError, ValueError)):
            await service.start()
        assert not service.connector.entered.is_set()
        with contender(store):
            pass
        assert service.status()["closed"]

    asyncio.run(scenario())
    assert (store.path.read_bytes() if store.path.exists() else None) == before


def test_competing_start_cannot_release_original_writer(setup):
    store, make, _ = setup

    async def scenario():
        original, replacement = make(), make()
        await original.start()
        try:
            with pytest.raises(AlreadyRunningError):
                await replacement.start()
            await replacement.close(timeout=1)
            with pytest.raises(AlreadyRunningError):
                contender(store).acquire()
        finally:
            await original.close(timeout=3)

    asyncio.run(scenario())


@pytest.mark.parametrize("foreign", ["process", "event-loop"])
def test_foreign_execution_owner_cannot_release_writer(setup, foreign):
    store, make, _ = setup

    async def scenario():
        service = make()
        await service.start()
        try:
            if foreign == "process":
                original = service._pid
                service._pid += 1
                try:
                    with pytest.raises(RuntimeError, match="execution owner"):
                        await service.close(timeout=1)
                finally:
                    service._pid = original
            else:
                def other_loop():
                    with pytest.raises(RuntimeError, match="execution owner"):
                        asyncio.run(service.close(timeout=1))
                await asyncio.to_thread(other_loop)
            with pytest.raises(AlreadyRunningError):
                contender(store).acquire()
            assert not service.status()["draining"]
        finally:
            await service.close(timeout=3)

    asyncio.run(scenario())


def test_constructor_refuses_unobservable_serving_scope(setup):
    store, make, _ = setup
    configured = make()
    with pytest.raises(ValueError, match="no native observation binding"):
        WatchService(
            store, fleet="fleet-a", gateway="gateway-a", slot="service-a",
            bindings=(WatchScopeBinding("caller-a", "project", SCOPE),),
            observations=(), gateway_origin="https://gateway.invalid",
            access_token_callback=lambda: None, proof_key=object(),
            authority=configured._authority, checkpoint=configured._checkpoint,
        )
    with contender(store):
        pass


def test_stop_keeps_writer_until_all_parallel_reads_settle(setup, monkeypatch):
    store, make, _ = setup
    for number in range(1, 7):
        store.execute(SCOPE, "register", {
            **registration(), "request_id": f"request-{number}",
            "number": number, "until": ("merged",),
        }, expires_at=int(time.time()) + 60)
    release = [threading.Event() for _ in range(4)]
    entered = threading.Event()
    calls = []
    lock = threading.Lock()

    def fetch(repo, number, *args, **kwargs):
        with lock:
            calls.append(number)
            if len(calls) == 4:
                entered.set()
        assert release[number - 1].wait(8)
        return PRSnapshot(merged=True)

    monkeypatch.setattr(watch_observer, "fetch_snapshot", fetch)

    async def scenario():
        service = make()
        await service.start()
        try:
            await wait_for(entered.is_set)
            assert not service.status()["ready"]
            with pytest.raises(TimeoutError):
                await service.close(timeout=0.05)
            for event in release[:3]:
                event.set()
            await asyncio.sleep(0.1)
            assert service.status()["writer_held"] and not service._disk.done()
            with pytest.raises(AlreadyRunningError):
                contender(store).acquire()
        finally:
            for event in release:
                event.set()
            await service.close(timeout=3)
        assert sorted(calls) == [1, 2, 3, 4]
        assert store.execute(SCOPE, "pending", {}, expires_at=int(time.time()) + 60)["events"] == []
        with contender(store):
            pass

    asyncio.run(scenario())


def test_observation_shares_scope_and_restarts_from_original_event(setup, monkeypatch):
    store, make, _ = setup
    original = registration()
    peer = replace(SCOPE, consumer="consumer-b")
    for scope in (SCOPE, peer):
        store.execute(scope, "register", {**original, "until": ("merged",)},
                      expires_at=int(time.time()) + 60)
    calls = []
    monkeypatch.setattr(watch_observer, "fetch_snapshot",
                        lambda *a, **kw: calls.append(a) or PRSnapshot(merged=True))

    async def scenario():
        first = make()
        await first.start()
        await wait_for(lambda: first.status()["ready"])
        await first.close(timeout=3)
        events = store.execute(SCOPE, "pending", {}, expires_at=int(time.time()) + 60)
        assert len(events["events"]) == 1
        second = make()
        await second.start()
        await wait_for(lambda: second.status()["ready"])
        await second.close(timeout=3)
        assert store.execute(SCOPE, "pending", {}, expires_at=int(time.time()) + 60) == events
        assert len(calls) == 1

    asyncio.run(scenario())


def test_provider_unavailable_is_not_ready_or_registration_loss(setup, monkeypatch):
    store, make, _ = setup
    original = {**registration(), "until": ("merged",)}
    receipt = store.execute(SCOPE, "register", original, expires_at=int(time.time()) + 60)
    read = threading.Event()

    def unavailable(*args, **kwargs):
        read.set()
        raise ValueError("synthetic provider unavailable")

    monkeypatch.setattr(watch_observer, "fetch_snapshot", unavailable)

    async def scenario():
        service = make()
        await service.start()
        try:
            assert await asyncio.to_thread(read.wait, 3)
            await wait_for(lambda: service._last_cycle is not None)
            assert service.connector.connected.is_set() and not service.status()["ready"]
            assert store.execute(SCOPE, "register", original,
                                 expires_at=int(time.time()) + 60) == receipt
        finally:
            await service.close(timeout=3)

    asyncio.run(scenario())


def test_normal_transaction_contention_recovers_on_existing_cadence(setup):
    store, make, _ = setup

    async def scenario():
        service = make()
        await service.start()
        try:
            await wait_for(lambda: service.status()["ready"])
            with OperationLock(store.root / "watch-replay.lock"):
                await wait_for(lambda: service._last_cycle is None)
                assert not service.status()["ready"]
                assert all(not task.done() for task in service._tasks)
            await wait_for(lambda: service.status()["ready"])
        finally:
            await service.close(timeout=3)

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel_drain", [False, True])
def test_drain_retains_writer_until_native_read_really_finishes(setup, monkeypatch, cancel_drain):
    store, make, _ = setup
    original = {**registration(), "until": ("merged",)}
    receipt = store.execute(SCOPE, "register", original, expires_at=int(time.time()) + 60)
    entered, release = threading.Event(), threading.Event()

    def fetch(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return PRSnapshot(merged=True)

    monkeypatch.setattr(watch_observer, "fetch_snapshot", fetch)

    async def scenario():
        service = make()
        await service.start()
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            if cancel_drain:
                drain = asyncio.create_task(service.close(timeout=3))
                await wait_for(lambda: service.status()["draining"])
                drain.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await drain
            else:
                with pytest.raises(TimeoutError):
                    await service.close(timeout=0.01)
            assert not service.status()["ready"]
            with pytest.raises(AlreadyRunningError):
                contender(store).acquire()
            response = await service.gateway.handle(request("register", registration()))
            assert response.status == 503
        finally:
            release.set()
            await service.close(timeout=3)
        with contender(store):
            pass
        assert store.execute(SCOPE, "register", original,
                             expires_at=int(time.time()) + 60) == receipt
        assert store.execute(SCOPE, "pending", {}, expires_at=int(time.time()) + 60)["events"] == []

    asyncio.run(scenario())


def test_start_cancellation_keeps_custody_of_initial_disk_read(setup, monkeypatch):
    store, make, _ = setup
    entered, release = threading.Event(), threading.Event()
    targets = store.observation_targets

    def held(**kwargs):
        entered.set()
        assert release.wait(5)
        return targets(**kwargs)

    monkeypatch.setattr(store, "observation_targets", held)

    async def scenario():
        service = make()
        start = asyncio.create_task(service.start())
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            start.cancel()
            with pytest.raises(asyncio.CancelledError):
                await start
            with pytest.raises(AlreadyRunningError):
                contender(store).acquire()
            assert not service.connector.entered.is_set()
        finally:
            release.set()
            await service.close(timeout=3)
        with contender(store):
            pass

    asyncio.run(scenario())


def test_close_withdraws_admission_before_waiting_for_mutex(setup):
    store, make, _ = setup

    async def scenario():
        service = make()
        await service.start()
        await service._closing.acquire()
        try:
            with pytest.raises(TimeoutError):
                await service.close(timeout=0.01)
            response = await service.gateway.handle(request("register", registration()))
            assert response.status == 503
            assert service.status()["draining"]
            assert service._closing.locked()
            with pytest.raises(AlreadyRunningError):
                contender(store).acquire()
        finally:
            service._closing.release()
            await service.close(timeout=3)

    asyncio.run(scenario())


@pytest.mark.parametrize("ending", ["timeout", "cancel"])
def test_queued_close_keeps_its_own_budget_and_cannot_release_active_drain(
    setup, monkeypatch, ending,
):
    store, make, _ = setup
    store.execute(SCOPE, "register", {**registration(), "until": ("merged",)},
                  expires_at=int(time.time()) + 60)
    entered, release = threading.Event(), threading.Event()

    def held(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return PRSnapshot(merged=True)

    monkeypatch.setattr(watch_observer, "fetch_snapshot", held)

    async def scenario():
        service = make()
        await service.start()
        original = queued = None
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            original = asyncio.create_task(service.close(timeout=3))
            await wait_for(lambda: service.status()["draining"])
            queued = asyncio.create_task(service.close(timeout=0.01 if ending == "timeout" else 3))
            if ending == "cancel":
                await wait_for(lambda: bool(service._closing._waiters))
                queued.cancel()
            with pytest.raises(TimeoutError if ending == "timeout" else asyncio.CancelledError):
                await queued
            assert not original.done()
            with pytest.raises(AlreadyRunningError):
                contender(store).acquire()
        finally:
            release.set()
            if original is not None:
                await original
            if queued is not None:
                await asyncio.gather(queued, return_exceptions=True)
            await service.close(timeout=3)
        assert not service._closing.locked()
        with contender(store):
            pass

    asyncio.run(scenario())


@pytest.mark.parametrize("ending", ["cancel", "stop"])
@pytest.mark.parametrize("timeout", [False, True])
def test_foreground_startup_withdrawal_drains_its_retained_adoption(
    setup, monkeypatch, timeout, ending,
):
    store, make, _ = setup
    entered, release = threading.Event(), threading.Event()
    targets = store.observation_targets

    def held(**kwargs):
        entered.set()
        assert release.wait(5)
        return targets(**kwargs)

    monkeypatch.setattr(store, "observation_targets", held)

    async def scenario():
        service = make()
        stop = asyncio.Event()
        running = asyncio.create_task(service.run(
            stop, drain_timeout=0.02 if timeout else 3,
        ))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            began = asyncio.get_running_loop().time()
            if ending == "cancel":
                running.cancel()
            else:
                stop.set()
            await wait_for(lambda: service.status()["draining"])
            with pytest.raises(AlreadyRunningError):
                contender(store).acquire()
            if timeout:
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(running), 1)
                assert running.done()
                assert asyncio.get_running_loop().time() - began < 1
                with pytest.raises(AlreadyRunningError):
                    contender(store).acquire()
            else:
                assert not running.done()
                release.set()
                if ending == "cancel":
                    with pytest.raises(asyncio.CancelledError):
                        await running
                else:
                    await running
                assert service._disk.done() and service.status()["closed"]
                with contender(store):
                    pass
        finally:
            release.set()
            await asyncio.gather(running, return_exceptions=True)
            await service.close(timeout=3)
        assert not service.connector.entered.is_set()

    asyncio.run(scenario())


def test_foreground_with_preset_stop_does_not_acquire_or_start(setup):
    store, make, _ = setup

    async def scenario():
        service = make()
        stop = asyncio.Event()
        stop.set()
        await service.run(stop, drain_timeout=0.01)
        assert not service.connector.entered.is_set()
        assert service._disk is None and not service._tasks
        with contender(store):
            pass

    asyncio.run(scenario())


def test_stop_racing_adoption_completion_does_not_launch_units(setup, monkeypatch):
    store, make, _ = setup
    entered, release = threading.Event(), threading.Event()
    targets = store.observation_targets

    def held(**kwargs):
        entered.set()
        assert release.wait(5)
        return targets(**kwargs)

    monkeypatch.setattr(store, "observation_targets", held)

    async def scenario():
        service = make()
        stop = asyncio.Event()
        running = asyncio.create_task(service.run(stop, drain_timeout=3))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            stop.set()
            release.set()
            await running
            assert not service.connector.entered.is_set()
            assert not service._tasks and service.status()["closed"]
            with contender(store):
                pass
        finally:
            release.set()
            await asyncio.gather(running, return_exceptions=True)
            await service.close(timeout=3)

    asyncio.run(scenario())


@pytest.mark.parametrize("second", ["start", "run"])
def test_foreground_reserves_owner_before_scheduling_adoption(setup, monkeypatch, second):
    store, make, _ = setup
    entered, release = threading.Event(), threading.Event()
    targets = store.observation_targets

    def held(**kwargs):
        entered.set()
        assert release.wait(5)
        return targets(**kwargs)

    monkeypatch.setattr(store, "observation_targets", held)

    async def scenario():
        service = make()
        stop = asyncio.Event()
        running = asyncio.create_task(service.run(stop, drain_timeout=3))
        competing = asyncio.create_task(
            service.start() if second == "start" else service.run(asyncio.Event()),
        )
        try:
            with pytest.raises(RuntimeError, match="already used"):
                await competing
            assert await asyncio.to_thread(entered.wait, 3)
            assert not service.status()["draining"]
            with pytest.raises(AlreadyRunningError):
                contender(store).acquire()
        finally:
            stop.set()
            release.set()
            await running
        assert service.status()["closed"] and not service.connector.entered.is_set()

    asyncio.run(scenario())


def test_cancellation_racing_successful_close_lock_acquisition_releases_only_mutex(setup):
    store, make, _ = setup

    async def scenario():
        service = make()
        await service.start()
        original_lock = service._closing
        caller = asyncio.current_task()

        class CancellingLock(asyncio.Lock):
            async def acquire(self):
                acquired = await super().acquire()
                asyncio.get_running_loop().call_soon(caller.cancel)
                return acquired

        lock = service._closing = CancellingLock()
        try:
            with pytest.raises(asyncio.CancelledError):
                await service.close(timeout=3)
            assert not lock.locked() and service.status()["draining"]
            with pytest.raises(AlreadyRunningError):
                contender(store).acquire()
        finally:
            service._closing = original_lock
            await service.close(timeout=3)

    asyncio.run(scenario())


@pytest.mark.parametrize("ending", ["cancel", "timeout"])
@pytest.mark.parametrize("repeated", [False, True])
def test_close_joins_exact_pending_acquisition_despite_repeated_cancellation(setup, ending, repeated):
    store, make, _ = setup

    async def scenario():
        service = make()
        await service.start()
        original_lock = service._closing
        entered, cancelling, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

        class DeferredAcquisition(asyncio.Lock):
            async def acquire(self):
                entered.set()
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    cancelling.set()
                    await release.wait()
                return await super().acquire()

        lock = service._closing = DeferredAcquisition()
        close = asyncio.create_task(service.close(timeout=0.01 if ending == "timeout" else 3))
        try:
            await asyncio.wait_for(entered.wait(), 3)
            if ending == "cancel":
                close.cancel()
            await asyncio.wait_for(cancelling.wait(), 3)
            if repeated:
                for _ in range(3):
                    close.cancel()
                    await asyncio.sleep(0)
                    assert not close.done()
            assert service.status()["writer_held"]
            with pytest.raises(AlreadyRunningError):
                contender(store).acquire()
            release.set()
            with pytest.raises(
                asyncio.CancelledError if ending == "cancel" or repeated else TimeoutError,
            ):
                await close
            assert not lock.locked()
            assert service.status()["writer_held"]
            with pytest.raises(AlreadyRunningError):
                contender(store).acquire()
        finally:
            release.set()
            await asyncio.gather(close, return_exceptions=True)
            service._closing = original_lock
            await service.close(timeout=3)
        assert service.status()["closed"] and not service.status()["writer_held"]

    asyncio.run(scenario())


@pytest.mark.parametrize("adopting", [False, True])
def test_second_foreground_invocation_cannot_drain_existing_owner(setup, monkeypatch, adopting):
    store, make, _ = setup
    entered, release = threading.Event(), threading.Event()
    targets = store.observation_targets

    def held(**kwargs):
        entered.set()
        assert release.wait(5)
        return targets(**kwargs)

    if adopting:
        monkeypatch.setattr(store, "observation_targets", held)

    async def scenario():
        service = make()
        stop = asyncio.Event()
        running = asyncio.create_task(service.run(stop, drain_timeout=3))
        try:
            if adopting:
                assert await asyncio.to_thread(entered.wait, 3)
            else:
                await wait_for(lambda: service.status()["ready"])
            with pytest.raises(RuntimeError, match="already used"):
                await service.run(asyncio.Event(), drain_timeout=3)
            assert not service.status()["draining"]
            with pytest.raises(AlreadyRunningError):
                contender(store).acquire()
            release.set()
            await wait_for(lambda: service.status()["ready"])
        finally:
            release.set()
            stop.set()
            await running
        assert service.status()["closed"]

    asyncio.run(scenario())


def test_drain_timeout_on_serving_write_does_not_release_writer(setup, monkeypatch):
    store, make, _ = setup
    entered, release = threading.Event(), threading.Event()
    atomic = watch_replay_store._atomic_write_json

    def held(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        atomic(*args, **kwargs)

    async def scenario():
        service = make()
        # Exercise the separate serving write between observation cycles.
        service.interval = 60
        await service.start()
        await wait_for(lambda: service.status()["ready"])
        monkeypatch.setattr(watch_replay_store, "_atomic_write_json", held)
        original = registration()
        pending = asyncio.create_task(service.gateway.handle(request("register", original)))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            with pytest.raises(TimeoutError):
                await service.close(timeout=0.01)
            with pytest.raises(AlreadyRunningError):
                contender(store).acquire()
        finally:
            release.set()
            await service.close(timeout=3)
            response = await pending
        assert response.status == 503
        monkeypatch.setattr(watch_replay_store, "_atomic_write_json", atomic)
        original["until"] = tuple(original["until"])
        assert store.execute(SCOPE, "register", original,
                             expires_at=int(time.time()) + 60)["status"] == "active"
        assert len(json.loads(store.path.read_bytes())["records"]) == 1

    asyncio.run(scenario())


def test_close_during_adoption_cannot_start_late_units(setup, monkeypatch):
    store, make, _ = setup
    entered, release = threading.Event(), threading.Event()
    targets = store.observation_targets

    def held(**kwargs):
        entered.set()
        assert release.wait(5)
        return targets(**kwargs)

    monkeypatch.setattr(store, "observation_targets", held)

    async def scenario():
        service = make()
        start = asyncio.create_task(service.start())
        close = None
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            close = asyncio.create_task(service.close(timeout=3))
            await wait_for(lambda: service.status()["draining"])
        finally:
            release.set()
            await asyncio.gather(start, return_exceptions=True)
            if close is not None:
                await close
        assert not service.connector.entered.is_set()
        with contender(store):
            pass

    asyncio.run(scenario())


def test_authority_loss_after_commit_is_uncertain_and_preserves_original_id(setup, monkeypatch):
    store, make, revoked = setup
    atomic = watch_replay_store._atomic_write_json
    committed = threading.Event()

    def withdraw_after_commit(*args, **kwargs):
        atomic(*args, **kwargs)
        revoked.set()
        committed.set()

    async def scenario():
        service = make()
        await service.start()
        await wait_for(lambda: service.status()["ready"])
        original = registration()
        monkeypatch.setattr(watch_replay_store, "_atomic_write_json", withdraw_after_commit)
        try:
            response = await service.gateway.handle(request("register", original))
            assert committed.is_set()
            assert response.status == 503 and json.loads(response.body)["status"] == "uncertain"
            assert not service.status()["ready"]
            response = await service.gateway.handle(request("register", {
                **original, "request_id": "replacement",
            }))
            assert response.status == 503
        finally:
            await service.close(timeout=3)
        monkeypatch.setattr(watch_replay_store, "_atomic_write_json", atomic)
        revoked.clear()
        original["until"] = tuple(original["until"])
        receipt = store.execute(SCOPE, "register", original, expires_at=int(time.time()) + 60)
        assert receipt["status"] == "active"
        assert len(json.loads(store.path.read_bytes())["records"]) == 1

    asyncio.run(scenario())

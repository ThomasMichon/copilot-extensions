"""Admission must outlive a real WatchService bounded-drain failure."""

import asyncio
from dataclasses import replace
import threading
from types import SimpleNamespace

import pytest
from agent_procutil import daemon_instances as instances
from single_instance_lease import AlreadyRunningError, SingleInstance

from agent_pull_requests import watch_service
from agent_pull_requests.watch_replay_store import ReplayStore, ReplayStoreInvalid
from agent_pull_requests_service import hosting
from agent_pull_requests_service.config import load_config
from test_host import configuration as configuration


class Connector:
    def __init__(self, **_):
        self.connected = asyncio.Event()
        self.entered = asyncio.Event()
        self.exited = asyncio.Event()

    async def run(self, stop):
        self.connected.set()
        self.entered.set()
        try:
            await stop.wait()
        finally:
            self.connected.clear()
            self.exited.set()


def actual_service(configuration, monkeypatch):
    path, _ = configuration
    config = replace(load_config(path), drain_timeout=0.03)
    config.store().provision()
    real_service = hosting.WatchService
    services, guards = [], []
    real_admit = instances.admit_daemon_instance

    def make(*args, **kwargs):
        service = real_service(*args, **kwargs)
        services.append(service)
        return service

    def admit(*args, **kwargs):
        guard = real_admit(*args, **kwargs)
        guards.append(guard)
        return guard

    monkeypatch.setattr(hosting, "WatchService", make)
    monkeypatch.setattr(watch_service, "ServiceConnector", Connector)
    monkeypatch.setattr(instances, "admit_daemon_instance", admit)
    monkeypatch.setattr(
        hosting, "load_service_credentials",
        lambda **_: SimpleNamespace(access_token=None, proof_key=object()),
    )
    return config, services, guards


def record(guard):
    return next(row for row in instances.inspect_instances(guard.receipt.registry_root)
                if row["instance_id"] == guard.receipt.instance_id)


async def until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(0.001)
    await asyncio.wait_for(wait(), 5)


@pytest.mark.parametrize("ending", ["timeout", "cancel", "repeated-cancel"])
def test_actual_timed_out_disk_and_writer_keep_admission_until_settled(
    configuration, monkeypatch, ending,
):
    config, services, guards = actual_service(configuration, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    original_read = ReplayStore.observation_targets
    original_close = watch_service.WatchService.close
    original_run = watch_service.WatchService.run
    timeouts, owner_finished = [], threading.Event()

    def blocked(store, *args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original_read(store, *args, **kwargs)

    async def close(service, **kwargs):
        try:
            return await original_close(service, **kwargs)
        except TimeoutError:
            timeouts.append(service)
            raise

    async def run(service, *args, **kwargs):
        try:
            await original_run(service, *args, **kwargs)
        finally:
            owner_finished.set()

    monkeypatch.setattr(ReplayStore, "observation_targets", blocked)
    monkeypatch.setattr(watch_service.WatchService, "close", close)
    monkeypatch.setattr(watch_service.WatchService, "run", run)

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(hosting.run(config, stop=stop))
        try:
            await until(entered.is_set)
            if ending == "timeout":
                stop.set()
            else:
                task.cancel()
            await until(lambda: bool(timeouts) and owner_finished.is_set())
            service, guard = services[0], guards[0]
            assert not task.done()
            assert service._lease.held and not service._disk.done()
            assert record(guard)["state"] == "running" and not guard._closed
            assert record(guard)["purpose"] == "unattended-test"
            from agent_procutil import _daemon_instance_store

            assert guard.receipt.instance_id in _daemon_instance_store._guards
            contender = SingleInstance(config.root, service="agent-pull-requests-watch")
            with pytest.raises(AlreadyRunningError):
                contender.acquire()
            if ending == "repeated-cancel":
                for _ in range(3):
                    task.cancel()
                    await asyncio.sleep(0.01)
                    assert not task.done() and record(guard)["state"] == "running"
            release.set()
            with pytest.raises(TimeoutError if ending == "timeout" else asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(task), 5)
            assert service.status()["closed"] and not service._lease.held
            assert service._disk.done() and all(unit.done() for unit in service._tasks)
            assert guard._closed and record(guard)["state"] == "finished"
            assert guard.receipt.instance_id not in _daemon_instance_store._guards
            contender.acquire()
            contender.release()
        finally:
            release.set()
            stop.set()
            await asyncio.gather(task, return_exceptions=True)
            if services and services[0].status()["writer_held"]:
                await original_close(services[0], timeout=3)

    asyncio.run(scenario())


def test_actual_normal_close_finishes_admission_after_all_units(configuration, monkeypatch):
    config, services, guards = actual_service(configuration, monkeypatch)

    async def scenario():
        stop = asyncio.Event()
        task = asyncio.create_task(hosting.run(config, stop=stop))
        try:
            await until(lambda: services and services[0].connector.entered.is_set())
            assert record(guards[0])["state"] == "running"
            stop.set()
            await asyncio.wait_for(task, 5)
            service = services[0]
            assert service.status()["closed"] and not service._lease.held
            assert all(unit.done() for unit in service._tasks)
            assert service._disk is None or service._disk.done()
            assert record(guards[0])["state"] == "finished"
        finally:
            stop.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_actual_startup_failure_releases_only_after_initial_work_settles(configuration, monkeypatch):
    config, services, guards = actual_service(configuration, monkeypatch)
    make = hosting.WatchService

    def invalid_after_construction(*args, **kwargs):
        service = make(*args, **kwargs)
        config.store().path.write_text("{}", encoding="utf-8")
        return service

    monkeypatch.setattr(hosting, "WatchService", invalid_after_construction)

    async def scenario():
        with pytest.raises(ReplayStoreInvalid):
            await hosting.run(config)
        service = services[0]
        assert not service._lease.held
        assert service._disk is None or service._disk.done()
        assert record(guards[0])["state"] == "finished"

    asyncio.run(scenario())

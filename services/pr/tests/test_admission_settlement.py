"""Admission must outlive a real WatchService bounded-drain failure."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import threading
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Callable, cast

import pytest
from agent_procutil import daemon_instances as instances
from single_instance_lease import AlreadyRunningError, SingleInstance

from agent_pull_requests import watch_service
from agent_pull_requests.watch_service import WatchService
from agent_pull_requests.watch_replay_store import ReplayStore, ReplayStoreInvalid
from agent_pull_requests_service import hosting
from agent_pull_requests_service.config import HostConfig, load_config
if TYPE_CHECKING:
    from services.pr.tests.test_host import Configuration, configuration as configuration
else:
    from test_host import configuration as configuration


class Connector:
    def __init__(self, **_: Any) -> None:
        self.connected = asyncio.Event()
        self.entered = asyncio.Event()
        self.exited = asyncio.Event()

    async def run(self, stop: asyncio.Event) -> None:
        self.connected.set()
        self.entered.set()
        try:
            await stop.wait()
        finally:
            self.connected.clear()
            self.exited.set()


def actual_service(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch,
) -> tuple[HostConfig, list[WatchService], list[instances.DaemonInstanceGuard]]:
    path, _ = configuration
    config = replace(load_config(path), drain_timeout=cast(int, 0.03))
    config.store().provision()
    real_service = getattr(hosting, "WatchService")
    services: list[WatchService]
    guards: list[instances.DaemonInstanceGuard]
    services, guards = [], []
    real_admit = instances.admit_daemon_instance

    def make(*args: Any, **kwargs: Any) -> WatchService:
        service = cast(WatchService, real_service(*args, **kwargs))
        services.append(service)
        return service

    def admit(*args: Any, **kwargs: Any) -> instances.DaemonInstanceGuard:
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


def record(guard: instances.DaemonInstanceGuard) -> dict[str, Any]:
    return next(row for row in instances.inspect_instances(guard.receipt.registry_root)
                if row["instance_id"] == guard.receipt.instance_id)


async def until(predicate: Callable[[], object]) -> None:
    async def wait() -> None:
        while not predicate():
            await asyncio.sleep(0.001)
    await asyncio.wait_for(wait(), 5)


@pytest.mark.parametrize("ending", ["timeout", "cancel", "repeated-cancel"])
def test_actual_timed_out_disk_and_writer_keep_admission_until_settled(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch, ending: str,
) -> None:
    config, services, guards = actual_service(configuration, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    original_read = ReplayStore.observation_targets
    original_close = watch_service.WatchService.close
    original_run = watch_service.WatchService.run
    timeouts: list[WatchService]
    timeouts, owner_finished = [], threading.Event()

    def blocked(store: ReplayStore, *args: Any, **kwargs: Any) -> Any:
        entered.set()
        assert release.wait(10)
        return original_read(store, *args, **kwargs)

    async def close(service: WatchService, **kwargs: Any) -> None:
        try:
            return await original_close(service, **kwargs)
        except TimeoutError:
            timeouts.append(service)
            raise

    async def run(service: WatchService, *args: Any, **kwargs: Any) -> None:
        try:
            await original_run(service, *args, **kwargs)
        finally:
            owner_finished.set()

    monkeypatch.setattr(ReplayStore, "observation_targets", blocked)
    monkeypatch.setattr(watch_service.WatchService, "close", close)
    monkeypatch.setattr(watch_service.WatchService, "run", run)

    async def scenario() -> None:
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
            assert service._lease is not None and service._disk is not None
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


def test_actual_normal_close_finishes_admission_after_all_units(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, services, guards = actual_service(configuration, monkeypatch)
    # Successful closure uses the host default, not the injected timeout fault.
    config = replace(config, drain_timeout=HostConfig.drain_timeout)

    async def scenario() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(hosting.run(config, stop=stop))
        try:
            await until(lambda: services and cast(Connector, services[0].connector).entered.is_set())
            assert record(guards[0])["state"] == "running"
            stop.set()
            await asyncio.wait_for(task, 5)
            service = services[0]
            assert service._lease is not None
            assert service.status()["closed"] and not service._lease.held
            assert all(unit.done() for unit in service._tasks)
            assert service._disk is None or service._disk.done()
            assert record(guards[0])["state"] == "finished"
        finally:
            stop.set()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_actual_startup_failure_releases_only_after_initial_work_settles(
    configuration: Configuration, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config, services, guards = actual_service(configuration, monkeypatch)
    make = getattr(hosting, "WatchService")

    def invalid_after_construction(*args: Any, **kwargs: Any) -> WatchService:
        service = cast(WatchService, make(*args, **kwargs))
        config.store().path.write_text("{}", encoding="utf-8")
        return service

    monkeypatch.setattr(hosting, "WatchService", invalid_after_construction)

    async def scenario() -> None:
        with pytest.raises(ReplayStoreInvalid):
            await hosting.run(config)
        service = services[0]
        assert service._lease is not None
        assert not service._lease.held
        assert service._disk is None or service._disk.done()
        assert record(guards[0])["state"] == "finished"

    asyncio.run(scenario())

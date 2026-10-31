import asyncio
import time
import threading
from types import SimpleNamespace

import pytest
from single_instance_lease import AlreadyRunningError, SingleInstance

from agent_ssh_service import hosting
from agent_ssh_service.gateway import SSHService
from agent_ssh_service.store import DirectoryStore
from agent_ssh_service.config import SSHServiceError
from agent_ssh_service.hosting import run
from fleet_manager.gateway_service_handler import CallerContext, ServiceRequest
from fleet_manager.gateway_service_channel import TransportError
from fleet_manager.planning import canonical, decode
from fleet_manager.ssh_directory import DirectoryUpdate, SSHDirectoryClient

from conftest import enrollment


def request(principal, method, path, body=b""):
    return ServiceRequest(
        CallerContext("fleet-a", "gateway-a", "ssh", principal, int(time.time()) + 60),
        method, path, (), body,
    )


async def test_connected_machine_wakes_on_later_join_and_reconnect_catches_up(config):
    service = SSHService(DirectoryStore(config))
    first = await service.handle(request(
        "principal-worker-a", "POST", "/enroll", canonical(enrollment())))
    revision = decode(first.body)["directory"]["revision"]
    waiting = asyncio.create_task(service.handle(request(
        "principal-worker-a", "GET", f"/directory?after={revision}&wait=10")))
    await asyncio.sleep(0.02)
    assert not waiting.done()
    joined = await service.handle(request(
        "principal-worker-b", "POST", "/enroll", canonical(enrollment(1))))
    notice = await asyncio.wait_for(waiting, 2)
    assert notice.status == 200 and notice.body == joined.body
    again = await service.handle(request("principal-worker-a", "GET", "/directory"))
    assert again.body == joined.body
    duplicate = await service.handle(request(
        "principal-worker-b", "POST", "/enroll", canonical(enrollment(1))))
    assert duplicate.body == joined.body


@pytest.mark.parametrize("principal,path", [
    ("unknown", "/directory"), ("principal-worker-a", "/directory?after=999"),
    ("principal-worker-a", "/directory?after=0&after=1"),
    ("principal-worker-a", "/directory?wait=26"),
])
async def test_notification_does_not_accept_unknown_callers_or_positions(config, principal, path):
    service = SSHService(DirectoryStore(config))
    response = await service.handle(request(principal, "GET", path))
    assert response.status in (400, 403)


async def test_foreign_gateway_context_is_not_a_service_grant(config):
    service = SSHService(DirectoryStore(config))
    selected = request("principal-worker-a", "GET", "/directory")
    foreign = ServiceRequest(
        CallerContext("foreign-fleet", "gateway-a", "ssh", "principal-worker-a",
                      int(time.time()) + 60), selected.method, selected.target, (), b"",
    )
    assert (await service.handle(foreign)).status == 403


async def test_drain_releases_wait_without_fabricating_a_new_revision(config):
    service = SSHService(DirectoryStore(config))
    waiting = asyncio.create_task(service.handle(request(
        "principal-worker-a", "GET", "/directory?after=1&wait=20")))
    await asyncio.sleep(0.02)
    await service.close()
    assert (await asyncio.wait_for(waiting, 2)).status == 503
    assert service.store.snapshot()["directory"]["revision"] == 1


async def test_waiter_limit_keeps_gateway_channel_capacity_for_enrollment(config):
    service = SSHService(DirectoryStore(config))
    service.waiters = 6
    refused = await service.handle(request(
        "principal-worker-a", "GET", "/directory?after=1&wait=20"))
    assert refused.status == 503
    joined = await service.handle(request(
        "principal-worker-a", "POST", "/enroll", canonical(enrollment())))
    assert joined.status == 200


async def test_fleet_watch_consumes_actual_service_updates_and_stops_owned_wait(config):
    service = SSHService(DirectoryStore(config))
    stop = asyncio.Event()

    class Caller:
        async def request(self, method, target, *, body=b"", response_limit):
            assert response_limit == 32768
            return await service.handle(request(
                "principal-worker-a", method, target.removeprefix("/ssh"), body))

    client = SSHDirectoryClient(Caller())
    accepted = []
    caught_up = asyncio.Event()

    def accept(update):
        assert isinstance(update, DirectoryUpdate)
        accepted.append(update.revision)
        caught_up.set()
        if update.revision == 2:
            stop.set()

    owner = asyncio.create_task(client.watch(stop, accept))
    try:
        await asyncio.wait_for(caught_up.wait(), 2)
        await client.enroll(enrollment())
        await asyncio.wait_for(owner, 2)
        assert accepted == [1, 2]
        assert service.waiters == 0
    finally:
        stop.set()
        await asyncio.gather(owner, return_exceptions=True)


@pytest.mark.parametrize("status", [429, 503])
async def test_fleet_watch_recovers_after_sustained_notification_capacity_refusals(
    config, monkeypatch, status,
):
    service = SSHService(DirectoryStore(config))
    stop, delays, accepted = asyncio.Event(), [], []
    original_wait_for = asyncio.wait_for

    async def fast_backoff(awaitable, timeout):
        if timeout <= 4:
            delays.append(timeout)
            awaitable.close()
            await asyncio.sleep(0)
            raise asyncio.TimeoutError
        return await original_wait_for(awaitable, timeout)

    class Caller:
        calls = 0

        async def request(self, method, target, *, response_limit):
            assert method == "GET" and response_limit == 32768
            self.calls += 1
            if self.calls <= 12:
                return SimpleNamespace(status=status, body=b"")
            return await service.handle(request(
                "principal-worker-a", method, target.removeprefix("/ssh")))

    caller = Caller()
    monkeypatch.setattr(asyncio, "wait_for", fast_backoff)

    def accept(update):
        accepted.append(update.revision)
        stop.set()

    await original_wait_for(SSHDirectoryClient(caller).watch(stop, accept), 2)
    assert caller.calls == 13 and accepted == [1]
    assert delays == [1, 2, 3] + [4] * 9


@pytest.mark.parametrize("status", [401, 403])
async def test_fleet_watch_preserves_fatal_authentication_refusals(status):
    class Caller:
        calls = 0

        async def request(self, method, target, *, response_limit):
            self.calls += 1
            return SimpleNamespace(status=status, body=b"")

    caller, accepted = Caller(), []
    with pytest.raises(TransportError) as error:
        await SSHDirectoryClient(caller).watch(asyncio.Event(), accepted.append)
    assert error.value.status == status and caller.calls == 1 and accepted == []


async def test_fleet_watch_can_stop_while_capacity_is_unavailable(monkeypatch):
    stop, accepted = asyncio.Event(), []
    original_wait_for = asyncio.wait_for

    async def stop_during_backoff(awaitable, timeout):
        if timeout <= 4:
            stop.set()
        return await original_wait_for(awaitable, timeout)

    class Caller:
        calls = 0

        async def request(self, method, target, *, response_limit):
            self.calls += 1
            return SimpleNamespace(status=503, body=b"")

    caller = Caller()
    monkeypatch.setattr(asyncio, "wait_for", stop_during_backoff)
    await original_wait_for(SSHDirectoryClient(caller).watch(stop, accepted.append), 2)
    assert stop.is_set() and caller.calls == 1 and accepted == []


async def test_fleet_watch_does_not_retry_a_failed_local_consumer(config):
    service, calls = SSHService(DirectoryStore(config)), []

    class Caller:
        async def request(self, method, target, *, response_limit):
            calls.append(target)
            return await service.handle(request(
                "principal-worker-a", method, target.removeprefix("/ssh")))

    def accept(update):
        raise RuntimeError("synthetic local convergence failure")

    with pytest.raises(RuntimeError, match="local convergence failure"):
        await SSHDirectoryClient(Caller()).watch(asyncio.Event(), accept)
    assert len(calls) == 1


async def test_cancelled_handler_settles_its_disk_worker_before_returning(config, monkeypatch):
    service = SSHService(DirectoryStore(config))
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    def blocked(*args, **kwargs):
        started.set()
        try:
            assert release.wait(3)
            kwargs["checkpoint"]()
            return {}, False
        finally:
            finished.set()

    monkeypatch.setattr(service.store, "enroll", blocked)
    selected = request("principal-worker-a", "POST", "/enroll", canonical(enrollment()))
    handler = asyncio.create_task(service.handle(selected))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        selected.lifetime.close()
        handler.cancel()
        await asyncio.sleep(0.02)
        assert not handler.done() and not finished.is_set()
        handler.cancel()
        closing = asyncio.create_task(service.close())
        await asyncio.sleep(0.02)
        closing.cancel()
        await asyncio.sleep(0.02)
        closing.cancel()
        await asyncio.sleep(0.02)
        assert not handler.done() and not closing.done() and not finished.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await handler
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert finished.is_set()
        assert service.store.snapshot()["directory"]["revision"] == 1
    finally:
        release.set()
        await asyncio.gather(handler, return_exceptions=True)


async def test_foreign_data_directory_is_not_adopted_before_credentials_or_lease(config):
    root = config.to_dict()["data_dir"]
    from pathlib import Path

    foreign = Path(root) / "other-service-state"
    foreign.write_text("preserve", encoding="utf-8")
    with pytest.raises(SSHServiceError, match="unowned-service"):
        await run(config)
    assert foreign.read_text("utf-8") == "preserve"
    assert not (Path(root) / "directory.sqlite3").exists()


@pytest.mark.parametrize("operation", ["enrollment", "reconciliation"])
async def test_repeated_host_and_handler_cancellation_retains_actual_writer_lease(
    config, monkeypatch, operation,
):
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    selected = request("principal-worker-a", "POST", "/enroll", canonical(enrollment()))
    enroll = DirectoryStore.enroll

    def blocked():
        started.set()
        try:
            assert release.wait(10)
        finally:
            finished.set()

    def blocked_enrollment(store, *args, **kwargs):
        blocked()
        return enroll(store, *args, **kwargs)

    class Connector:
        def __init__(self, *, handler, **_):
            self.binding = handler

        async def run(self, stop):
            service = self.binding.handle.__self__
            if operation == "reconciliation":
                service.reconcile_relay = blocked
            pending = asyncio.create_task(self.binding.handle(selected))
            try:
                await stop.wait()
            finally:
                selected.lifetime.close()
                pending.cancel()
                await asyncio.sleep(0)
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)

    monkeypatch.setattr(hosting, "ServiceConnector", Connector)
    monkeypatch.setattr(hosting, "load_service_credentials",
                        lambda **_: SimpleNamespace(access_token=None, proof_key=None))
    if operation == "enrollment":
        monkeypatch.setattr(DirectoryStore, "enroll", blocked_enrollment)
    owner = asyncio.create_task(run(config))
    contender = SingleInstance(config.to_dict()["data_dir"], service="agent-ssh-service")
    try:
        assert await asyncio.to_thread(started.wait, 3)
        owner.cancel()
        await asyncio.sleep(0.02)
        owner.cancel()
        await asyncio.sleep(0.02)
        assert not owner.done() and not finished.is_set()
        with pytest.raises(AlreadyRunningError):
            contender.acquire()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await owner
        assert finished.is_set()
        contender.acquire()
    finally:
        release.set()
        await asyncio.gather(owner, return_exceptions=True)
        contender.release()

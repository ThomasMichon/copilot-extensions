import asyncio
import time
import threading

import pytest

from agent_ssh_service.gateway import SSHService
from agent_ssh_service.store import DirectoryStore
from agent_ssh_service.config import SSHServiceError
from agent_ssh_service.hosting import run
from fleet_manager.gateway_service_handler import CallerContext, ServiceRequest
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
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await handler
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

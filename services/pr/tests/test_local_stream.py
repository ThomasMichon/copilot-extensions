"""Actual Unix SSE/client views, unchanged provider traffic and joined withdrawal."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
import json
from pathlib import Path
import sys
import time
from typing import TYPE_CHECKING, Any, cast

import pytest

from agent_pull_requests.watch_service import WatchService
from agent_pull_requests.watch_local import LocalIngress
from agent_pull_requests_service.local_client import LocalClient, LocalClientError
from agent_pull_requests_service.local_config import LocalHostConfig, load_service_config

if TYPE_CHECKING:
    from services.pr.tests.test_local_repository import NATIVE, configuration
else:
    from test_local_repository import NATIVE, configuration

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux private Unix ingress")


def test_prior_host_stream_capability_refuses_before_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def prior(_self: LocalClient, method: str, operation: str, value: Any = None) -> dict[str, Any]:
        calls.append((method, operation))
        assert operation == "capabilities"
        return {
            "protocol": "agent-pull-requests.repository-cache/v1", "namespace": "test",
            "repository_key": "selected", "continuing": True,
            "storage": "repository_cache_version:2", "forge_writes": False, "task_emissions": False,
        }

    monkeypatch.setattr(LocalClient, "_http", prior)
    client = LocalClient(tmp_path / "not-started.sock", "test", "repository")
    with pytest.raises(LocalClientError, match="unsupported"):
        list(client.stream({"registration_id": "original", "reviewer": "reviewer"}))
    assert calls == [("GET", "capabilities")]


def test_actual_stream_all_views_resume_no_ack_no_provider_reads_and_drain(tmp_path: Path) -> None:
    path, value, state, log = configuration(tmp_path)
    value.update(reviewer_actions=True, interval=300, operation_seconds=5)
    path.write_text(json.dumps(value))
    state.write_text(json.dumps({"head": "a" * 40}))
    script = Path(value["repository_cache"]["repositories"][0]["prefix"][1])
    script.write_text(NATIVE.replace(
        '"base_ref":"dev"',
        '"base_ref":"dev","base_sha":"' + "b" * 40 + '","author":"author",'
        '"reviews_complete":True,"updated_at":"1970-01-01T00:01:00Z"',
    ))
    selected = load_service_config(path)
    assert isinstance(selected, LocalHostConfig)
    selected.provision()

    async def scenario() -> None:
        service = WatchService(
            selected.store(), fleet=None, gateway=None, slot=None,
            bindings=selected.bindings, observations=selected.observations,
            gateway_origin=None, access_token_callback=None, proof_key=None,
            authority=lambda: int(time.time()) + 30, checkpoint=lambda: None,
            interval=300, local_socket=selected.repositories.socket_path,
            local_peers=selected.repositories.peers, operation_seconds=5,
        )
        await service.start()
        iterator = None
        try:
            await asyncio.wait_for(service.connector.connected.wait(), 5)
            async def cycle() -> None:
                while service._last_cycle is None:
                    await asyncio.sleep(.02)
            await asyncio.wait_for(cycle(), 10)
            client = LocalClient(selected.repositories.socket_path, "test", "repo-github")
            subscribed = await asyncio.to_thread(client.request, "subscribe", {
                "request_id": "streaming", "ttl": 3600, "after": 0,
            })
            registration = subscribed["subscription"]["registration_id"]
            payload = {"registration_id": registration, "reviewer": "reviewer", "seconds": 2}
            calls = log.read_bytes()
            iterator = client.stream(payload)
            frames: list[dict[str, Any]] = []
            for _ in range(3):
                frames.append(await asyncio.to_thread(next, iterator))
            assert [frame["event"] for frame in frames] == ["status", "pulls", "next-actions"]
            assert frames[1]["data"]["view"]["pulls"][0]["number"] == 7
            assert frames[2]["data"]["view"]["next_actions"][0]["action"] == "initial_verdict"
            cursor = frames[-1]["id"]
            await asyncio.to_thread(iterator.close)
            iterator = None
            resumed = LocalClient(selected.repositories.socket_path, "test", "repo-github")
            with pytest.raises(LocalClientError, match="binding_mismatch"):
                await asyncio.to_thread(lambda: list(resumed.stream(
                    payload | {"reviewer": "other"}, last_event_id=cursor,
                )))
            iterator = resumed.stream(payload, last_event_id=cursor)
            frame = await asyncio.to_thread(next, iterator)
            assert frame["event"] == "status" and not frame["data"]["view"]["events"]
            inspected = await asyncio.to_thread(client.request, "inspect", {"registration_id": registration})
            assert inspected["subscription"]["acknowledged"] == 0
            assert inspected["subscription"]["expires_at"] == subscribed["subscription"]["expires_at"]
            assert log.read_bytes() == calls
            await service.close(timeout=5)
            with pytest.raises(LocalClientError):
                await asyncio.to_thread(lambda: list(iterator))
        finally:
            if iterator is not None:
                await asyncio.to_thread(iterator.close)
            await service.close(timeout=10)
        assert not service.status()["writer_held"] and not selected.repositories.socket_path.exists()
        assert not cast(LocalIngress, service.connector)._tasks and not service.gateway._tasks

    asyncio.run(scenario())


def test_stream_original_ingress_deadline_bounds_idle_and_slow_writers(tmp_path: Path) -> None:
    from agent_pull_requests.repository_stream import LocalStreamResponse
    from agent_pull_requests_service.local_client import _Connection
    import os

    socket_path = tmp_path / "finite.sock"

    async def idle() -> AsyncGenerator[bytes, None]:
        yield b"event: status\ndata: {}\n\n"
        await asyncio.sleep(30)

    async def slow() -> AsyncGenerator[bytes, None]:
        for _ in range(32):
            yield b"x" * 1_048_576

    async def scenario() -> None:
        for producer in (idle, slow):
            async def handler(_request: Any) -> LocalStreamResponse:
                return LocalStreamResponse(producer())
            stop = asyncio.Event()
            ingress = LocalIngress(
                path=socket_path, namespace="test", peers=((getattr(os, "getuid", lambda: -1)(), "reader"),),
                handler=handler, operation_seconds=1,
            )
            owner = asyncio.create_task(ingress.run(stop))
            await asyncio.wait_for(ingress.connected.wait(), 2)
            connection = _Connection(socket_path)
            started = time.monotonic()
            try:
                await asyncio.to_thread(connection.request, "GET", "/")
                # Do not consume the response: the large producer fills the socket.
                await asyncio.sleep(1.3)
                assert not ingress._tasks
                assert time.monotonic() - started < 3
            finally:
                connection.close()
                stop.set()
                await asyncio.wait_for(owner, 3)
            assert not socket_path.exists()

    asyncio.run(scenario())

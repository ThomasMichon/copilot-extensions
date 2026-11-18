"""Real Unix/cache exchanges and bounded cancellation, without coordinator activation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from typing import Any, TYPE_CHECKING

import pytest

from agent_pull_requests_service.async_local_client import AsyncBoundRepositoryClient
from agent_pull_requests_service import async_local_client
from agent_pull_requests_service.local_client import LocalClientError, LocalDeadlineExpired

if TYPE_CHECKING:
    from services.pr.tests.test_bound_local_client import Host, running
else:
    from test_bound_local_client import Host, running

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux private Unix client")


def client(host: Host, **options: Any) -> AsyncBoundRepositoryClient:
    return AsyncBoundRepositoryClient(
        host.path, "repository", identity=host.identity, consumer="consumer", **options,
    )


def test_actual_async_cache_selection_and_original_registration(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with running(tmp_path) as host:
            selected = client(host)
            before = host.store.path.read_bytes()
            capabilities = await selected.negotiate()
            capabilities["identity"]["repo"] = "changed/output"
            page = await selected.request("next-actions", {"reviewer": "reviewer"})
            assert page["identity"] == host.identity.document()
            assert host.store.path.read_bytes() == before
            original = {"request_id": "original", "ttl": 60}
            receipt = await selected.request("subscribe", original)
            replay = await selected.request("subscribe", original)
            assert replay["subscription"] == receipt["subscription"]
            assert all(call["body"]["expected_consumer"] == "consumer"
                       for call in host.calls if call["method"] == "POST")
            assert all(call["operation"] != "acknowledge" for call in host.calls)
    asyncio.run(scenario())


@pytest.mark.parametrize("feature", ["absent", "unsupported"])
def test_unsupported_selection_cannot_reach_a_mutation(tmp_path: Path, feature: str) -> None:
    async def scenario() -> None:
        async with running(tmp_path, feature=feature) as host:
            before = host.store.path.read_bytes()
            with pytest.raises(LocalClientError, match="unsupported"):
                await client(host).request("subscribe", {"request_id": "original", "ttl": 60})
            assert [call["method"] for call in host.calls] == ["GET"]
            assert host.store.path.read_bytes() == before
    asyncio.run(scenario())


def test_remapped_consumer_refuses_without_adopting_another_scope(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with running(tmp_path) as host:
            selected = client(host)
            receipt = await selected.request("subscribe", {"request_id": "original", "ttl": 60})
            host.select("remapped")
            before = host.store.path.read_bytes()
            with pytest.raises(LocalClientError, match="repository_consumer_mismatch"):
                await selected.request("inspect", {
                    "registration_id": receipt["subscription"]["registration_id"],
                })
            assert host.store.path.read_bytes() == before
    asyncio.run(scenario())


@pytest.mark.parametrize("cancel", [False, True])
def test_slow_peer_yields_and_releases_owned_connection(
    tmp_path: Path, cancel: bool,
) -> None:
    async def scenario() -> None:
        if sys.platform != "linux":
            return
        async with running(tmp_path) as host:
            entered, closed = asyncio.Event(), asyncio.Event()

            async def hold(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
                try:
                    await reader.readuntil(b"\r\n\r\n")
                    entered.set()
                    assert await reader.read() == b""
                    closed.set()
                finally:
                    writer.close()
                    await writer.wait_closed()

            path = tmp_path / "slow.sock"
            server = await asyncio.start_unix_server(hold, str(path))
            selected = AsyncBoundRepositoryClient(
                path, "repository", identity=host.identity, consumer="consumer", operation_seconds=0.1,
            )
            async with server:
                task = asyncio.create_task(selected.negotiate())
                await asyncio.wait_for(entered.wait(), 1)
                assert not task.done()
                if cancel:
                    task.cancel()
                with pytest.raises(asyncio.CancelledError if cancel else LocalDeadlineExpired):
                    await task
                await asyncio.wait_for(closed.wait(), 1)
                assert not selected._busy
            assert not host.calls
    asyncio.run(scenario())


def test_elapsed_original_deadline_refuses_before_socket_io(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with running(tmp_path) as host:
            selected = client(host)
            selected.deadline = time.monotonic() - 1
            with pytest.raises(LocalDeadlineExpired):
                await selected.request("subscribe", {"request_id": "original", "ttl": 60})
            assert not host.calls
    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["prepare", "send"])
def test_expiry_during_synchronous_preparation_cannot_start_an_exchange(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    async def scenario() -> None:
        async with running(tmp_path) as host:
            selected = client(host)
            if stage == "send":
                await selected.negotiate()
            before = len(host.calls)
            clock = [1000.0]
            native_freeze = async_local_client._freeze_request
            native_dumps = json.dumps

            def detach(payload: dict[str, Any] | None) -> dict[str, Any] | None:
                result = native_freeze(payload)
                if stage == "prepare":
                    clock[0] += 31
                return result

            def encode(*args: Any, **kwargs: Any) -> str:
                if stage == "send":
                    clock[0] += 31
                return native_dumps(*args, **kwargs)

            monkeypatch.setattr(async_local_client, "time", SimpleNamespace(monotonic=lambda: clock[0]))
            monkeypatch.setattr(async_local_client, "_freeze_request", detach)
            monkeypatch.setattr(async_local_client, "json", SimpleNamespace(
                dumps=encode, loads=json.loads, JSONDecodeError=json.JSONDecodeError,
            ))
            selected.deadline = 1001.0
            with pytest.raises(LocalDeadlineExpired):
                await selected.request("subscribe", {"request_id": "original", "ttl": 60})
            assert len(host.calls) == before and not selected._busy
    asyncio.run(scenario())


@pytest.mark.parametrize("payload", [
    {"request_id": "x" * 1_000_000},
    {str(index): index for index in range(9)},
    {"after": 1 << 100_000},
    {"request_id": {"nested": ["not", "a", "scalar"]}},
])
def test_oversized_or_nested_input_refuses_without_serialization_or_ipc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: dict[str, Any],
) -> None:
    async def scenario() -> None:
        async with running(tmp_path) as host:
            def forbidden(*args: Any, **kwargs: Any) -> str:
                raise AssertionError("unbounded input reached JSON encoding")

            monkeypatch.setattr(async_local_client, "json", SimpleNamespace(
                dumps=forbidden, loads=json.loads, JSONDecodeError=json.JSONDecodeError,
            ))
            with pytest.raises(LocalClientError, match="bounded scalar"):
                await asyncio.wait_for(client(host).request("subscribe", payload), 0.1)
            assert not host.calls
    asyncio.run(scenario())


def test_peer_uid_is_checked_before_request_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async with running(tmp_path) as host:
            monkeypatch.setattr(async_local_client, "os", SimpleNamespace(getuid=lambda: -1))
            before = host.store.path.read_bytes()
            with pytest.raises(LocalClientError, match="OS identity"):
                await client(host).negotiate()
            assert not host.calls and host.store.path.read_bytes() == before
    asyncio.run(scenario())


def test_original_payload_is_frozen_and_parallel_use_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async with running(tmp_path) as host:
            selected = client(host)
            entered, release = asyncio.Event(), asyncio.Event()
            native = selected._http

            async def paused(
                method: str, operation: str, payload: dict[str, Any] | None = None,
            ) -> dict[str, Any]:
                if method == "GET":
                    entered.set()
                    await release.wait()
                return await native(method, operation, payload)

            monkeypatch.setattr(selected, "_http", paused)
            original = {"request_id": "original", "ttl": 60}
            task = asyncio.create_task(selected.request("subscribe", original))
            await asyncio.wait_for(entered.wait(), 1)
            original["request_id"] = "replacement"
            try:
                with pytest.raises(LocalClientError, match="in-flight"):
                    await selected.request("subscribe", original)
            finally:
                release.set()
            await task
            assert host.calls[-1]["body"]["request"]["request_id"] == "original"
            assert len(host.calls) == 2
    asyncio.run(scenario())


def test_cancelled_committed_registration_reconciles_original_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async with running(tmp_path) as host:
            gateway = host.gateways[-1]
            native: Callable[..., Any] = gateway.handle_local
            committed, release = asyncio.Event(), asyncio.Event()
            accepted: dict[str, Any] = {}

            async def delay_committed(request: Any) -> Any:
                response = await native(request)
                if request.target.endswith("/subscribe") and not accepted:
                    accepted.update(json.loads(response.body)["result"])
                    committed.set()
                    await release.wait()
                return response

            monkeypatch.setattr(gateway, "handle_local", delay_committed)
            selected = client(host)
            original = {"request_id": "original", "ttl": 60}
            task = asyncio.create_task(selected.request("subscribe", original))
            try:
                await asyncio.wait_for(committed.wait(), 2)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            finally:
                release.set()
            receipt = await selected.request("subscribe", original)
            assert receipt["subscription"] == accepted["subscription"]
            assert [call["body"]["request"]["request_id"] for call in host.calls
                    if call["operation"] == "subscribe"] == ["original", "original"]
            assert all(call["operation"] != "acknowledge" for call in host.calls)
    asyncio.run(scenario())


@pytest.mark.parametrize("response", [
    b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nContent-Length: 2\r\n\r\n{}",
    b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n",
    b"HTTP/1.1 200 OK\r\nContent-Length: 1048577\r\n\r\n",
    b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\n{}",
    b"HTTP/1.1 200 OK\r\nContent-Length: 13\r\n\r\n{\"x\":1,\"x\":2}",
])
def test_malformed_peer_responses_leave_selection_unset(tmp_path: Path, response: bytes) -> None:
    async def scenario() -> None:
        if sys.platform != "linux":
            return
        async with running(tmp_path) as host:
            async def reply(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
                try:
                    await reader.readuntil(b"\r\n\r\n")
                    writer.write(response)
                    await writer.drain()
                finally:
                    writer.close()
                    await writer.wait_closed()

            path = tmp_path / "malformed.sock"
            server = await asyncio.start_unix_server(reply, str(path))
            selected = AsyncBoundRepositoryClient(
                path, "repository", identity=host.identity, consumer="consumer",
            )
            async with server:
                with pytest.raises(LocalClientError):
                    await selected.negotiate()
            assert selected._selection.binding is None and not host.calls
    asyncio.run(scenario())

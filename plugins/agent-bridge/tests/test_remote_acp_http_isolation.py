"""Deterministic read-lock starvation and independent HTTP client ownership."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import httpx
import pytest

from agent_bridge.remote_acp_config import RemoteAcpTarget
from agent_bridge.remote_acp_http import HttpAcpTransport
from agent_bridge.remote_acp_transport import RemoteTransportError


class ReadLockedStream(httpx.AsyncByteStream):
    def __init__(self, pool: ReadLockedPool, *, quiet: bool) -> None:
        self.pool = pool
        self.quiet = quiet
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async with self.pool.read_lock:
            if self.quiet:
                self.pool.reading.set()
                await asyncio.Event().wait()
            self.pool.body_read.set()
            yield b""

    async def aclose(self) -> None:
        self.closed = True


class ReadLockedPool(httpx.AsyncBaseTransport):
    def __init__(self) -> None:
        self.read_lock = asyncio.Lock()
        self.reading = asyncio.Event()
        self.body_read = asyncio.Event()
        self.requests: list[httpx.Request] = []
        self.streams: list[ReadLockedStream] = []
        self.closed = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        quiet = request.method == "GET"
        stream = ReadLockedStream(self, quiet=quiet)
        self.streams.append(stream)
        return httpx.Response(
            200 if quiet else 202,
            headers={
                "content-type": "text/event-stream" if quiet else "application/json",
                "set-cookie": "stream-affinity=retained; Path=/; Secure",
            },
            stream=stream,
            extensions={"http_version": b"HTTP/2"},
        )

    async def aclose(self) -> None:
        self.closed = True


def target() -> RemoteAcpTarget:
    return RemoteAcpTarget(
        endpoint="https://localhost/acp", transport="streamable-http",
        auth={"source": "none", "identity": "synthetic"}, allow_loopback_test=True,
        timeout_seconds=1,
    )


@pytest.mark.asyncio
async def test_shared_read_lock_fixture_blocks_an_already_completed_control_response() -> None:
    pool = ReadLockedPool()
    async with httpx.AsyncClient(transport=pool) as client:
        quiet = await client.send(client.build_request("GET", "https://localhost/acp"), stream=True)
        reading = asyncio.create_task(quiet.aread())
        control = None
        try:
            await asyncio.wait_for(pool.reading.wait(), 1)
            control = await client.send(
                client.build_request("POST", "https://localhost/acp"), stream=True,
            )
            assert control.status_code == 202
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(control.aread(), 0.05)
            assert not pool.body_read.is_set()
        finally:
            reading.cancel()
            await asyncio.gather(reading, return_exceptions=True)
            await quiet.aclose()
            if control is not None:
                await control.aclose()
    assert pool.closed and all(stream.closed for stream in pool.streams)


@pytest.mark.asyncio
async def test_quiet_connection_and_session_sse_cannot_starve_control_body_reads() -> None:
    clients: list[httpx.AsyncClient] = []
    pools: list[ReadLockedPool] = []

    def factory() -> httpx.AsyncClient:
        pool = ReadLockedPool()
        client = httpx.AsyncClient(transport=pool, trust_env=False, timeout=0.01)
        pools.append(pool)
        clients.append(client)
        return client

    wire = HttpAcpTransport(
        target(), client_factory=factory, cookies={"affinity": "synthetic"},
    )
    wire._connection_id = "synthetic-connection"
    wire.sessions.add("session-one")
    try:
        await wire._open_stream(None)
        await asyncio.wait_for(pools[-1].reading.wait(), 1)
        await wire._open_stream("session-one")
        await asyncio.wait_for(pools[-1].reading.wait(), 1)
        assert len(clients) == 3 and len(wire._stream_clients) == 2
        assert all(client.cookies.jar is wire.cookies.jar for client in clients)
        await asyncio.wait_for(wire.send({
            "jsonrpc": "2.0", "method": "session/cancel",
            "params": {"sessionId": "session-one"},
        }), 1)
        assert pools[0].body_read.is_set()
        assert all(pool.read_lock.locked() for pool in pools[1:])
        control = pools[0].requests[-1]
        assert control.headers["Acp-Session-Id"] == "session-one"
        assert "affinity=synthetic" in control.headers["cookie"]
        assert "stream-affinity=retained" in control.headers["cookie"]
        assert all(
            request.extensions["timeout"]["read"] == wire.target.idle_seconds
            for pool in pools[1:] for request in pool.requests
        )
        assert control.extensions["timeout"]["read"] == wire.target.timeout_seconds
    finally:
        await asyncio.wait_for(wire.close(), 2)
    assert not wire._stream_clients
    assert all(client.is_closed for client in clients)
    assert all(pool.closed and all(stream.closed for stream in pool.streams) for pool in pools)


@pytest.mark.asyncio
async def test_reused_stream_client_is_refused_and_control_client_still_closes() -> None:
    client = httpx.AsyncClient(transport=ReadLockedPool())
    wire = HttpAcpTransport(target(), client_factory=lambda: client)
    wire._connection_id = "synthetic-connection"
    try:
        with pytest.raises(RemoteTransportError, match="closed during stream setup"):
            await asyncio.wait_for(wire._open_stream(None), 2)
    finally:
        await asyncio.wait_for(wire.close(), 2)
    assert client.is_closed and not wire._stream_clients

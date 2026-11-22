"""ACP WS uses ordinary JSON-RPC, sharing the retained TCP Host controller."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import pytest_asyncio
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.typing import Subprotocol

from agent_remote_driver.session_host.client import SessionHostClient
from agent_remote_driver.session_host.host import ChildProcess, SessionHost
from agent_remote_driver.session_host.ws_transport import WebSocketTransport
from agent_remote_driver.session_host import launcher
from agent_remote_driver.session_host import ws_transport
from agent_remote_driver.session_host import protocol
from agent_remote_driver.service import DriverService
from agent_remote_driver.session_host.registered_inventory import HostEndpoint


class _Child:
    pid: int | None = None
    returncode: int | None = None

    def __init__(self) -> None:
        self.stdin = self
        self.stdout = asyncio.StreamReader()
        self.writes: list[bytes] = []
        self.done = asyncio.Event()

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    async def drain(self) -> None:
        pass

    def kill(self) -> None:
        self.returncode = 0
        self.stdout.feed_eof()
        self.done.set()

    async def wait(self) -> int:
        await self.done.wait()
        assert self.returncode is not None
        return self.returncode


Broker = tuple[_Child, SessionHost, int, WebSocketTransport]
Driver = tuple[_Child, SessionHost, int, list[HostEndpoint], DriverService, str]


@pytest_asyncio.fixture
async def broker() -> AsyncIterator[Broker]:
    child = _Child()
    host = SessionHost(cast(ChildProcess, child), nonce="fixture-tcp", lifetime_policy="acp")
    tcp = await host.serve()
    websocket = WebSocketTransport(tcp, "fixture-tcp")
    await websocket.serve()
    try:
        yield child, host, tcp, websocket
    finally:
        await websocket.close()
        await host.close()
        child.kill()


def _connect(websocket: WebSocketTransport, **kwargs: Any) -> connect:
    return connect(
        f"ws://127.0.0.1:{websocket.port}/acp",
        subprotocols=[Subprotocol("acp.v1"), Subprotocol("bearer." + websocket.nonce)],
        compression=None, proxy=None, **kwargs,
    )


async def _released(host: SessionHost) -> None:
    for _ in range(100):
        if host._front is None:
            return
        await asyncio.sleep(0.005)
    pytest.fail("controller did not release its broker connection")


async def _service_http(
    service: DriverService, path: str, *, credential: str | None = None, extra_headers: str = "",
) -> tuple[int, dict[str, Any]]:
    reader, writer = await asyncio.open_connection("127.0.0.1", service.port)
    prefix = "Bear" + "er "
    authorization = "" if credential is None else "Authorization: " + prefix + credential + "\r\n"
    try:
        writer.write((
            f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{service.port}\r\n"
            + authorization + extra_headers + "Connection: close\r\n\r\n"
        ).encode())
        await writer.drain()
        headers = (await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 3)).decode()
        length = next(
            int(line.split(":", 1)[1]) for line in headers.splitlines()
            if line.lower().startswith("content-length:")
        )
        body = await asyncio.wait_for(reader.readexactly(length), 3)
        return int(headers.split()[1]), json.loads(body)
    finally:
        writer.close()
        await writer.wait_closed()


@pytest_asyncio.fixture
async def driver_service(broker: Broker) -> AsyncIterator[Driver]:
    child, host, tcp, _ = broker
    records = [HostEndpoint("original-instance", "fixture-native", 0, tcp, "fixture-tcp")]
    credential = "fixture-service-capability-" + "x" * 32
    service = DriverService(
        credential, inventory=lambda: (list(records), {"availability": "available", "reason": None}),
    )
    await service.serve()
    try:
        yield child, host, tcp, records, service, credential
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_service_inventory_is_authenticated_and_does_not_export_host_capabilities(
    driver_service: Driver,
) -> None:
    child, host, _, _, service, credential = driver_service
    assert (await _service_http(service, "/health"))[0] == 403
    status, health = await _service_http(service, "/health", credential=credential)
    assert status == 200 and health["ready"] and health["transports"] == ["acp.v1"]
    status, inventory = await _service_http(service, "/sessions", credential=credential)
    assert status == 200 and inventory["sessions"] == [{
        "session_id": "fixture-native", "instance_id": "original-instance", "host_pid": 0,
        "acp_path": "/sessions/original-instance/acp",
    }]
    assert "fixture-tcp" not in json.dumps(inventory) and credential not in json.dumps(inventory)
    assert host._front is None and not child.writes


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [
    "Origin: http://untrusted.invalid\r\n",
    "Host: untrusted.invalid\r\n",
    "Authorization: invalid-duplicate\r\n",
])
async def test_service_http_rejects_ambiguous_or_browser_credentials(
    driver_service: Driver, headers: str,
) -> None:
    child, host, _, _, service, credential = driver_service
    status, _ = await _service_http(
        service, "/sessions", credential=credential, extra_headers=headers,
    )
    assert status == 403 and host._front is None and not child.writes


@pytest.mark.asyncio
async def test_service_relays_original_host_and_detaches_without_native_termination(
    driver_service: Driver,
) -> None:
    child, host, tcp, _, service, credential = driver_service
    request = {
        "jsonrpc": "2.0", "id": 13, "method": "session/prompt",
        "params": {"sessionId": "fixture-native", "prompt": [{"type": "text", "text": "literal"}]},
    }
    async with connect(
        f"ws://127.0.0.1:{service.port}/sessions/original-instance/acp",
        subprotocols=[Subprotocol("acp.v1"), Subprotocol("bearer." + credential)], proxy=None,
    ) as connection:
        await connection.send(json.dumps(request))
        for _ in range(100):
            if child.writes:
                break
            await asyncio.sleep(.005)
        assert [json.loads(value) for value in child.writes] == [request]
        reply = {"jsonrpc": "2.0", "id": 13, "result": {"stopReason": "end_turn"}}
        child.stdout.feed_data((json.dumps(reply) + "\n").encode())
        assert json.loads(await connection.recv()) == reply
    await _released(host)
    await service.close()
    assert child.returncode is None and len(child.writes) == 1
    client = await SessionHostClient.connect(port=tcp)
    try:
        hello = await client.attach(nonce=b"fixture-tcp")
        assert hello.max_seq == host.ack_cursor == 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_service_never_follows_replacement_instance_or_creates_a_session(
    driver_service: Driver,
) -> None:
    child, host, tcp, records, service, credential = driver_service
    records[:] = [HostEndpoint("replacement", "fixture-native", 0, tcp, "fixture-tcp")]
    async with connect(
        f"ws://127.0.0.1:{service.port}/sessions/original-instance/acp",
        subprotocols=[Subprotocol("acp.v1"), Subprotocol("bearer." + credential)], proxy=None,
    ) as connection:
        with pytest.raises(ConnectionClosed) as refused:
            await connection.recv()
        assert refused.value.rcvd is not None and refused.value.rcvd.code == 1013
    assert host._front is None and not child.writes


@pytest.mark.asyncio
@pytest.mark.parametrize("matching", [True, False])
async def test_service_accepts_bridge_dual_credentials_only_when_they_agree(
    driver_service: Driver, matching: bool,
) -> None:
    child, host, _, _, service, credential = driver_service
    scheme = "Bear" + "er "
    header = scheme + (credential if matching else "different-capability")
    connection = connect(
        f"ws://127.0.0.1:{service.port}/sessions/original-instance/acp",
        subprotocols=[Subprotocol("acp.v1"), Subprotocol("bearer." + credential)], proxy=None,
        additional_headers={"Authorization": header},
    )
    if matching:
        async with connection as websocket:
            assert websocket.subprotocol == "acp.v1"
    else:
        with pytest.raises(InvalidStatus):
            async with connection:
                pytest.fail("contradictory credentials accepted")
    await _released(host)
    assert child.returncode is None and not child.writes


@pytest.mark.asyncio
async def test_service_drain_keeps_accepted_native_stream_until_frontend_detaches(
    driver_service: Driver,
) -> None:
    child, host, _, _, service, credential = driver_service
    drain = None
    try:
        async with connect(
            f"ws://127.0.0.1:{service.port}/sessions/original-instance/acp",
            subprotocols=[Subprotocol("acp.v1"), Subprotocol("bearer." + credential)], proxy=None,
        ) as connection:
            request = {"jsonrpc": "2.0", "id": 29, "method": "initialize", "params": {}}
            await connection.send(json.dumps(request))
            for _ in range(100):
                if child.writes:
                    break
                await asyncio.sleep(.005)
            assert [json.loads(value) for value in child.writes] == [request]
            drain = asyncio.create_task(service.drain())
            for _ in range(100):
                if service.draining:
                    break
                await asyncio.sleep(.005)
            assert service.draining and not drain.done()
            response = {"jsonrpc": "2.0", "id": 29, "result": {"protocolVersion": 1}}
            child.stdout.feed_data((json.dumps(response) + "\n").encode())
            assert json.loads(await connection.recv()) == response
        await asyncio.wait_for(drain, 3)
        await _released(host)
        assert child.returncode is None and len(child.writes) == 1
    finally:
        if drain is not None and not drain.done():
            drain.cancel()
            await asyncio.gather(drain, return_exceptions=True)


@pytest.mark.asyncio
async def test_acp_ws_literal_permission_roundtrip_and_same_child_tcp_handover(broker: Broker) -> None:
    child, host, tcp, websocket = broker
    literal = 'first line\n"quoted" & literal $value \u00e9'
    request = {
        "jsonrpc": "2.0", "id": 7, "method": "session/prompt",
        "params": {"sessionId": "fixture-native", "prompt": [{"type": "text", "text": literal}]},
    }
    async with _connect(websocket) as connection:
        assert connection.subprotocol == "acp.v1"
        await connection.send(json.dumps(request, indent=2))
        for _ in range(100):
            if child.writes:
                break
            await asyncio.sleep(0.005)
        assert len(child.writes) == 1 and json.loads(child.writes[0]) == request
        competitor = await SessionHostClient.connect(port=tcp)
        try:
            with pytest.raises(ConnectionError):
                await competitor.attach(nonce=b"fixture-tcp")
        finally:
            await competitor.close()
        async with _connect(websocket) as other_connection:
            with pytest.raises(ConnectionClosed) as refused:
                await other_connection.recv()
            assert refused.value.rcvd is not None and refused.value.rcvd.code == 1013
        permission = {"jsonrpc": "2.0", "id": "permit",
                      "method": "session/request_permission", "params": {}}
        child.stdout.feed_data((json.dumps(permission) + "\n").encode())
        assert json.loads(await connection.recv()) == permission
        response = {"jsonrpc": "2.0", "id": "permit", "result": {"outcome": "approved"}}
        await connection.send(json.dumps(response))
        final = {"jsonrpc": "2.0", "id": 7, "result": {"stopReason": "end_turn"}}
        child.stdout.feed_data((json.dumps(final) + "\n").encode())
        assert json.loads(await connection.recv()) == final
    await _released(host)
    assert child.returncode is None and json.loads(child.writes[-1]) == response
    client = await SessionHostClient.connect(port=tcp)
    try:
        hello = await client.attach(nonce=b"fixture-tcp")
        assert hello.child_pid == 0 and hello.max_seq == 2
        assert host.ack_cursor == 2
        await client.terminate()
        assert [frame async for frame in client.frames()] == []
        assert not client.child_alive
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["nonce", "origin", "host", "path", "duplicate-auth", "protocol"])
async def test_ws_refuses_untrusted_handshake_without_controller_effects(
    broker: Broker, kind: str,
) -> None:
    child, host, _, websocket = broker
    url = f"ws://127.0.0.1:{websocket.port}/acp"
    protocols = [Subprotocol("acp.v1"), Subprotocol("bearer." + websocket.nonce)]
    headers = {}
    if kind == "nonce":
        protocols[-1] = Subprotocol("bearer.wrong")
    elif kind == "origin":
        headers["Origin"] = "http://untrusted.invalid"
    elif kind == "host":
        headers["Host"] = "untrusted.invalid"
    elif kind == "path":
        url += "/other"
    elif kind == "duplicate-auth":
        protocols += [Subprotocol("bearer.wrong")]
    else:
        protocols.remove(Subprotocol("acp.v1"))
    with pytest.raises(InvalidStatus):
        async with connect(url, subprotocols=protocols, additional_headers=headers, proxy=None):
            pytest.fail("untrusted handshake accepted")
    assert host._front is None and not child.writes


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [b"{}", "[]", '{"jsonrpc":"1.0"}', "{}\n{}"])
async def test_ws_refuses_malformed_rpc_without_child_effects(
    broker: Broker, payload: str | bytes,
) -> None:
    child, host, _, websocket = broker
    async with _connect(websocket) as connection:
        await connection.send(payload)
        with pytest.raises(ConnectionClosed):
            await connection.recv()
    await _released(host)
    assert child.returncode is None and not child.writes


@pytest.mark.asyncio
async def test_ws_child_exit_drains_last_rpc_without_custom_envelope(broker: Broker) -> None:
    child, _, _, websocket = broker
    async with _connect(websocket) as connection:
        # A first inbound message proves attachment completed.
        await connection.send('{"jsonrpc":"2.0","method":"session/cancel","params":{}}')
        while not child.writes:
            await asyncio.sleep(0.005)
        final = '{"jsonrpc":"2.0","id":9,"result":{"stopReason":"end_turn"}}'
        child.stdout.feed_data((final + "\n").encode())
        child.kill()
        assert await connection.recv() == final
        with pytest.raises(ConnectionClosed) as closed:
            await connection.recv()
        assert closed.value.rcvd is not None and closed.value.rcvd.code == 1000


@pytest.mark.asyncio
async def test_ws_readiness_failure_closes_both_listeners_and_owned_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = _Child()
    child.pid = 0
    ports: list[int] = []

    async def spawn(*args: Any, **kwargs: Any) -> _Child:
        return child

    serve_tcp, serve_ws = SessionHost.serve, WebSocketTransport.serve

    async def tcp(self: SessionHost, *args: Any, **kwargs: Any) -> int:
        port = await serve_tcp(self, *args, **kwargs)
        ports.append(port)
        return port

    async def ws(self: WebSocketTransport, *args: Any, **kwargs: Any) -> int:
        port = await serve_ws(self, *args, **kwargs)
        ports.append(port)
        return port

    def failed_publish(*args: Any) -> None:
        raise OSError("fixture readiness publication failed")

    monkeypatch.setattr(launcher, "_spawn_child", spawn)
    monkeypatch.setattr(launcher, "apply_host_survival", lambda: None)
    monkeypatch.setattr(launcher, "_require_receiving_policy", lambda *args: None)
    monkeypatch.setattr(launcher, "_write_host_state", failed_publish)
    monkeypatch.setattr(SessionHost, "serve", tcp)
    monkeypatch.setattr(WebSocketTransport, "serve", ws)
    with pytest.raises(OSError, match="readiness publication failed"):
        await launcher.run_host(["inert-fixture"], state_file=tmp_path / "state.json", ws_port=0)
    assert child.returncode == 0 and len(ports) == 2
    for port in ports:
        with pytest.raises(OSError):
            await asyncio.open_connection("127.0.0.1", port)


@pytest.mark.asyncio
@pytest.mark.parametrize("policy,ws_port", [("acp", None), ("acp", 0), ("legacy", None)])
async def test_standalone_authentication_is_mandatory_except_explicit_legacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, policy: str, ws_port: int | None,
) -> None:
    child = _Child()
    child.pid = 0

    async def spawn(*args: Any, **kwargs: Any) -> _Child:
        return child

    monkeypatch.delenv(launcher._NONCE_ENV, raising=False)
    monkeypatch.setattr(launcher, "_spawn_child", spawn)
    monkeypatch.setattr(launcher, "apply_host_survival", lambda: None)
    monkeypatch.setattr(launcher, "_require_receiving_policy", lambda *args: None)
    ready = asyncio.Event()
    state = tmp_path / "host.json"
    task = asyncio.create_task(launcher.run_host(
        ["inert-fixture"], state_file=state, ready=ready,
        lifetime_policy=policy, ws_port=ws_port,
    ))
    client = None
    try:
        await asyncio.wait_for(ready.wait(), 2)
        facts = json.loads(state.read_text())
        if policy == "acp":
            assert len(facts["nonce"]) == 64
            for token in (b"", b"wrong"):
                rejected = await SessionHostClient.connect(port=facts["port"])
                try:
                    with pytest.raises(ConnectionError):
                        await rejected.attach(nonce=token)
                finally:
                    await rejected.close()
            assert not child.writes and child.returncode is None
            if ws_port is not None:
                assert facts["websocket"]["nonce"] == facts["nonce"]
        else:
            assert facts["nonce"] == ""
        client = await SessionHostClient.connect(port=facts["port"])
        await client.attach(nonce=facts["nonce"].encode())
        await client.terminate()
        assert [frame async for frame in client.frames()] == []
        assert child.returncode == 0
        if policy == "acp":
            done, _ = await asyncio.wait((task,), timeout=2)
            assert done, "Child exit must finish host shutdown before the client disconnects"
            await task
    finally:
        if client is not None:
            await client.close()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("shutdown", ["disconnect", "drain"])
async def test_ws_disconnect_releases_backpressured_controller(
    broker: Broker, monkeypatch: pytest.MonkeyPatch, shutdown: str,
) -> None:
    child, host, tcp, websocket = broker
    client_blocked, child_blocked = asyncio.Event(), asyncio.Event()
    cancelled: set[str] = set()
    original_write = SessionHostClient.write

    async def write(client: SessionHostClient, payload: bytes) -> None:
        await original_write(client, payload)
        client_blocked.set()
        try:
            await asyncio.Future[None]()
        finally:
            cancelled.add("client")

    async def drain() -> None:
        child_blocked.set()
        try:
            await asyncio.Future[None]()
        finally:
            cancelled.add("child")

    monkeypatch.setattr(SessionHostClient, "write", write)
    monkeypatch.setattr(child, "drain", drain)
    async with _connect(websocket) as connection:
        await connection.send('{"jsonrpc":"2.0","method":"session/cancel","params":{}}')
        await asyncio.wait_for(client_blocked.wait(), 2)
        await asyncio.wait_for(child_blocked.wait(), 2)
        if shutdown == "drain":
            await asyncio.wait_for(websocket.close(drain=True), 14)
        else:
            await connection.close()
    await _released(host)
    assert cancelled == {"client", "child"}
    assert child.returncode is None and len(child.writes) == 1
    assert not websocket._handlers
    client = await SessionHostClient.connect(port=tcp)
    try:
        await client.attach(nonce=b"fixture-tcp")
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["attach", "drain"])
async def test_ws_handles_pre311_asyncio_timeout_class(
    broker: Broker, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    child, host, _, websocket = broker

    class LegacyTimeout(Exception):
        pass

    compatibility = SimpleNamespace(**vars(asyncio))
    compatibility.TimeoutError = LegacyTimeout
    if operation == "attach":
        async def attach_timeout(*args: Any, **kwargs: Any) -> None:
            raise LegacyTimeout()

        monkeypatch.setattr(SessionHostClient, "attach", attach_timeout)
        monkeypatch.setattr(ws_transport, "asyncio", compatibility)
        async with _connect(websocket) as connection:
            with pytest.raises(ConnectionClosed) as closed:
                await connection.recv()
            assert closed.value.rcvd is not None and closed.value.rcvd.code == 1013
        await websocket.close()
    else:
        async with _connect(websocket) as connection:
            await connection.send('{"jsonrpc":"2.0","method":"session/cancel","params":{}}')
            while not child.writes:
                await asyncio.sleep(0.005)

            async def wait_timeout(awaitable: Awaitable[Any], seconds: float) -> Any:
                if seconds == 10:
                    raise LegacyTimeout()
                return await asyncio.wait_for(awaitable, seconds)

            compatibility.wait_for = wait_timeout
            monkeypatch.setattr(ws_transport, "asyncio", compatibility)
            await websocket.close(drain=True)
    await _released(host)
    assert child.returncode is None and not websocket._handlers


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_type", [protocol.MsgType.HELLO, protocol.MsgType.FRAME])
async def test_disconnect_during_initial_output_releases_controller_and_retains_replay(
    broker: Broker, monkeypatch: pytest.MonkeyPatch, blocked_type: protocol.MsgType,
) -> None:
    child, host, tcp, _ = broker
    frame = b'{"jsonrpc":"2.0","id":9,"result":{}}\n'
    child.stdout.feed_data(frame)
    while host.max_seq != 1:
        await asyncio.sleep(0.005)
    blocked = asyncio.Event()
    send = protocol.write_message

    async def backpressured(
        writer: asyncio.StreamWriter, kind: protocol.MsgType, payload: bytes = b"",
    ) -> None:
        if kind == blocked_type and not blocked.is_set():
            blocked.set()
            await asyncio.Future[None]()
        await send(writer, kind, payload)

    monkeypatch.setattr(protocol, "write_message", backpressured)
    first = await SessionHostClient.connect(port=tcp)
    try:
        await protocol.write_message(
            first._writer, protocol.MsgType.ATTACH, protocol.pack_attach(0, b"fixture-tcp"),
        )
        await asyncio.wait_for(blocked.wait(), 2)
    finally:
        await first.close()
    await _released(host)
    assert child.returncode is None and host.ack_cursor == 0
    restored = await SessionHostClient.connect(port=tcp)
    try:
        hello = await restored.attach(nonce=b"fixture-tcp")
        assert hello.max_seq == 1
        assert await asyncio.wait_for(anext(restored.frames()), 2) == (1, frame)
        await restored.ack(1)
    finally:
        await restored.close()

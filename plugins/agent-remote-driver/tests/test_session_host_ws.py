"""ACP WS uses ordinary JSON-RPC, sharing the retained TCP Host controller."""

import asyncio
import json
from types import SimpleNamespace

import pytest
import pytest_asyncio
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from agent_remote_driver.session_host.client import SessionHostClient
from agent_remote_driver.session_host.host import SessionHost
from agent_remote_driver.session_host.ws_transport import WebSocketTransport
from agent_remote_driver.session_host import launcher
from agent_remote_driver.session_host import ws_transport
from agent_remote_driver.session_host import protocol


class _Child:
    pid = None
    returncode = None

    def __init__(self):
        self.stdin = self
        self.stdout = asyncio.StreamReader()
        self.writes = []
        self.done = asyncio.Event()

    def write(self, data):
        self.writes.append(data)

    async def drain(self):
        pass

    def kill(self):
        self.returncode = 0
        self.stdout.feed_eof()
        self.done.set()

    async def wait(self):
        await self.done.wait()
        return self.returncode


@pytest_asyncio.fixture
async def broker():
    child = _Child()
    host = SessionHost(child, nonce="fixture-tcp", lifetime_policy="acp")
    tcp = await host.serve()
    websocket = WebSocketTransport(tcp, "fixture-tcp")
    await websocket.serve()
    try:
        yield child, host, tcp, websocket
    finally:
        await websocket.close()
        await host.close()
        child.kill()


def _connect(websocket, **kwargs):
    return connect(
        f"ws://127.0.0.1:{websocket.port}/acp",
        subprotocols=["acp.v1", "bearer." + websocket.nonce],
        compression=None, proxy=None, **kwargs,
    )


async def _released(host):
    for _ in range(100):
        if host._front is None:
            return
        await asyncio.sleep(0.005)
    pytest.fail("controller did not release its broker connection")


@pytest.mark.asyncio
async def test_acp_ws_literal_permission_roundtrip_and_same_child_tcp_handover(broker):
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
        async with _connect(websocket) as competitor:
            with pytest.raises(ConnectionClosed) as refused:
                await competitor.recv()
            assert refused.value.rcvd.code == 1013
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
async def test_ws_refuses_untrusted_handshake_without_controller_effects(broker, kind):
    child, host, _, websocket = broker
    url = f"ws://127.0.0.1:{websocket.port}/acp"
    protocols = ["acp.v1", "bearer." + websocket.nonce]
    headers = {}
    if kind == "nonce":
        protocols[-1] = "bearer.wrong"
    elif kind == "origin":
        headers["Origin"] = "http://untrusted.invalid"
    elif kind == "host":
        headers["Host"] = "untrusted.invalid"
    elif kind == "path":
        url += "/other"
    elif kind == "duplicate-auth":
        protocols += ["bearer.wrong"]
    else:
        protocols.remove("acp.v1")
    with pytest.raises(InvalidStatus):
        async with connect(url, subprotocols=protocols, additional_headers=headers, proxy=None):
            pytest.fail("untrusted handshake accepted")
    assert host._front is None and not child.writes


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [b"{}", "[]", '{"jsonrpc":"1.0"}', "{}\n{}"])
async def test_ws_refuses_malformed_rpc_without_child_effects(broker, payload):
    child, host, _, websocket = broker
    async with _connect(websocket) as connection:
        await connection.send(payload)
        with pytest.raises(ConnectionClosed):
            await connection.recv()
    await _released(host)
    assert child.returncode is None and not child.writes


@pytest.mark.asyncio
async def test_ws_child_exit_drains_last_rpc_without_custom_envelope(broker):
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
        assert closed.value.rcvd.code == 1000


@pytest.mark.asyncio
async def test_ws_readiness_failure_closes_both_listeners_and_owned_child(tmp_path, monkeypatch):
    child = _Child()
    child.pid = 0
    ports = []

    async def spawn(*args, **kwargs):
        return child

    serve_tcp, serve_ws = SessionHost.serve, WebSocketTransport.serve

    async def tcp(self, *args, **kwargs):
        port = await serve_tcp(self, *args, **kwargs)
        ports.append(port)
        return port

    async def ws(self, *args, **kwargs):
        port = await serve_ws(self, *args, **kwargs)
        ports.append(port)
        return port

    def failed_publish(*args):
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
    tmp_path, monkeypatch, policy, ws_port,
):
    child = _Child()
    child.pid = 0

    async def spawn(*args, **kwargs):
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
async def test_ws_disconnect_releases_backpressured_controller(broker, monkeypatch, shutdown):
    child, host, tcp, websocket = broker
    client_blocked, child_blocked = asyncio.Event(), asyncio.Event()
    cancelled = set()
    original_write = SessionHostClient.write

    async def write(client, payload):
        await original_write(client, payload)
        client_blocked.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.add("client")

    async def drain():
        child_blocked.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.add("child")

    monkeypatch.setattr(SessionHostClient, "write", write)
    child.drain = drain
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
async def test_ws_handles_pre311_asyncio_timeout_class(broker, monkeypatch, operation):
    child, host, _, websocket = broker

    class LegacyTimeout(Exception):
        pass

    compatibility = SimpleNamespace(**vars(asyncio))
    compatibility.TimeoutError = LegacyTimeout
    if operation == "attach":
        async def timeout(*args, **kwargs):
            raise LegacyTimeout()

        monkeypatch.setattr(SessionHostClient, "attach", timeout)
        monkeypatch.setattr(ws_transport, "asyncio", compatibility)
        async with _connect(websocket) as connection:
            with pytest.raises(ConnectionClosed) as closed:
                await connection.recv()
            assert closed.value.rcvd.code == 1013
        await websocket.close()
    else:
        async with _connect(websocket) as connection:
            await connection.send('{"jsonrpc":"2.0","method":"session/cancel","params":{}}')
            while not child.writes:
                await asyncio.sleep(0.005)

            async def timeout(awaitable, seconds):
                if seconds == 10:
                    raise LegacyTimeout()
                return await asyncio.wait_for(awaitable, seconds)

            compatibility.wait_for = timeout
            monkeypatch.setattr(ws_transport, "asyncio", compatibility)
            await websocket.close(drain=True)
    await _released(host)
    assert child.returncode is None and not websocket._handlers


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_type", [protocol.MsgType.HELLO, protocol.MsgType.FRAME])
async def test_disconnect_during_initial_output_releases_controller_and_retains_replay(
    broker, monkeypatch, blocked_type,
):
    child, host, tcp, _ = broker
    frame = b'{"jsonrpc":"2.0","id":9,"result":{}}\n'
    child.stdout.feed_data(frame)
    while host.max_seq != 1:
        await asyncio.sleep(0.005)
    blocked = asyncio.Event()
    send = protocol.write_message

    async def backpressured(writer, kind, payload=b""):
        if kind == blocked_type and not blocked.is_set():
            blocked.set()
            await asyncio.Future()
        return await send(writer, kind, payload)

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

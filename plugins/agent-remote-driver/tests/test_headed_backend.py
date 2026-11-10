"""The headed SDK edge uses the same broker and retains its native launch owner."""

import asyncio
import json
import os
import shutil
from contextlib import asynccontextmanager

import pytest
from websockets.asyncio.client import connect

from agent_remote_driver.headed.backend import HeadedBackend
from agent_remote_driver.headed.staging import stage_extension
from agent_remote_driver.session_host.client import SessionHostClient
from agent_remote_driver.session_host.host import SessionHost
from agent_remote_driver.session_host.ws_transport import WebSocketTransport
from agent_procutil.owned_process import run_caller_owned


class Owner:
    pid = None
    returncode = None
    stops = 0

    def __init__(self):
        self.done = asyncio.Event()

    async def wait(self):
        await self.done.wait()
        return self.returncode

    async def terminate(self):
        self.stops += 1
        self.returncode = 0
        self.done.set()


@pytest.mark.asyncio
async def test_retained_node_delivery_client_uses_the_real_common_host_without_native_fallback(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the retained JavaScript consumer")
    owner = Owner()
    owner.pid = os.getpid()
    backend = HeadedBackend(owner, session_id="native-fixture", nonce="fixture-" * 8)
    await backend.serve()
    host = SessionHost(backend, nonce="front-" * 8, lifetime_policy="cli")
    port = await host.serve()
    reader, writer = await _peer(backend, nativePid=owner.pid)
    assets = stage_extension(tmp_path / "retained")
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({
        "version": 2, "mode": "headed", "state": "running", "lifetime_policy": "cli",
        "session_id": "native-fixture", "nonce": "front-" * 8,
        "host_pid": os.getpid(), "child_pid": owner.pid, "cwd": str(tmp_path),
        "protocol_version": 1, "port": port, "headed_client": ["native-send-v1"],
    }), encoding="utf-8")
    requests = []

    async def sdk():
        while raw := await reader.readline():
            request = json.loads(raw)
            requests.append(request)
            if request["method"] == "initialize":
                result = {
                    "protocolVersion": 1, "_meta": {
                        "nativeSessionId": "native-fixture",
                        "nativeSend": {
                            "version": 1, "modes": ["enqueue", "immediate"], "receipt": "native-message-id",
                        },
                    },
                }
            else:
                assert request["method"] == "copilot-driver/native-send"
                result = {"messageId": "actual-native-message"}
            writer.write((json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}) + "\n").encode())
            await writer.drain()

    peer = asyncio.create_task(sdk())
    client = assets / "extensions" / "copilot-driver-headed" / "host-client.mjs"
    script = (
        "const {createHeadedSession}=await import(" + json.dumps(client.as_uri()) + ");\n"
        "const native={sessionId:'native-fixture',send(){throw Error('raw SDK forbidden')}};\n"
        "const client=createHeadedSession(native,{binding:"
        + json.dumps({
            "contract": "copilot-driver.headed-client", "version": 1,
            "sessionId": "native-fixture", "cwd": str(tmp_path), "stateFile": str(state_file),
            "nonce": "front-" * 8, "hostPid": os.getpid(),
        })
        + ",cwd:" + json.dumps(str(tmp_path)) + ",nativePid:" + str(owner.pid) + "});\n"
        "try {console.log(await client.send({prompt:'literal\\npeer message',mode:'immediate'}));}"
        "finally {client.close();}\n"
    )
    try:
        await asyncio.wait_for(backend.bound.wait(), 2)
        result = await asyncio.to_thread(
            run_caller_owned, [node, "--input-type=module", "-e", script],
            timeout=15, max_stdout_bytes=4096, max_stderr_bytes=4096,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == "actual-native-message\n"
        assert not result.stderr
        assert [request["method"] for request in requests] == ["initialize", "copilot-driver/native-send"]
        assert requests[-1]["params"] == {
            "sessionId": "native-fixture", "prompt": "literal\npeer message", "mode": "immediate",
        }
        assert host.child_alive and owner.stops == 0
    finally:
        peer.cancel()
        await asyncio.gather(peer, return_exceptions=True)
        writer.close()
        await writer.wait_closed()
        await host.close()
        owner.returncode = 0
        owner.done.set()
        await backend.close()


def test_launch_assets_are_core_owned_retained_and_never_silently_repaired(tmp_path):
    from importlib.resources import files

    attempt = tmp_path / "original-attempt"
    selected = stage_extension(attempt)
    assert stage_extension(attempt) == selected
    asset = selected / "extensions" / "copilot-driver-headed" / "extension.mjs"
    assert asset.read_bytes() == files("agent_remote_driver.headed").joinpath("extension.mjs").read_bytes()
    asset.write_text("changed retained source", encoding="utf-8")
    with pytest.raises(ValueError, match="assets changed"):
        stage_extension(attempt)
    assert asset.read_text() == "changed retained source"


async def _peer(backend, **overrides):
    reader, writer = await asyncio.open_connection("127.0.0.1", backend.declaration()["port"])
    writer.write((json.dumps({
        "contract": "copilot-driver.headed-sdk", "version": 1,
        "sessionId": "native-fixture", "nonce": "fixture-" * 8, "extensionPid": 123,
        "nativePid": 122,
        **overrides,
    }) + "\n").encode())
    await writer.drain()
    return reader, writer


@asynccontextmanager
async def _auth_peer():
    from agent_remote_driver.headed.auth_wait import AuthWaitReceiver
    from test_headed_auth_wait import configuration

    owner = Owner()
    receiver = AuthWaitReceiver("native-fixture", configuration())
    receiver.phase = "running"
    await receiver.serve()
    backend = HeadedBackend(
        owner, session_id="native-fixture", nonce="fixture-" * 8, auth_wait=receiver,
    )
    await backend.serve()
    reader, writer = await _peer(backend)
    try:
        await asyncio.wait_for(backend.bound.wait(), 2)
        yield owner, receiver, backend, reader, writer
    finally:
        writer.close()
        await writer.wait_closed()
        await backend.close()
        await receiver.close()


async def _freeze_idle(receiver, backend, reader, writer):
    from test_headed_auth_wait import IDLE, request

    assert (await request(receiver, "revoke"))["ok"]
    await receiver.expiry()
    freezing = asyncio.create_task(backend.freeze_auth())
    try:
        assert json.loads(await reader.readline())["contract"] == "copilot-driver.headed-auth-freeze"
        writer.write((json.dumps({
            "contract": "copilot-driver.headed-auth-state", "version": 1,
            "incarnation": receiver.incarnation, "state": IDLE,
        }) + "\n").encode())
        await writer.drain()
    finally:
        await freezing
    assert backend.auth_snapshot == IDLE


@pytest.mark.asyncio
async def test_idle_freeze_then_sdk_loss_before_owned_retirement_cannot_resume():
    import time
    from test_headed_auth_wait import request

    async with _auth_peer() as (owner, receiver, backend, reader, writer):
        await _freeze_idle(receiver, backend, reader, writer)
        writer.close()
        await writer.wait_closed()
        await asyncio.wait_for(backend.lost.wait(), 1)
        assert owner.returncode is None and owner.stops == 0
        await owner.terminate()
        await backend.finish_auth_stop()
        assert receiver.phase == "held" and receiver.hold == "native-connection-lost"
        assert not (await request(
            receiver, "renew", lease_revision=2, budget_seconds=5, expires_at=time.time() + 5,
        ))["ok"]


@pytest.mark.asyncio
@pytest.mark.parametrize("record,forward_failure", [
    (b'{"jsonrpc":', False),
    (b'{"jsonrpc":"2.0"}', False),
    (b'{"jsonrpc":"2.0","id":8,"result":{}}\n', True),
])
async def test_abnormal_sdk_drain_cannot_reuse_an_old_idle_snapshot(record, forward_failure):
    import time
    from test_headed_auth_wait import request

    async with _auth_peer() as (owner, receiver, backend, reader, writer):
        await _freeze_idle(receiver, backend, reader, writer)
        backend.auth_retiring = True
        await owner.terminate()
        if forward_failure:
            backend.stdout.feed_eof()
        writer.write(record)
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        await backend.finish_auth_stop()
        assert receiver.phase == "held" and receiver.hold == "native-observation-incomplete"
        assert not (await request(
            receiver, "renew", lease_revision=2, budget_seconds=5, expires_at=time.time() + 5,
        ))["ok"]


@pytest.mark.asyncio
async def test_owned_exit_drains_queued_sdk_reply_before_broker_eof():
    async with _auth_peer() as (owner, receiver, backend, reader, writer):
        await _freeze_idle(receiver, backend, reader, writer)
        backend.auth_retiring = True
        await owner.terminate()
        await asyncio.sleep(.01)
        assert not backend.stdout.at_eof()
        reply = b'{"jsonrpc":"2.0","id":9,"result":{"late":true}}\n'
        writer.write(reply)
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        await backend.finish_auth_stop()
        assert await asyncio.wait_for(backend.stdout.read(), 1) == reply
        assert receiver.phase == "auth_wait" and receiver.hold is None


@pytest.mark.asyncio
@pytest.mark.parametrize("retiring", [False, True])
async def test_only_owned_retirement_reset_drains_buffered_native_records(retiring):
    from agent_remote_driver.headed.backend import _AuthStopReader

    reader = _AuthStopReader(lambda: retiring)
    reader.feed_data(b"complete\nincomplete")
    reader.set_exception(ConnectionResetError())
    if not retiring:
        with pytest.raises(ConnectionResetError):
            await reader.readline()
    else:
        assert await reader.readline() == b"complete\n"
        assert await reader.readline() == b"incomplete"
        assert await reader.readline() == b""


@pytest.mark.asyncio
@pytest.mark.parametrize("matching_identity", [True, False])
async def test_native_observation_fault_only_closes_its_bound_sdk_stream(tmp_path, matching_identity):
    import time

    from native.test_native_headed_auth_wait import _observation_loss_backend
    from agent_remote_driver.headed.auth_wait import AuthWaitReceiver

    receiver = AuthWaitReceiver("native-fixture", {
        "source_owner": "fixture", "source_revision": "fixture-revision",
        "lease_revision": 1, "budget_seconds": 30, "expires_at": time.time() + 30,
    })
    receiver.phase = "running"
    fault_file, receipt_file = tmp_path / "fault.json", tmp_path / "receipt.json"
    backend = _observation_loss_backend(fault_file, receipt_file)(
        Owner(), session_id="native-fixture", nonce="fixture-" * 8, auth_wait=receiver,
    )
    await backend.serve()
    reader, writer = await _peer(backend)
    snapshot = {
        "controlled": True, "closed": False, "pending": True,
        "sending": False, "outstanding": False, "waiting": False,
    }
    try:
        await asyncio.wait_for(backend.bound.wait(), 2)
        writer.write((json.dumps({
            "contract": "copilot-driver.headed-auth-state", "version": 1,
            "incarnation": receiver.incarnation, "state": snapshot,
        }) + "\n").encode())
        await writer.drain()
        for _ in range(100):
            if backend.auth_snapshot == snapshot:
                break
            await asyncio.sleep(.01)
        assert backend.auth_snapshot == snapshot
        fault_file.write_text(json.dumps({
            "session_id": "native-fixture", "incarnation": receiver.incarnation,
            "native_pid": 122 if matching_identity else 999, "extension_pid": 123,
        }), encoding="utf-8")
        for _ in range(100):
            if receipt_file.exists():
                break
            await asyncio.sleep(.01)
        receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
        if matching_identity:
            assert receipt["snapshot"] == snapshot
            assert receipt["status"]["hold"] == "native-connection-lost"
            assert receipt["writer_closed"] and not receipt["bound"]
            assert await asyncio.wait_for(reader.read(), 1) == b""
        else:
            assert receipt == {"error": "AssertionError"}
            assert backend.bound.is_set() and not backend.lost.is_set()
            assert receiver.hold is None
    finally:
        writer.close()
        await writer.wait_closed()
        await backend.close()
        await receiver.close()


@pytest.mark.asyncio
async def test_cli_backend_shares_ws_tcp_control_and_only_exact_owner_can_stop():
    owner = Owner()
    backend = HeadedBackend(owner, session_id="native-fixture", nonce="fixture-" * 8)
    await backend.serve()
    host = SessionHost(
        backend, nonce="front-private", lifetime_policy="cli",
        unexpected_reap_seconds=0.01, active_reap_seconds=0.01,
    )
    port = await host.serve()
    websocket = WebSocketTransport(port, "front-private")
    await websocket.serve()
    peer_reader, peer_writer = await _peer(backend)
    client = None
    try:
        await asyncio.wait_for(backend.bound.wait(), 2)
        async with connect(
            f"ws://127.0.0.1:{websocket.port}/acp", proxy=None,
            subprotocols=["acp.v1", "bearer.front-private"],
        ) as connection:
            request = {
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": 1},
            }
            await connection.send(json.dumps(request))
            assert json.loads(await asyncio.wait_for(peer_reader.readline(), 2)) == request
            response = {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": 1}}
            peer_writer.write((json.dumps(response) + "\n").encode())
            await peer_writer.drain()
            assert json.loads(await asyncio.wait_for(connection.recv(), 2)) == response
        for _ in range(100):
            if host._front is None:
                break
            await asyncio.sleep(0.005)
        client = await SessionHostClient.connect(port=port)
        await client.attach(nonce=b"front-private")
        await client.detach(reapable=True)
        await client.close()
        client = None
        await asyncio.sleep(0.03)
        assert host.child_alive and owner.stops == 0 and backend.bound.is_set()
        client = await SessionHostClient.connect(port=port)
        await client.attach(nonce=b"front-private")
        await client.terminate()
        await asyncio.wait_for(host.wait_child(), 2)
        assert owner.stops == 1 and not host.child_alive
    finally:
        if client is not None:
            await client.close()
        await websocket.close()
        await host.close()
        peer_writer.close()
        await peer_writer.wait_closed()
        await backend.close()


@pytest.mark.asyncio
async def test_wrong_binding_refuses_and_peer_loss_never_rehooks_or_retires_native():
    owner = Owner()
    backend = HeadedBackend(owner, session_id="native-fixture", nonce="fixture-" * 8)
    await backend.serve()
    peers = []
    try:
        for wrong in ({"nonce": "wrong"}, {"sessionId": "other-native"}, {"extensionPid": True}):
            reader, writer = await _peer(backend, **wrong)
            peers.append(writer)
            assert await asyncio.wait_for(reader.read(), 2) == b""
            assert not backend.bound.is_set()
        _reader, writer = await _peer(backend)
        peers.append(writer)
        await asyncio.wait_for(backend.bound.wait(), 2)
        writer.close()
        await writer.wait_closed()
        for _ in range(100):
            if not backend.bound.is_set():
                break
            await asyncio.sleep(0.005)
        with pytest.raises(ConnectionError, match="unavailable"):
            backend.write(b"must-not-be-replayed\n")
        reader, writer = await _peer(backend)
        peers.append(writer)
        assert await asyncio.wait_for(reader.read(), 2) == b""
        assert owner.returncode is None and owner.stops == 0 and not backend.bound.is_set()
        await backend.close()
        assert owner.returncode is None and owner.stops == 0
    finally:
        for writer in peers:
            writer.close()
            await writer.wait_closed()
        await backend.close()

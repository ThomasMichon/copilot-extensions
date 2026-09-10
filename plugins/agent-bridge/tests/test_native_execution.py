"""Native identity, restart, representation and retirement contracts."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from agent_bridge.native_manager import NativeManager
from agent_bridge.native_store import NativeError, NativeStore, receipt
from agent_bridge.session_host.client import SessionHostClient
from agent_bridge.session_host.host import SessionHost
from agent_bridge.session_host import protocol


def request(tmp_path):
    return {
        "requestId": "request-one", "codespace": "example-space", "owner": str(tmp_path),
        "cwd": "/workspaces/example-web", "command": "exec copilot",
        "localForward": ["4321:4321"], "reverseForward": ["9000:9001"],
        "noPluginStaging": True, "requireRelay": True,
    }


class Backend:
    launches = 0
    represented = True
    stopped = False

    def __init__(self, prefix, row, on_reserved, on_progress=None):
        self.row, self.on_reserved = row, on_reserved
        self.closed = False

    async def start(self, resume=False):
        self.on_reserved()

    async def request(self, method, params=None):
        if method == "launch":
            type(self).launches += 1
        if method == "stop":
            type(self).stopped = True
            return {"retired": True, "state": "stopped", "exitCode": 7, "recovery": {"ok": True}}
        if method == "message":
            return {"delivered": True}
        return {
            "state": "ready" if self.represented else "unrepresented",
            "sessionId": "real-native-session", "represented": self.represented,
            "host": {"nonce": "private-host-nonce", "child_pid": 1234, "port": 10000},
            "localPort": 10001, "activated": True, "retired": False,
        }

    async def close(self):
        self.closed = True


@pytest.fixture
def factory(tmp_path, monkeypatch):
    Backend.launches, Backend.represented, Backend.stopped = 0, True, False

    def make():
        value = NativeManager(tmp_path / "controller", lambda: ["provider"], transport_factory=Backend)
        monkeypatch.setattr(value, "_prove_endpoint", AsyncMock())
        return value

    return make


async def prepared(manager, value):
    result = await manager.start(value)
    await asyncio.gather(*list(manager.tasks.values()))
    return await manager.status(result["executionId"], result["generation"])


@pytest.mark.asyncio
async def test_start_retry_restart_and_registration_loss_never_spawn_another_native(tmp_path, factory):
    first = factory()
    initial = await prepared(first, request(tmp_path))
    assert initial["ready"] and initial["sessionId"] == "real-native-session"
    assert "nonce" not in json.dumps(initial)
    again = await prepared(first, request(tmp_path))
    assert again["executionId"] == initial["executionId"]
    assert Backend.launches == 1
    Backend.represented = False
    lost = await first.status(initial["executionId"])
    assert not lost["ready"]
    with pytest.raises(NativeError, match="ACP fallback"):
        await first.represented(initial["executionId"], initial["generation"], "message", {
            "expectedSessionId": "real-native-session", "messageId": "message-one", "body": "hello",
        })
    with pytest.raises(NativeError, match="blocks ACP"):
        first.assert_acp_allowed("example-space")
    await first.shutdown()
    second = factory()
    recovering = await second.status(initial["executionId"], initial["generation"])
    assert not recovering["ready"]
    await asyncio.gather(*list(second.tasks.values()))
    Backend.represented = True
    restored = await second.status(initial["executionId"])
    assert restored["ready"] and restored["sessionId"] == initial["sessionId"]
    assert Backend.launches == 1
    stopped = await second.stop(initial["executionId"], initial["generation"])
    assert stopped["state"] == "stopped" and stopped["exitCode"] == 7
    second.assert_acp_allowed("example-space")
    await second.shutdown()


@pytest.mark.asyncio
async def test_generation_mismatch_and_same_owner_second_request_are_rejected(tmp_path, factory):
    manager = factory()
    initial = await prepared(manager, request(tmp_path))
    with pytest.raises(NativeError):
        await manager.stop(initial["executionId"], "wrong-generation")
    assert not Backend.stopped
    with pytest.raises(NativeError, match="already owns"):
        await manager.start({**request(tmp_path), "requestId": "second-request"})
    assert Backend.launches == 1
    await manager.shutdown()


@pytest.mark.asyncio
async def test_existing_acp_incumbent_prevents_native_admission(tmp_path):
    manager = NativeManager(tmp_path / "controller", lambda: ["provider"], acp_busy=lambda _: True)
    with pytest.raises(NativeError, match="ACP execution"):
        await manager.start(request(tmp_path))
    assert manager.store() is None


def test_corrupt_or_missing_controller_authority_never_becomes_empty(tmp_path):
    root = tmp_path / "controller"
    store = NativeStore(root)
    row, created = store.reserve("execution", "generation", "request", "example-space", "owner", "hash", {})
    assert created and receipt(row)["mode"] == "native"
    store.path.unlink()
    with pytest.raises(NativeError, match="refusing"):
        NativeStore(root)


class Child:
    def __init__(self):
        self.stdout = asyncio.StreamReader()
        self.pid = 4321
        self.returncode = None
        self.done = asyncio.Event()
        self.stdin = self
        self.writes = []
        self.sizes = []

    def write(self, data):
        self.writes.append(data)

    async def drain(self):
        pass

    async def wait(self):
        await self.done.wait()
        return self.returncode

    def kill(self):
        self.returncode = 7
        self.stdout.feed_eof()
        self.done.set()

    def resize(self, *size):
        self.sizes.append(size)

    def start(self):
        pass


@pytest.mark.asyncio
async def test_native_partial_output_replay_probe_and_retirement_do_not_use_acp():
    child = Child()
    host = SessionHost(child, nonce="secret", terminal=True, unexpected_reap_seconds=.01)
    port = await host.serve()
    first = await SessionHostClient.connect(port=port)
    try:
        await first.attach(nonce=b"secret")
        child.stdout.feed_data(b"prompt without newline> ")
        seq, data = await asyncio.wait_for(anext(first.frames()), 1)
        assert data == b"prompt without newline> "
        await first.ack(seq)
        observer = await SessionHostClient.connect(port=port)
        await observer.probe(nonce=b"secret")
        await observer.close()
        wrong = await SessionHostClient.connect(port=port)
        with pytest.raises(ConnectionError):
            await wrong.probe(nonce=b"wrong")
        await wrong.close()
        await first.write(b"still attached\n")
        await asyncio.sleep(.01)
        assert child.writes == [b"still attached\n"]
        await first.close()
        await asyncio.sleep(.03)
        assert child.returncode is None
        second = await SessionHostClient.connect(port=port)
        await second.attach(nonce=b"secret")
        assert (await asyncio.wait_for(anext(second.frames()), 1))[1] == data
        await second.resize(40, 100)
        await asyncio.sleep(.01)
        assert child.sizes == [(40, 100)]
        retire = await SessionHostClient.connect(port=port)
        assert await asyncio.wait_for(retire.retire(child_pid=child.pid, nonce=b"secret"), 2) == 7
        await retire.close()
        await second.close()
    finally:
        await first.close()
        if child.returncode is None:
            child.kill()
        await host.close()


def test_native_resize_preserves_existing_unknown_protocol_fixture():
    assert protocol.MsgType.RESIZE.value != b"Z"
    assert protocol.unpack_resize(protocol.pack_resize(24, 80)) == (24, 80)
    with pytest.raises(protocol.ProtocolError):
        protocol.pack_resize(0, 80)

"""Resident admission outlives cancelled cleanup of a real warm upstream."""

import asyncio
import json
import sys

import pytest

from agent_mcp.client import OneShotSession
from agent_mcp.config import parse_config
from agent_mcp import serve
from agent_mcp.ipc import _connect
from agent_mcp.serve import Server, WarmPool
from agent_procutil.daemon_instances import daemon_test_lifecycle, inspect_instances

CHILD = """
import json, sys
for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    result = {}
    if message.get("method") == "initialize":
        result = {"protocolVersion": "2025-06-18", "capabilities": {},
                  "serverInfo": {"name": "retirement-test"}}
    elif message.get("method") == "tools/call":
        result = {"content": [{"type": "text", "text": "ok"}]}
    print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
"""


async def until(predicate):
    async def wait():
        while not predicate():
            await asyncio.sleep(0.001)
    await asyncio.wait_for(wait(), 5)


@pytest.mark.parametrize("ending", ["cancel", "stop", "startup-error"])
@pytest.mark.parametrize("repeated", [False, True])
async def test_cancelled_cleanup_keeps_admission_and_live_upstream(
    tmp_path, monkeypatch, ending, repeated,
):
    pool = WarmPool()
    config = parse_config({
        "server": {"type": "stdio", "command": [sys.executable, "-c", CHILD]},
        "auth": {"kind": "none"},
    })
    await pool.call("warm", config, "echo", {})
    child = pool._entries["warm"].session._transport._proc
    entered, release = asyncio.Event(), asyncio.Event()
    original_exit = OneShotSession.__aexit__

    async def blocked_exit(session, *args):
        entered.set()
        await release.wait()
        await original_exit(session, *args)

    monkeypatch.setattr(OneShotSession, "__aexit__", blocked_exit)
    server = Server(tmp_path / "runtime" / "serve.sock", pool=pool)

    async def control():
        if ending == "startup-error":
            raise RuntimeError("control startup refused")

    monkeypatch.setattr(server, "_start_control_listener", control)
    registry = tmp_path / "instances"
    with daemon_test_lifecycle(owner="mcp-retirement", registry_root=registry) as environment:
        for name, value in environment.items():
            monkeypatch.setenv(name, value)
        task = asyncio.create_task(server.serve_forever())
        try:
            if ending != "startup-error":
                await until(lambda: server._server is not None)
                if ending == "cancel":
                    task.cancel()
                else:
                    server._stop.set()
            await asyncio.wait_for(entered.wait(), 5)
            assert child.returncode is None and pool.size == 1
            assert inspect_instances(registry)[0]["state"] == "running"
            if repeated:
                for _ in range(3):
                    task.cancel()
                    await asyncio.sleep(0.01)
                    assert not task.done()
                    assert inspect_instances(registry)[0]["state"] == "running"
                    assert child.returncode is None and pool.size == 1
            release.set()
            if repeated or ending == "cancel":
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(asyncio.shield(task), 5)
            elif ending == "startup-error":
                with pytest.raises(RuntimeError, match="control startup refused"):
                    await asyncio.wait_for(asyncio.shield(task), 5)
            else:
                await asyncio.wait_for(asyncio.shield(task), 5)
            assert child.returncode is not None and pool.size == 0
            assert inspect_instances(registry)[0]["state"] == "finished"
            assert server._lease is None
        finally:
            release.set()
            server._stop.set()
            await asyncio.gather(task, return_exceptions=True)
            await pool.close_all()


async def test_attached_native_session_retires_before_host_admission(tmp_path, monkeypatch):
    config = parse_config({
        "server": {"type": "stdio", "command": [sys.executable, "-c", CHILD]},
        "auth": {"kind": "none"},
    })
    entered, release = asyncio.Event(), asyncio.Event()
    sessions = []

    class RetainedSession:
        decorator_count = 0

        def __init__(self, cfg, sink):
            self.upstream = OneShotSession(cfg)
            sessions.append(self)

        async def start(self):
            await self.upstream.__aenter__()

        async def aclose(self):
            entered.set()
            await release.wait()
            await self.upstream.__aexit__(None, None, None)

    monkeypatch.setattr(serve, "BridgeSession", RetainedSession)
    monkeypatch.setattr(serve, "load_config", lambda _: config)
    server = Server(tmp_path / "runtime" / "serve.sock")
    registry = tmp_path / "instances"
    writer = None
    with daemon_test_lifecycle(owner="mcp-attached-retirement", registry_root=registry) as environment:
        for name, value in environment.items():
            monkeypatch.setenv(name, value)
        task = asyncio.create_task(server.serve_forever())
        try:
            await until(lambda: server._control_server is not None)
            reader, writer, token = await _connect(server.socket_path)
            writer.write((json.dumps({"op": "attach", "bridge": "test", "token": token}) + "\n").encode())
            await writer.drain()
            assert json.loads(await asyncio.wait_for(reader.readline(), 5))["attached"]
            child = sessions[0].upstream._transport._proc
            server._stop.set()
            await asyncio.sleep(0.01)
            assert not task.done() and server.attached == 1 and child.returncode is None
            writer.close()
            await writer.wait_closed()
            writer = None
            await asyncio.wait_for(entered.wait(), 5)
            for _ in range(3):
                task.cancel()
                await asyncio.sleep(0.01)
                assert not task.done() and child.returncode is None
                assert server.attached == 1 and server._connections
                assert inspect_instances(registry)[0]["state"] == "running"
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(task), 5)
            assert child.returncode is not None
            assert server.attached == 0 and not server._connections
            assert inspect_instances(registry)[0]["state"] == "finished"
        finally:
            release.set()
            server._stop.set()
            if writer is not None:
                writer.close()
                await writer.wait_closed()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("channel", ["data", "control"])
async def test_idle_accepted_connection_closes_before_admission(tmp_path, monkeypatch, channel):
    server = Server(tmp_path / "runtime" / "serve.sock")
    registry = tmp_path / "instances"
    writer = None
    with daemon_test_lifecycle(owner="mcp-idle-retirement", registry_root=registry) as environment:
        for name, value in environment.items():
            monkeypatch.setenv(name, value)
        task = asyncio.create_task(server.serve_forever())
        try:
            await until(lambda: server._control_server is not None)
            if channel == "data":
                reader, writer, _ = await _connect(server.socket_path)
            else:
                port = server._control_server.sockets[0].getsockname()[1]
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
            await until(lambda: bool(server._connections))
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(task), 5)
            assert await asyncio.wait_for(reader.read(), 5) == b""
            assert not server._connections
            assert inspect_instances(registry)[0]["state"] == "finished"
        finally:
            server._stop.set()
            if writer is not None:
                writer.close()
                await writer.wait_closed()
            await asyncio.gather(task, return_exceptions=True)

"""The existing stdio/ACP-WS relay consumes the standalone Driver endpoint."""

import asyncio
import json
import os
import sys

from agent_procutil import direct_python, no_window_flags
from agent_remote_driver.session_host.host import SessionHost
from agent_remote_driver.session_host.ws_transport import WebSocketTransport
import pytest


@pytest.mark.asyncio
async def test_existing_acp_connect_relay_drives_core_without_a_bridge_agent():
    class Child:
        pid = None
        returncode = None

        def __init__(self):
            self.stdin = self
            self.stdout = asyncio.StreamReader()
            self.writes = []
            self.done = asyncio.Event()

        def write(self, data):
            self.writes.append(data)
            request = json.loads(data)
            self.stdout.feed_data((json.dumps({
                "jsonrpc": "2.0", "id": request["id"],
                "result": {"protocolVersion": 1},
            }) + "\n").encode())

        async def drain(self):
            pass

        async def wait(self):
            await self.done.wait()
            return self.returncode

        def kill(self):
            self.returncode = 0
            self.stdout.feed_eof()
            self.done.set()

    child = Child()
    host = SessionHost(child, nonce="fixture-internal", lifetime_policy="acp")
    tcp = await host.serve()
    websocket = WebSocketTransport(tcp, "fixture-internal")
    await websocket.serve()
    interpreter, interpreter_env = direct_python(sys.executable)
    program = (
        "import asyncio,json,sys\n"
        "from agent_bridge.acp_connect import _relay\n"
        "url,token=json.loads(sys.stdin.buffer.readline())\n"
        "raise SystemExit(asyncio.run(_relay(url,token)))\n"
    )
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            interpreter, "-I", "-c", program,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, **interpreter_env}, creationflags=no_window_flags(),
        )
        request = {"jsonrpc": "2.0", "id": 41, "method": "initialize",
                   "params": {"protocolVersion": 1}}
        process.stdin.write((json.dumps([
            f"ws://127.0.0.1:{websocket.port}/acp", websocket.nonce,
        ]) + "\n" + json.dumps(request) + "\n").encode())
        await process.stdin.drain()
        frame = await asyncio.wait_for(process.stdout.readline(), 10)
        assert json.loads(frame) == {
            "jsonrpc": "2.0", "id": 41, "result": {"protocolVersion": 1},
        }
        process.stdin.close()
        await asyncio.wait_for(process.wait(), 5)
        assert process.returncode == 0
        assert len(child.writes) == 1 and json.loads(child.writes[0]) == request
        assert child.returncode is None
    finally:
        if process is not None:
            if process.returncode is None:
                process.kill()
            await asyncio.wait_for(process.communicate(), 5)
        await websocket.close()
        await host.close()
        child.kill()

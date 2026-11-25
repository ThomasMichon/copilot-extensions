"""Actual Bridge pickup within the existing Driver native ACP packet."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
import importlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Literal, Protocol, cast

import pytest

from acp.exceptions import RequestError
from agent_procutil import no_window_kwargs
from agent_bridge.acp_client import AcpClient
from agent_bridge.session_host.acp_adapter import open_acp_streams
from agent_remote_driver.session_host.client import SessionHostClient
from agent_remote_driver.session_host.host import SessionHost
from agent_remote_driver.service import DriverService
from agent_remote_driver.session_host.registered_inventory import HostEndpoint


class _NativeFixture(Protocol):
    async def run_native_copilot_roundtrip(
        self, tmp_path: Path, distribution: Literal["managed-environment", "copied-core"],
        pickup: Callable[[SessionHostClient, int, str, str, int], Awaitable[None]],
    ) -> None: ...


async def _bridge_pickup(
    wire: SessionHostClient, cursor: int, native: str, prompt: str, child_pid: int,
    *, refused: bool = False, observation: dict[str, Any] | None = None,
) -> None:
    streams = await open_acp_streams(wire, start_from=cursor)
    events: list[dict[str, Any]] = []

    def observe(kind: str, data: dict[str, Any]) -> None:
        assert len(events) < 256, "Bridge fixture event capacity exceeded"
        events.append({"type": kind, "data": data})

    bridge = AcpClient(on_event=observe)

    async def close_frontend() -> None:
        await streams.aclose()
        await wire.close()

    try:
        await asyncio.wait_for(bridge.start_streams(
            streams.reader, streams.writer, child_pid=child_pid, closer=close_frontend,
        ), 30)
        bridge.adopt_session(native)
        try:
            result = await asyncio.wait_for(bridge.send_prompt(prompt), 30)
        except RequestError as error:
            assert refused and error.code == -32001, error
            result = {"refused": True, "code": error.code}
        else:
            assert not refused, "Bridge unexpectedly accepted input after native control changed"
            assert result["stop_reason"] == "end_turn"
        if observation is not None:
            observation.update(
                session_id=native, child_pid=child_pid, result=result, events=events,
            )
    finally:
        try:
            await asyncio.wait_for(bridge.shutdown(), 5)
        finally:
            await close_frontend()


async def _bridge_existing_host(
    state: dict[str, Any], native: str, prompt: str, *, refused: bool = False,
) -> dict[str, Any]:
    assert state["session_id"] == native
    observation: dict[str, Any] = {}
    wire = await SessionHostClient.connect(port=state["port"])
    try:
        hello = await wire.attach(nonce=state["nonce"].encode())
        assert hello.child_pid == state["child_pid"]
        await _bridge_pickup(
            wire, 0, native, prompt, hello.child_pid,
            refused=refused, observation=observation,
        )
    finally:
        await wire.close()
    return observation


@pytest.mark.asyncio
@pytest.mark.timeout(180)
async def test_native_bridge_picks_up_copied_core(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # This composes the existing Driver packet, not an independent native budget.
    root = Path(__file__).resolve().parents[4]
    monkeypatch.syspath_prepend(str(root / "plugins/agent-remote-driver/tests/native"))
    fixture = cast(_NativeFixture, importlib.import_module("test_session_host_copilot"))
    await fixture.run_native_copilot_roundtrip(tmp_path, "copied-core", pickup=_bridge_pickup)


async def _bridge_service_host(
    port: int, native_pid: int,
) -> dict[str, Any]:
    capability = "fixture-service-capability-" + "x" * 32
    service = DriverService(
        capability, inventory=lambda: (
            [HostEndpoint("original", "retained-native", native_pid, port, "fixture-key")],
            {"availability": "available", "reason": None},
        ),
    )
    await service.serve()
    relay = None
    events = []
    bridge = AcpClient(on_event=lambda kind, data: events.append({"type": kind, "data": data}))
    try:
        relay = await asyncio.create_subprocess_exec(
            sys.executable, "-B", "-c",
            "import asyncio,os,sys\n"
            "from agent_bridge.acp_connect import _relay\n"
            "sys.exit(asyncio.run(_relay(sys.argv[1], os.environ['DRIVER_FIXTURE_CAPABILITY'])))",
            f"ws://127.0.0.1:{service.port}/sessions/original/acp",
            env=dict(os.environ, DRIVER_FIXTURE_CAPABILITY=capability),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, **no_window_kwargs(),
        )
        await asyncio.wait_for(bridge.start(relay), 10)
        bridge.adopt_session("retained-native")
        result = await asyncio.wait_for(bridge.send_prompt("one real consumer input"), 10)
        return {
            "session_id": "retained-native", "child_pid": native_pid,
            "result": result, "events": events,
        }
    finally:
        try:
            await asyncio.wait_for(bridge.shutdown(), 5)
        finally:
            try:
                if relay is not None:
                    if relay.returncode is None:
                        relay.terminate()
                    await asyncio.wait_for(relay.wait(), 5)
                    assert relay.stderr is not None
                    assert await relay.stderr.read() == b""
            finally:
                await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["tcp", "service"])
async def test_bridge_pickup_crosses_real_core_socket_and_owned_child(
    tmp_path: Path, transport: str,
) -> None:
    transcript = tmp_path / "native-requests.jsonl"
    script = (
        "import json,sys\n"
        "for line in sys.stdin:\n"
        " request=json.loads(line)\n"
        " with open(sys.argv[1],'a',encoding='utf-8') as out:out.write(json.dumps(request)+'\\n')\n"
        " if request['method']=='initialize':\n"
        "  result={'protocolVersion':1,'agentCapabilities':{}}\n"
        " else:\n"
        "  assert request['method']=='session/prompt'\n"
        "  assert request['params']['sessionId']=='retained-native'\n"
        "  print(json.dumps({'jsonrpc':'2.0','method':'session/update','params':{\n"
        "   'sessionId':'retained-native','update':{'sessionUpdate':'agent_message_chunk',\n"
        "   'content':{'type':'text','text':'real child response'}}}}),flush=True)\n"
        "  result={'stopReason':'end_turn'}\n"
        " print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':result}),flush=True)\n"
    )
    child = await asyncio.create_subprocess_exec(
        sys.executable, "-I", "-B", "-c", script, str(transcript),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, **no_window_kwargs(),
    )
    host = SessionHost(child, nonce="fixture-key", lifetime_policy="acp")
    try:
        port = await host.serve()
        if transport == "service":
            observed = await asyncio.wait_for(_bridge_service_host(port, child.pid), 30)
        else:
            observed = await asyncio.wait_for(_bridge_existing_host({
                "session_id": "retained-native", "child_pid": child.pid,
                "port": port, "nonce": "fixture-key",
            }, "retained-native", "one real consumer input"), 15)
        requests = [json.loads(line) for line in transcript.read_text(encoding="utf-8").splitlines()]
        assert [request["method"] for request in requests] == ["initialize", "session/prompt"]
        assert requests[1]["params"]["prompt"] == [{
            "type": "text", "text": "one real consumer input",
        }]
        assert observed["child_pid"] == child.pid and child.returncode is None
        assert observed["result"]["stop_reason"] == "end_turn"
        assert {"type": "agent_message", "data": {"text": "real child response"}} in observed["events"]
    finally:
        await host.close()
        if child.returncode is None:
            child.terminate()
        await asyncio.wait_for(child.wait(), 5)
        assert child.stderr is not None
        assert await child.stderr.read() == b""


@pytest.mark.asyncio
@pytest.mark.parametrize("reject_prompt,expected_refusal", [(False, False), (True, False), (True, True)])
async def test_bridge_pickup_uses_only_initialize_and_existing_prompt(
    reject_prompt: bool, expected_refusal: bool,
) -> None:
    class Wire:
        child_alive = True
        child_exit_code = 0

        def __init__(self) -> None:
            self.messages: list[dict[str, Any]] = []
            self.pending: asyncio.Queue[tuple[int, bytes]] = asyncio.Queue()
            self.closed = False

        async def write(self, data: bytes) -> None:
            message = json.loads(data)
            self.messages.append(message)
            if message["method"] == "initialize":
                result = {"protocolVersion": 1, "agentCapabilities": {}}
            else:
                assert message["method"] == "session/prompt"
                assert message["params"] == {
                    "sessionId": "retained-native",
                    "prompt": [{"type": "text", "text": 'steer "quote" & $literal \u03a9'}],
                }
                result = {"stopReason": "end_turn"}
            reply = {"jsonrpc": "2.0", "id": message["id"], "result": result}
            if reject_prompt and message["method"] == "session/prompt":
                reply.pop("result")
                reply["error"] = {
                    "code": -32001 if expected_refusal else -32603, "message": "fixture refusal",
                }
            if not reject_prompt and message["method"] == "session/prompt":
                await self.pending.put((len(self.messages), (json.dumps({
                    "jsonrpc": "2.0", "method": "session/update", "params": {
                        "sessionId": "retained-native", "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": "Bridge observed this response"},
                        },
                    },
                }) + "\n").encode()))
            await self.pending.put((len(self.messages), (json.dumps(reply) + "\n").encode()))

        async def frames(self) -> AsyncIterator[tuple[int, bytes]]:
            while True:
                yield await self.pending.get()

        async def ack(self, sequence: int) -> None:
            assert 0 < sequence <= len(self.messages)

        async def close(self) -> None:
            self.closed = True

    wire = Wire()
    observation: dict[str, Any] = {}
    try:
        await _bridge_pickup(
            cast(SessionHostClient, wire), 7, "retained-native",
            'steer "quote" & $literal \u03a9', 12345,
            refused=expected_refusal, observation=observation,
        )
    except Exception as error:
        assert reject_prompt and "fixture refusal" in str(error)
    else:
        assert not reject_prompt or expected_refusal
        assert observation["session_id"] == "retained-native"
        assert observation["child_pid"] == 12345
        if expected_refusal:
            assert observation["result"] == {"refused": True, "code": -32001}
        else:
            assert {"type": "agent_message", "data": {"text": "Bridge observed this response"}} in observation["events"]
    assert wire.closed
    assert [message["method"] for message in wire.messages] == ["initialize", "session/prompt"]

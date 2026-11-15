"""Actual Bridge pickup within the existing Driver native ACP packet."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
import importlib
import json
from pathlib import Path
from typing import Any, Literal, Protocol, cast

import pytest

from agent_bridge.acp_client import AcpClient
from agent_bridge.session_host.acp_adapter import open_acp_streams
from agent_remote_driver.session_host.client import SessionHostClient


class _NativeFixture(Protocol):
    async def run_native_copilot_roundtrip(
        self, tmp_path: Path, distribution: Literal["managed-environment", "copied-core"],
        pickup: Callable[[SessionHostClient, int, str, str, int], Awaitable[None]],
    ) -> None: ...


async def _bridge_pickup(
    wire: SessionHostClient, cursor: int, native: str, prompt: str, child_pid: int,
) -> None:
    streams = await open_acp_streams(wire, start_from=cursor)
    bridge = AcpClient()

    async def close_frontend() -> None:
        await streams.aclose()
        await wire.close()

    try:
        await asyncio.wait_for(bridge.start_streams(
            streams.reader, streams.writer, child_pid=child_pid, closer=close_frontend,
        ), 30)
        bridge.adopt_session(native)
        result = await asyncio.wait_for(bridge.send_prompt(prompt), 30)
        assert result["stop_reason"] == "end_turn"
    finally:
        try:
            await asyncio.wait_for(bridge.shutdown(), 5)
        finally:
            await close_frontend()


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


@pytest.mark.asyncio
@pytest.mark.parametrize("reject_prompt", [False, True])
async def test_bridge_pickup_uses_only_initialize_and_existing_prompt(
    reject_prompt: bool,
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
                reply["error"] = {"code": -32603, "message": "fixture refusal"}
            await self.pending.put((len(self.messages), (json.dumps(reply) + "\n").encode()))

        async def frames(self) -> AsyncIterator[tuple[int, bytes]]:
            while True:
                yield await self.pending.get()

        async def ack(self, sequence: int) -> None:
            assert 0 < sequence <= len(self.messages)

        async def close(self) -> None:
            self.closed = True

    wire = Wire()
    try:
        await _bridge_pickup(
            cast(SessionHostClient, wire), 7, "retained-native",
            'steer "quote" & $literal \u03a9', 12345,
        )
    except Exception as error:
        assert reject_prompt and "fixture refusal" in str(error)
    else:
        assert not reject_prompt
    assert wire.closed
    assert [message["method"] for message in wire.messages] == ["initialize", "session/prompt"]

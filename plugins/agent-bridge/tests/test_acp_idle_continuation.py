"""Actual SDK traffic, callback fences and inert private state restoration."""

from __future__ import annotations

import asyncio
import copy
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any

import pytest
from zdd.private_protocol import Deadline, PeerIdentity, PrivateIPCError
from zdd.routing import Endpoint

from agent_bridge.acp_client import AcpClient
from agent_bridge.acp_continuation import (
    capture_idle_continuation, decode_idle_continuation, prepare_idle_continuation,
)
from agent_bridge.events import EventLog
from agent_bridge.session_host import private_receipt as receipt_module
from agent_bridge.session_host.acp_boundary import AcpWireBoundary
from agent_bridge.session_host.private_receipt import VerifiedHostReceipt
from test_predecessor_runtime import NativeChannel


def wire(**fields: Any) -> bytes:
    return (json.dumps({"jsonrpc": "2.0", **fields}, separators=(",", ":")) + "\n").encode()


@asynccontextmanager
async def connected(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[
    AcpClient, EventLog, Callable[..., VerifiedHostReceipt],
    Callable[[bytes], Awaitable[None]], asyncio.StreamReader,
]]:
    boundary = AcpWireBoundary()
    peer_ready = asyncio.Event()
    peer_writer = None
    peer_tasks = []
    options = [
        {"id": "model", "name": "Model", "type": "select", "currentValue": "selected",
         "options": [{"value": "selected", "name": "Selected"}]},
        {"id": "reasoning_effort", "name": "Effort", "type": "select", "currentValue": "high",
         "options": [{"value": "high", "name": "High"}]},
    ]

    async def send(raw: bytes) -> None:
        assert peer_writer is not None
        boundary.observe(raw, to_child=False)
        peer_writer.write(raw)
        await peer_writer.drain()

    async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nonlocal peer_writer
        peer_tasks.append(asyncio.current_task())
        peer_writer = writer
        peer_ready.set()
        try:
            while raw := await reader.readline():
                boundary.observe(raw, to_child=True)
                message = json.loads(raw)
                method = message.get("method")
                if method == "initialize":
                    result = {"protocolVersion": 1, "agentCapabilities": {}}
                elif method == "session/new":
                    result = {"sessionId": "surviving-acp", "configOptions": options}
                elif method == "session/prompt":
                    for update in [
                        {"sessionUpdate": "agent_thought_chunk",
                         "content": {"type": "text", "text": "actual-thought"}},
                        {"sessionUpdate": "tool_call", "toolCallId": "actual-tool",
                         "title": "Read", "kind": "read", "status": "in_progress"},
                        {"sessionUpdate": "tool_call_update", "toolCallId": "actual-tool",
                         "status": "completed", "content": [{
                             "type": "content", "content": {"type": "text", "text": "tool-result"},
                         }]},
                    ]:
                        await send(wire(method="session/update", params={
                            "sessionId": "surviving-acp", "update": update,
                        }))
                    await send(wire(method="session/update", params={
                        "sessionId": "surviving-acp", "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": "actual-reducer-text"},
                        },
                    }))
                    result = {"stopReason": "end_turn"}
                else:
                    continue
                await send(wire(id=message["id"], result=result))
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    reader, writer = await asyncio.open_connection(
        "127.0.0.1", server.sockets[0].getsockname()[1], limit=64 * 1024 * 1024,
    )
    await peer_ready.wait()
    events = EventLog(session_id="surviving-session", acp_session_id="surviving-acp")
    client = AcpClient(
        on_event=lambda kind, data: events.append(kind, data),
        preference_source="target-settings",
        target_preferences={"status": "resolved", "values": {
            "model": "selected", "reasoning_effort": "high",
        }},
    )
    verified = []
    monkeypatch.setattr(receipt_module, "_native_channel_type", lambda: NativeChannel)

    def receipt(
        *, mutate: Callable[[dict[str, Any]], None] | None = None,
    ) -> VerifiedHostReceipt:
        cursor = boundary._messages[False]
        stamp = boundary.capture(cursor, cursor)
        host = PeerIdentity(12345, "23456", "synthetic-principal")
        front = Endpoint("127.0.0.1", 54321, 34567, "fixture", 4, "45678")
        payload = {
            "schema": 1, "host": asdict(host), "instance": "a" * 64,
            "session_id": "surviving-session", "config_identity": "b" * 64,
            "attachment_generation": 2, "frontend": front.to_dict(), "snapshot": "c" * 64,
            "ack_cursor": cursor, "max_seq": cursor, "child_pid": 56789,
            "protocol_version": 1, "host_version": "fixture",
            "data_endpoint": {"bind": "127.0.0.1", "port": 1234},
            "acp_wire": asdict(stamp),
        }
        if mutate is not None:
            mutate(payload)
        value = VerifiedHostReceipt(
            payload, NativeChannel(host), host, front, "surviving-session", "b" * 64,
            Deadline(30),
        )
        verified.append(value)
        return value

    try:
        await client.start_streams(
            reader, writer, child_pid=56789,
            private_continuation=True, continuation_events=events,
        )
        await client.new_session("/synthetic/workspace")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        yield client, events, receipt, send, reader
    finally:
        await client.shutdown()
        await writer.wait_closed()
        for value in verified:
            value.close()
        server.close()
        await server.wait_closed()
        await asyncio.gather(*peer_tasks)


async def settle(client: AcpClient) -> None:
    for _ in range(100):
        conn = client._connection._conn
        if conn._tasks._tasks == {conn._recv_task}:
            return
        await asyncio.sleep(0)
    pytest.fail("Actual SDK callback tasks did not settle")


def test_real_sdk_idle_reducer_preferences_and_counter_restore_inert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async with connected(monkeypatch) as (client, events, receipt, send, _reader):
            result = await client.send_prompt("synthetic prompt")
            assert result["response_text"] == "actual-reducer-text"
            await settle(client)
            client._background_tasks = {"live-background": "actual-tool"}
            client._subagent_attr.subagents = {"live-background": "actual-tool"}
            client._subagent_attr.tool_call_agents = {
                "nested-tool": ("live-background", "actual-tool"),
            }
            native = receipt()
            payload = capture_idle_continuation(client, native, events, Deadline(2))
            snapshot = decode_idle_continuation(payload)
            assert snapshot.host.acp_wire.next_client_request_id == 3
            assert snapshot.preferences.confirmed == {"model": "selected", "reasoning_effort": "high"}
            assert snapshot.reducer.response == ["actual-reducer-text"]
            assert snapshot.reducer.thought == ["actual-thought"]
            assert snapshot.reducer.tools[0].content == ["tool-result"]
            assert "actual-reducer-text" not in repr(snapshot)

            # An unstarted stream is inert even with real SDK objects and
            # retained native receipt custody. No receiving or reinitialize.
            staged_reader, staged_writer = await asyncio.open_connection(
                "127.0.0.1", client._idle_transport.writer.get_extra_info("peername")[1],
            )
            try:
                successor = prepare_idle_continuation(
                    payload, native, events, staged_reader, staged_writer, Deadline(2),
                    on_event=lambda kind, data: events.append(kind, data),
                )
                assert not successor.is_running
                assert successor.acp_session_id == client.acp_session_id
                assert successor._connection._conn._recv_task is None
                assert successor._connection._conn._next_request_id == 3
                assert successor._build_turn_result() == client._build_turn_result()
                assert successor.confirmed_preferences == client.confirmed_preferences
                assert successor.active_background_tasks == ["live-background"]
                assert successor._subagent_attr.tool_call_agents == client._subagent_attr.tool_call_agents
                successor.confirmed_preferences["model"] = "receiver-only"
                assert client.confirmed_preferences["model"] == "selected"
                with pytest.raises(RuntimeError, match="not activated"):
                    await successor.send_prompt("must not write")
                with pytest.raises(ConnectionError, match="unavailable"):
                    await successor._connection.new_session(cwd="/must-not-create", mcp_servers=[])
                assert not successor._connection._conn._pending
                await successor.shutdown()
                await staged_writer.wait_closed()
            finally:
                staged_writer.close()
                await staged_writer.wait_closed()
    asyncio.run(scenario())


def test_delivered_notification_does_not_mean_callback_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async with connected(monkeypatch) as (client, events, receipt, send, _reader):
            entered, release = asyncio.Event(), asyncio.Event()
            tracker = client._connection._session_updates
            original = tracker._session_update

            async def blocked(*args: Any, **kwargs: Any) -> None:
                entered.set()
                await release.wait()
                return await original(*args, **kwargs)

            monkeypatch.setattr(tracker, "_session_update", blocked)
            await send(wire(method="session/update", params={
                "sessionId": "surviving-acp", "update": {
                    "sessionUpdate": "available_commands_update", "availableCommands": [],
                },
            }))
            await entered.wait()
            native = receipt()
            try:
                with pytest.raises(PrivateIPCError):
                    capture_idle_continuation(client, native, events, Deadline(2))
            finally:
                release.set()
                await settle(client)
            capture_idle_continuation(client, native, events, Deadline(2))
    asyncio.run(scenario())


@pytest.mark.parametrize("case", [
    "outer-operation", "prompt", "permission", "elicitation", "timer",
    "reader-partial", "counter", "wire-stamp", "event-owner", "transport-lost",
    "unverified-preferences", "unsupported-private-state",
])
def test_unsupported_frontend_boundaries_refuse(
    monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    async def scenario() -> None:
        async with connected(monkeypatch) as (client, events, receipt, _send, reader):
            native = receipt()
            if case == "outer-operation":
                client._idle_operations = 1
            elif case == "prompt":
                client._prompt_in_flight = True
            elif case == "permission":
                client._pending_permission_future = asyncio.get_running_loop().create_future()
            elif case == "elicitation":
                client._pending_ask_user_meta = {"pending": {"secret": "fixture-sensitive"}}
            elif case == "timer":
                client._out_of_turn_open = True
            elif case == "reader-partial":
                reader.feed_data(b'{"jsonrpc":')
            elif case == "counter":
                client._connection._conn._next_request_id += 1
            elif case == "wire-stamp":
                native = receipt(mutate=lambda value: value["acp_wire"].update(client_bytes=0))
            elif case == "event-owner":
                events = EventLog(session_id="surviving-session", acp_session_id="surviving-acp")
            elif case == "transport-lost":
                client.mark_transport_lost()
            elif case == "unverified-preferences":
                client._verified_options = None
            elif case == "unsupported-private-state":
                client.target_preferences["unsupported"] = object()
            with pytest.raises(PrivateIPCError) as caught:
                capture_idle_continuation(client, native, events, Deadline(2))
            assert "fixture-sensitive" not in str(caught.value)
    asyncio.run(scenario())


@pytest.mark.parametrize("case", ["pending-rpc", "scheduled-handler", "unknown-sdk"])
def test_sdk_owned_future_and_task_census_refuse(
    monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    async def scenario() -> None:
        async with connected(monkeypatch) as (client, events, receipt, _send, _reader):
            native = receipt()
            conn = client._connection._conn
            task = None
            if case == "pending-rpc":
                conn._pending[99] = asyncio.get_running_loop().create_future()
            elif case == "scheduled-handler":
                task = conn._tasks.create(asyncio.sleep(3600), name="actual-pending-notification")
            else:
                monkeypatch.delattr(conn, "_pending")
            try:
                with pytest.raises(PrivateIPCError):
                    capture_idle_continuation(client, native, events, Deadline(2))
            finally:
                if case == "pending-rpc":
                    conn._pending.pop(99).cancel()
                elif case == "unknown-sdk":
                    conn._pending = {}
                if task is not None:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize("case", [
    "cursor", "host", "schema", "unknown", "tool", "coercion", "preference",
])
def test_mutated_snapshot_or_receiver_cursor_refuses(
    monkeypatch: pytest.MonkeyPatch, case: str,
) -> None:
    async def scenario() -> None:
        async with connected(monkeypatch) as (client, events, receipt, _send, reader):
            native = receipt()
            payload = capture_idle_continuation(client, native, events, Deadline(2))
            changed = copy.deepcopy(payload)
            if case == "cursor":
                changed["next_event_id"] += 1
            elif case == "host":
                changed["host"]["snapshot"] = "d" * 64
            elif case == "schema":
                changed["schema"] = True
            elif case == "unknown":
                changed["unowned"] = "fixture-secret"
            elif case == "tool":
                changed["reducer"]["tools"] = [{"tool_call_id": "missing-fields"}]
            elif case == "coercion":
                changed["host"]["acp_wire"]["client_bytes"] = str(
                    changed["host"]["acp_wire"]["client_bytes"],
                )
            else:
                changed["preferences"]["confirmed"]["model"] = "not-read-back"
            with pytest.raises(PrivateIPCError):
                prepare_idle_continuation(
                    changed, native, events, reader, client._idle_transport.writer, Deadline(2),
                )
    asyncio.run(scenario())


def test_outer_operation_after_rpc_response_is_still_not_idle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async with connected(monkeypatch) as (client, events, receipt, _send, _reader):
            entered, release = asyncio.Event(), asyncio.Event()
            original = client._apply_model_config

            async def blocked(*args: Any, **kwargs: Any) -> None:
                entered.set()
                await release.wait()
                await original(*args, **kwargs)

            monkeypatch.setattr(client, "_apply_model_config", blocked)
            task = asyncio.create_task(client.new_session("/synthetic/workspace"))
            try:
                await entered.wait()
                await settle(client)
                assert not client._connection._conn._pending
                assert client._idle_operations == 1
                with pytest.raises(PrivateIPCError):
                    capture_idle_continuation(client, receipt(), events, Deadline(2))
            finally:
                release.set()
                await task
            await settle(client)
            capture_idle_continuation(client, receipt(), events, Deadline(2))
    asyncio.run(scenario())

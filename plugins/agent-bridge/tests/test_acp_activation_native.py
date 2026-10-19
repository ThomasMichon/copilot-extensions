"""Real ACP child and native-pinned old/new processes exercise idle activation."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import socket
import subprocess
import sys
from contextlib import AsyncExitStack
from pathlib import Path

import pytest
from zdd.private_child import OwnedChild
from zdd.private_protocol import Deadline, PeerIdentity, PrivateIPCError, read_frame, write_frame
from zdd.routing import Endpoint

from agent_bridge.acp_activation import NativeIdleActivation
from agent_bridge import __version__
from agent_bridge.acp_client import AcpClient
from agent_bridge.acp_continuation import capture_idle_continuation
from agent_bridge.acp_durability import capture_idle_durability, decode_idle_durability
from agent_bridge.db import Database
from agent_bridge.events import EventLog, SseEvent
from agent_bridge.launch_runtime import executing_executable
from agent_bridge.models import SessionStatus
from agent_bridge.session_host import protocol
from agent_bridge.session_host.acp_adapter import open_acp_streams
from agent_bridge.session_host.client import SessionHostClient
from agent_bridge.session_host.host import SessionHost
from agent_bridge.session_host.private_attachment import AttachmentOperation, PrivateAttachment, _self
from agent_bridge.session_host.private_receipt import VerifiedHostReceipt
from agent_bridge.session_manager import Session
from agent_bridge.transport import SpawnTarget
from test_acp_idle_continuation import settle
from test_private_host_attachment import until
from test_private_host_native import send
from test_relay_adoption_native import connect, launch, native_listener
from test_runtime_controls import publish


async def response(process: subprocess.Popen) -> dict:
    line = await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), 10)
    if not line:
        await asyncio.to_thread(process.wait, 3)
        pytest.fail("Native ACP frontend exited: " + process.stderr.read().decode())
    return json.loads(line)


def acp_child_main() -> None:
    """A real persistent ACP agent that refuses reinitialization or ID reuse."""
    next_id = 0
    options = [
        {"id": "model", "name": "Model", "type": "select", "currentValue": "selected",
         "options": [{"value": "selected", "name": "Selected"}]},
    ]
    for raw in sys.stdin.buffer:
        message = json.loads(raw)
        assert message["id"] == next_id
        next_id += 1
        method = message["method"]
        if method == "initialize":
            assert next_id == 1
            result = {"protocolVersion": 1, "agentCapabilities": {}}
        elif method == "session/new":
            assert next_id == 2
            result = {"sessionId": "continuous-acp", "configOptions": options}
        else:
            assert method == "session/prompt" and message["params"]["sessionId"] == "continuous-acp"
            print(json.dumps({
                "jsonrpc": "2.0", "method": "session/update", "params": {
                    "sessionId": "continuous-acp", "update": {
                        "sessionUpdate": "agent_message_chunk",
                        "content": {"type": "text", "text": f"continuous-result-{message['id']}"},
                    },
                },
            }), flush=True)
            result = {"stopReason": "end_turn"}
        print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)


def frontend_main() -> None:
    address, host_json, port, db_path, root = sys.argv[1:6]
    host_peer = PeerIdentity(**json.loads(host_json))
    native = connect(address, host_peer, Deadline(30))
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    print(json.dumps({"frontend_port": listener.getsockname()[1]}), flush=True)

    async def scenario() -> None:
        db = raw = streams = client = original = activation = None
        events = None
        pending = app = binding = None
        unowned_db = None
        stack = AsyncExitStack()

        async def command() -> dict:
            return json.loads(await asyncio.to_thread(sys.stdin.buffer.readline))

        async def control(value: dict) -> object:
            budget = Deadline(10)
            await asyncio.to_thread(write_frame, native, {"grant": value["grant"]}, budget)
            return await asyncio.to_thread(read_frame, native, budget)

        def verified(payload, endpoint, budget):
            return VerifiedHostReceipt(
                payload, native, host_peer, Endpoint(**endpoint),
                "continuous-session", "b" * 64, budget,
            )

        async def prompt(text: str, turn: int) -> dict:
            db.create_turn("continuous-session", turn, text, 1.0 + turn * 2)
            result = await client.send_prompt(text)
            await settle(client)
            db.update_turn(
                "continuous-session", turn, response_text=result["response_text"],
                thought_text=result["thought_text"], stop_reason=result["stop_reason"],
                tool_calls_json=json.dumps(result["tool_calls"]), completed_at=2.0 + turn * 2,
            )
            db.flush()
            return result

        try:
            while True:
                value = await command()
                action = value["action"]
                if action == "exit":
                    return
                if action == "control":
                    result = await control(value)
                    if value.get("lose_reply"):
                        result = {"reply_lost": True}
                elif action == "old-prepare":
                    raw = await SessionHostClient.connect(port=int(port))
                    await protocol.write_message(
                        raw._writer, protocol.MsgType.ATTACH_PRIVATE, value["ticket"].encode(),
                    )
                    result = {"prepared": True}
                elif action == "start":
                    hello = await protocol.read_message(raw._reader)
                    assert hello[0] is protocol.MsgType.HELLO
                    db = Database(Path(db_path))
                    db.create_session("continuous-session", "synthetic", None, None, "local", "idle", 1)
                    db.update_session_acp_id("continuous-session", "continuous-acp")
                    events = EventLog(db=db, session_id="continuous-session", acp_session_id="continuous-acp")
                    streams = await open_acp_streams(raw)
                    client = AcpClient(
                        on_event=lambda kind, data: events.append(kind, data),
                        preference_source="target-settings", target_preferences={
                            "status": "resolved", "values": {"model": "selected"},
                        },
                    )
                    await client.start_streams(
                        streams.reader, streams.writer, child_pid=protocol.unpack_u64(hello[1][8:16]),
                        closer=streams.aclose, private_continuation=True, continuation_events=events,
                    )
                    await client.new_session("/synthetic")
                    await settle(client)
                    result = await prompt("before transfer", 0)
                elif action == "capture":
                    budget = Deadline(20)
                    payload = await control(value)
                    original = verified(payload, value["frontend"], budget)
                    continuation = capture_idle_continuation(client, original, events, budget)
                    session = Session(
                        "continuous-session", "synthetic", SpawnTarget(cwd="/synthetic"),
                        preserve_target=True,
                    )
                    session.status, session.client = SessionStatus.IDLE, client
                    session.event_log, session.acp_session_id = events, "continuous-acp"
                    session.turn_count = 1
                    proof = await capture_idle_durability(session, continuation, original, budget)
                    result = {"continuation": continuation, "durability": proof, "receipt": payload}
                    if value.get("runtime"):
                        from runtime_idle_fixture import capture_runtime

                        result["runtime"] = capture_runtime(Path(root), db, session, original)
                elif action == "new-prepare":
                    budget = Deadline(20)
                    original = verified(
                        value["capture"]["receipt"],
                        value["capture"]["receipt"]["frontend"], budget,
                    )
                    if "runtime" in value["capture"]:
                        from runtime_idle_fixture import prepare_runtime

                        pending, app, binding = await prepare_runtime(
                            stack, value["capture"], Endpoint(**value["frontend"]),
                            native, listener, original, budget,
                        )
                        db, events = binding.source, binding.log
                    else:
                        db = Database(Path(db_path), read_only=True)
                        durable = decode_idle_durability(value["capture"]["durability"])
                        events = EventLog.from_captured_events(
                            [SseEvent(event.event_id, event.event_type, event.data, event.timestamp)
                             for event in durable.events], durable.next_event_id,
                            db=db, session_id=durable.session_id, acp_session_id=durable.acp_session_id,
                            worktree_id=None,
                        )
                    activation = await NativeIdleActivation.prepare(
                        Path(root), Endpoint(**value["frontend"]), original,
                        value["reservation"], value["capture"]["continuation"],
                        value["capture"]["durability"], events, db, budget,
                        runtime_binding=binding,
                    )
                    client = activation.client
                    result = {
                        "prepared": True, "running": client.is_running,
                        "tasks": len(activation._streams._tasks),
                        "reader": client._connection._conn._recv_task is not None,
                    }
                elif action == "activate-client":
                    rogue = None
                    if binding is not None:
                        import agent_bridge.acp_activation as activation_module

                        def forbidden(_source):
                            raise AssertionError("Opened a competing database owner")
                        activation_module.open_existing_writable = forbidden
                        if value.get("gate") == "runtime-session":
                            binding.session.turn_count += 1
                        elif value.get("gate") == "runtime-database":
                            unowned_db = Database(Path(db_path), read_only=True)
                            app.state.db = unowned_db
                        elif value.get("gate") == "runtime-callback":
                            client._on_event = lambda *_args: None
                        elif value.get("gate") == "runtime-cache":
                            app.state.worktree_discovery_cache = object()
                        elif value.get("gate") == "runtime-live":
                            app.state.live_event_store = object()
                        elif value.get("gate") == "runtime-auth":
                            app.state.auth_custody = object()
                        elif value.get("gate") == "runtime-token":
                            app.state.auth_token = "different-synthetic-token"
                        elif value.get("gate") == "runtime-codes":
                            app.state.ui_login_codes = dict(app.state.ui_login_codes)
                    if value.get("gate") == "swapped-stream":
                        rogue = await SessionHostClient.connect(port=int(port))
                        activation._raw = rogue
                    async def query(budget):
                        print(json.dumps({"inspect_required": True}), flush=True)
                        grant = await command()
                        receipt = await control(grant)
                        if grant.get("mutation") == "runtime-late-session":
                            binding.session.turn_count += 1
                        elif grant.get("mutation") == "runtime-late-cache":
                            pending.runtime.worktrees._cache["actual"][0].turn_count += 1
                        elif grant.get("mutation") == "runtime-late-live":
                            pending.runtime.live_events._sdk_events["a"]["shared"].clear()
                        elif grant.get("mutation") == "runtime-late-auth":
                            pending.runtime.authentication.codes["unused-native-code"] += 1.0
                        return native, receipt
                    try:
                        await activation.activate(query)
                        db = activation._database
                        result = {
                            "activated": True, "running": client.is_running,
                            "next_request_id": client._connection._conn._next_request_id,
                            "event_id": events._next_id,
                        }
                        if binding is not None:
                            assert binding.session.client is client
                            assert events._db is app.state.db is binding.database
                            assert not pending.serving and app.state.ready is False
                            from agent_bridge.serving_runtime import CandidateServing

                            with pytest.raises(PrivateIPCError):
                                CandidateServing(app, pending.runtime)
                            result["shared_runtime"] = True
                    except PrivateIPCError as exc:
                        if value.get("gate") == "runtime":
                            raise
                        result = {"refusal": exc.reason.value, "running": client.is_running}
                        if binding is not None:
                            assert events._db is binding.database
                            assert binding.database.get_turn("continuous-session", 0) is not None
                            assert binding.session.client is None
                            result["shared_database_preserved"] = True
                    finally:
                        if rogue is not None:
                            await activation.aclose()
                            result["unowned_stream_preserved"] = not rogue._writer.is_closing()
                            await rogue.close()
                elif action == "prompt":
                    if binding is not None:
                        assert await binding.manager.submit_prompt("continuous-session", "after transfer") == 1
                        await asyncio.wait_for(binding.session._prompt_task, 10)
                        assert binding.session.turn_count == 2
                        assert binding.session.status is SessionStatus.IDLE
                        db.flush()
                        result = dict(db.get_turn("continuous-session", 1))
                        await activation.aclose()
                        assert binding.session.client is None
                        assert events._db is binding.database is app.state.db
                        assert binding.database.get_turn("continuous-session", 1) is not None
                        result["shared_database_preserved"] = True
                    else:
                        result = await prompt("after transfer", 1)
                else:
                    raise AssertionError("unknown native fixture command")
                print(json.dumps(result), flush=True)
        finally:
            if activation is not None:
                await activation.aclose()
            elif client is not None:
                await client.shutdown()
            if raw is not None:
                await raw.close()
            if original is not None:
                original.close()
            await stack.aclose()
            if pending is not None:
                assert not pending._idle_writer.is_alive()
                assert pending._writable_database._local.conn is None
            if unowned_db is not None:
                assert unowned_db._local.conn is not None
                unowned_db.close()
            if db is not None:
                db.close()

    try:
        asyncio.run(scenario())
    finally:
        native.close()
        listener.close()


@pytest.mark.skipif(os.name not in ("nt", "posix"), reason="native private transport unavailable")
@pytest.mark.parametrize("gate", [
    None, "precommit", "wrong-endpoint", "swapped-stream",
    "database-drift", "late-database-drift", "route-drift",
    "runtime", "runtime-session", "runtime-database", "runtime-callback", "runtime-late-session",
    "runtime-cache", "runtime-late-cache",
    "runtime-live", "runtime-late-live",
    "runtime-auth", "runtime-token", "runtime-codes", "runtime-late-auth",
])
def test_real_native_idle_acp_activation_has_exact_custody_and_independent_readback(tmp_path: Path, gate):
    async def scenario() -> None:
        child = await asyncio.create_subprocess_exec(
            str(executing_executable()), "-u", "-c",
            "import json, sys\n" + inspect.getsource(acp_child_main) + "\nacp_child_main()",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        host = SessionHost(child, unexpected_reap_seconds=0)
        owner = PrivateAttachment(
            host, tmp_path, "b" * 64, "continuous-session", acp_wire_boundary=True,
        )
        port = await host.serve()
        db_path = tmp_path / "continuous.db"
        processes, references, channels, listeners = [], [], [], []
        try:
            async def frontend(generation):
                address, listener = native_listener(tmp_path, f"acp-owner-{generation}")
                listeners.append(listener)
                process = launch(
                    "from test_acp_activation_native import frontend_main; frontend_main()",
                    address, json.dumps(_self().__dict__), str(port), str(db_path), str(tmp_path),
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
                processes.append(process)
                reference = OwnedChild(process, executing_executable(), Deadline(5))
                references.append(reference)
                channel = await asyncio.to_thread(listener.accept, Deadline(10), expected=reference.peer)
                channels.append(channel)
                port_reply = await response(process)
                endpoint = Endpoint("127.0.0.1", port_reply["frontend_port"], reference.peer.pid, __version__, generation,
                                    reference.peer.start_time)
                return process, channel, endpoint

            async def native_control(process, channel, endpoint, operation, snapshot=None, **extra):
                grant = owner.register(channel.peer, endpoint, operation, Deadline(10), snapshot=snapshot)
                serving = asyncio.create_task(owner.handle(channel, Deadline(10)))
                send(process, {"action": "control", "grant": grant, **extra})
                result = await response(process)
                await serving
                return result

            old, old_channel, old_endpoint = await frontend(1)
            publish(tmp_path, old_endpoint)
            reservation = await native_control(old, old_channel, old_endpoint, AttachmentOperation.RESERVE)
            send(old, {"action": "old-prepare", "ticket": reservation["ticket"]})
            assert await response(old) == {"prepared": True}
            await until(lambda: owner._stage.front is not None)
            await native_control(old, old_channel, old_endpoint, AttachmentOperation.ACTIVATE)
            send(old, {"action": "start"})
            assert (await response(old))["response_text"] == "continuous-result-2"
            await until(lambda: host.ack_cursor == host.max_seq)
            grant = owner.register(old_channel.peer, old_endpoint, AttachmentOperation.CAPTURE, Deadline(10))
            serving = asyncio.create_task(owner.handle(old_channel, Deadline(10)))
            runtime = gate is not None and gate.startswith("runtime")
            send(old, {"action": "capture", "grant": grant, "frontend": old_endpoint.to_dict(), "runtime": runtime})
            capture = await response(old)
            await serving
            assert capture["continuation"]["host"]["acp_wire"]["next_client_request_id"] == 3
            new, new_channel, new_endpoint = await frontend(2)
            publish(tmp_path, new_endpoint, old_endpoint)
            assert await native_control(
                new, new_channel, new_endpoint, AttachmentOperation.RESERVE,
                capture["receipt"]["snapshot"],
            ) == {"refusal": "private-ipc-authority-refused"}
            send(old, {"action": "exit"})
            assert await asyncio.to_thread(old.wait, 5) == 0
            await until(lambda: host._front is None)
            reservation = await native_control(
                new, new_channel, new_endpoint, AttachmentOperation.RESERVE,
                capture["receipt"]["snapshot"],
            )
            send(new, {
                "action": "new-prepare", "frontend": new_endpoint.to_dict(),
                "capture": capture, "reservation": reservation,
            })
            assert await response(new) == {"prepared": True, "running": False, "tasks": 0, "reader": False}
            await until(lambda: owner._stage.front is not None)
            if gate != "precommit":
                assert await native_control(
                    new, new_channel, new_endpoint, AttachmentOperation.ACTIVATE,
                    capture["receipt"]["snapshot"], lose_reply=True,
                ) == {"reply_lost": True}
            send(new, {"action": "activate-client", "gate": gate})
            early_refusal = gate in (
                "swapped-stream", "runtime-session", "runtime-database", "runtime-callback", "runtime-cache",
                "runtime-live",
                "runtime-auth", "runtime-token", "runtime-codes",
            )
            for query_index in range(0 if early_refusal else 2):
                assert await response(new) == {"inspect_required": True}
                if gate == "wrong-endpoint":
                    # The grant still names the real native caller; corrupting
                    # the captured observation cannot establish socket custody.
                    owner._owned_front.writer.get_extra_info = (
                        lambda name, default=None: ("127.0.0.1", 1) if name == "peername" else default
                    )
                if gate == "database-drift" or (gate == "late-database-drift" and query_index == 1):
                    external = Database(db_path)
                    try:
                        external.update_turn("continuous-session", 0, response_text="different committed bytes")
                    finally:
                        external.close()
                if gate == "route-drift" and query_index == 1:
                    publish(tmp_path, old_endpoint, new_endpoint)
                grant = owner.register(
                    new_channel.peer, new_endpoint, AttachmentOperation.INSPECT_COMMIT,
                    Deadline(10), snapshot=reservation["recovery_snapshot"],
                )
                serving = asyncio.create_task(owner.handle(new_channel, Deadline(10)))
                send(new, {
                    "action": "control", "grant": grant,
                    "mutation": gate if gate in (
                        "runtime-late-session", "runtime-late-cache", "runtime-late-live",
                        "runtime-late-auth",
                    )
                    and query_index == 1 else None,
                })
                await serving
                if gate in ("precommit", "wrong-endpoint", "database-drift") or (
                    gate == "route-drift" and query_index == 1
                ):
                    break
            result = await response(new)
            if gate in (None, "runtime"):
                assert result["activated"] is True and result["running"] is True
                assert result["next_request_id"] == 3
                assert result["event_id"] == capture["durability"]["next_event_id"]
                send(new, {"action": "prompt"})
                after = await response(new)
                assert after["response_text"] == "continuous-result-3"
                if runtime:
                    assert result["shared_runtime"] is True
                    assert after["shared_database_preserved"] is True
                await until(lambda: host.ack_cursor == host.max_seq)
                passive = Database(db_path, read_only=True)
                try:
                    turns = passive.get_turns("continuous-session")
                    assert [turn["response_text"] for turn in turns] == [
                        "continuous-result-2", "continuous-result-3",
                    ]
                    events = passive.get_events("continuous-session")
                    assert [event["event_id"] for event in events] == list(range(1, len(events) + 1))
                    assert "continuous-result-3" in json.dumps(events)
                finally:
                    passive.close()
            else:
                assert result["refusal"] == "private-ipc-authority-refused"
                assert result["running"] is False
                if gate == "swapped-stream":
                    assert result["unowned_stream_preserved"] is True
                if runtime:
                    assert result["shared_database_preserved"] is True
            assert host.child_pid == child.pid and child.returncode is None
            send(new, {"action": "exit"})
            assert await asyncio.to_thread(new.wait, 5) == 0
        finally:
            for process in processes:
                if process.poll() is None:
                    send(process, {"action": "exit"})
                await asyncio.to_thread(process.wait, 5)
                process.stdin.close()
                process.stdout.close()
                process.stderr.close()
            child.stdin.close()
            await asyncio.wait_for(child.wait(), 3)
            owner.close()
            await host.close()
            for channel in channels:
                channel.close()
            for listener in listeners:
                listener.close()
            for reference in references:
                reference.close()
    asyncio.run(scenario())


if __name__ == "__main__":
    frontend_main()

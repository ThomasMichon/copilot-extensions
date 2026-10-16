"""Real host/child and native-pinned frontends preserve replay and recovery."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from zdd.private_child import OwnedChild
from zdd.private_protocol import Deadline, PeerIdentity, read_frame, write_frame
from zdd.routing import Endpoint

from agent_bridge.launch_runtime import executing_executable
from agent_bridge.session_host import protocol
from agent_bridge.session_host.host import SessionHost
from agent_bridge.session_host.private_attachment import (
    AttachmentOperation, PrivateAttachment, _self,
)
from agent_bridge.session_host.private_receipt import VerifiedHostReceipt
from test_private_host_attachment import until
from test_relay_adoption_native import connect, launch, native_listener
from test_runtime_controls import publish


def frontend_main() -> None:
    address, host_json, port = sys.argv[1:4]
    native = connect(address, PeerIdentity(**json.loads(host_json)), Deadline(15))

    async def scenario() -> None:
        reader = writer = None
        try:
            while line := await asyncio.to_thread(sys.stdin.buffer.readline):
                command = json.loads(line)
                action = command["action"]
                if action == "exit":
                    return
                if action == "control":
                    budget = Deadline(3)
                    await asyncio.to_thread(write_frame, native, {"grant": command["grant"]}, budget)
                    response = await asyncio.to_thread(read_frame, native, budget)
                    if command.get("verify_receipt"):
                        receipt = VerifiedHostReceipt(
                            response, native, PeerIdentity(**json.loads(host_json)),
                            Endpoint(**command["frontend"]), "synthetic-native-session",
                            "b" * 64, budget,
                        )
                        try:
                            response = {"verified": True, "receipt": receipt.private_payload(budget)}
                        finally:
                            receipt.close()
                elif action == "prepare":
                    reader, writer = await asyncio.open_connection("127.0.0.1", int(port))
                    await protocol.write_message(
                        writer, protocol.MsgType.ATTACH_PRIVATE, command["ticket"].encode(),
                    )
                    response = {"sent": True}
                elif action == "read":
                    message = await asyncio.wait_for(protocol.read_message(reader), 3)
                    if message[0] is protocol.MsgType.HELLO:
                        response = {
                            "hello": True, "max_seq": protocol.unpack_u64(message[1]),
                            "child_pid": protocol.unpack_u64(message[1][8:16]),
                        }
                    else:
                        seq, payload = protocol.unpack_frame(message[1])
                        response = {"seq": seq, "payload": payload.decode()}
                elif action == "ack":
                    await protocol.write_message(
                        writer, protocol.MsgType.ACK, protocol.pack_u64(command["seq"]),
                    )
                    response = {"sent": True}
                elif action == "write":
                    await protocol.write_message(writer, protocol.MsgType.WRITE, command["data"].encode())
                    response = {"sent": True}
                else:
                    raise AssertionError("unknown fixture command")
                print(json.dumps(response), flush=True)
        finally:
            if writer is not None:
                writer.close()
                await writer.wait_closed()

    try:
        asyncio.run(scenario())
    finally:
        native.close()


def send(process: subprocess.Popen, value: dict[str, object]) -> None:
    process.stdin.write(json.dumps(value).encode() + b"\n")
    process.stdin.flush()


async def response(process: subprocess.Popen) -> dict[str, object]:
    line = await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), 5)
    assert line, "frontend fixture exited before its response"
    return json.loads(line)


@pytest.mark.skipif(os.name not in ("nt", "posix"), reason="native private transport unavailable")
def test_real_native_frontends_require_exact_exit_and_fresh_grant_before_host_replay(tmp_path: Path):
    async def scenario() -> None:
        child = await asyncio.create_subprocess_exec(
            str(executing_executable()), "-u", "-c",
            "import sys\nfor line in sys.stdin.buffer:\n"
            " sys.stdout.buffer.write(line); sys.stdout.buffer.flush()",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        host = SessionHost(child, unexpected_reap_seconds=0)
        owner = PrivateAttachment(host, tmp_path, "b" * 64, "synthetic-native-session")
        port = await host.serve()
        old_address, old_listener = native_listener(tmp_path, "old-host-control")
        new_address, new_listener = native_listener(tmp_path, "new-host-control")
        recovery_address, recovery_listener = native_listener(tmp_path, "recovery-host-control")
        processes, references, channels = [], [], []
        try:
            async def frontend(address, listener, generation):
                process = launch(
                    "from test_private_host_native import frontend_main; frontend_main()",
                    address, json.dumps(_self().__dict__), str(port),
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
                processes.append(process)
                reference = OwnedChild(process, executing_executable(), Deadline(3))
                references.append(reference)
                channel = await asyncio.to_thread(
                    listener.accept, Deadline(5), expected=reference.peer,
                )
                channels.append(channel)
                endpoint = Endpoint(
                    "127.0.0.1", 12345, reference.peer.pid, "fixture", generation,
                    reference.peer.start_time,
                )
                return process, channel, endpoint

            async def native_control(process, channel, endpoint, operation, snapshot=None):
                grant = owner.register(
                    channel.peer, endpoint, operation, Deadline(3), snapshot=snapshot,
                )
                serving = asyncio.create_task(owner.handle(channel, Deadline(3)))
                verify = operation is AttachmentOperation.CAPTURE
                send(process, {
                    "action": "control", "grant": grant, "verify_receipt": verify,
                    "frontend": endpoint.to_dict(),
                })
                result = await response(process)
                await serving
                if verify:
                    assert result["verified"] is True
                    return result["receipt"]
                return result

            old, old_channel, old_endpoint = await frontend(old_address, old_listener, 1)
            publish(tmp_path, old_endpoint)
            reservation = await native_control(old, old_channel, old_endpoint, AttachmentOperation.RESERVE)
            send(old, {"action": "prepare", "ticket": reservation["ticket"]})
            assert await response(old) == {"sent": True}
            await until(lambda: owner._stage.front is not None)
            assert host._front is None and owner._generation == 0
            assert await native_control(old, old_channel, old_endpoint, AttachmentOperation.ACTIVATE) == {
                "attached": True, "attachment_generation": 1, "ack_cursor": 0,
            }
            send(old, {"action": "read"})
            assert (await response(old))["child_pid"] == child.pid
            send(old, {"action": "write", "data": "first-small-acp-frame\n"})
            await response(old)
            send(old, {"action": "read"})
            assert await response(old) == {"seq": 1, "payload": "first-small-acp-frame\n"}
            send(old, {"action": "ack", "seq": 1})
            await response(old)
            await until(lambda: host.ack_cursor == 1)
            snapshot = await native_control(old, old_channel, old_endpoint, AttachmentOperation.CAPTURE)
            assert snapshot["host"] == _self().__dict__
            assert snapshot["child_pid"] == child.pid and snapshot["ack_cursor"] == 1
            new, new_channel, new_endpoint = await frontend(new_address, new_listener, 2)
            publish(tmp_path, new_endpoint, old_endpoint)
            refused = await native_control(
                new, new_channel, new_endpoint, AttachmentOperation.RESERVE, snapshot["snapshot"],
            )
            assert refused == {"refusal": "private-ipc-authority-refused"}
            assert owner._generation == 1 and owner._stage is None and child.returncode is None
            # The live incumbent still drives the same child after refusal.
            send(old, {"action": "write", "data": "second-small-acp-frame\n"})
            await response(old)
            send(old, {"action": "read"})
            assert await response(old) == {"seq": 2, "payload": "second-small-acp-frame\n"}
            send(old, {"action": "ack", "seq": 2})
            await response(old)
            await until(lambda: host.ack_cursor == 2)
            send(old, {"action": "exit"})
            assert await asyncio.to_thread(old.wait, 3) == 0
            await until(lambda: host._front is None)
            reservation = await native_control(
                new, new_channel, new_endpoint, AttachmentOperation.RESERVE, snapshot["snapshot"],
            )
            recovery_snapshot = reservation["recovery_snapshot"]
            assert reservation["recovery_generation"] == 2
            send(new, {"action": "prepare", "ticket": reservation["ticket"]})
            await response(new)
            await until(lambda: owner._stage.front is not None)
            child.stdin.write(b"buffered-across-transfer\n")
            await child.stdin.drain()
            await until(lambda: host.max_seq == 3)
            assert host._front is None and owner._generation == 1
            stage = owner._stage
            assert not stage.activated.done() and stage.front.next_seq == 0
            assert await native_control(
                new, new_channel, new_endpoint, AttachmentOperation.ACTIVATE, snapshot["snapshot"],
            ) == {"attached": True, "attachment_generation": 2, "ack_cursor": 2}
            committed = await native_control(
                new, new_channel, new_endpoint, AttachmentOperation.INSPECT_COMMIT,
                recovery_snapshot,
            )
            assert committed["snapshot"] == recovery_snapshot
            assert committed["host"] == snapshot["host"]
            assert committed["instance"] == snapshot["instance"]
            assert committed["attachment_generation"] == 2
            assert owner._snapshot == recovery_snapshot
            send(new, {"action": "read"})
            assert await response(new) == {"hello": True, "max_seq": 3, "child_pid": child.pid}
            send(new, {"action": "read"})
            assert await response(new) == {"seq": 3, "payload": "buffered-across-transfer\n"}
            assert child.returncode is None and owner._generation == 2
            send(new, {"action": "write", "data": "new-owner-small-frame\n"})
            await response(new)
            send(new, {"action": "read"})
            assert await response(new) == {"seq": 4, "payload": "new-owner-small-frame\n"}
            send(new, {"action": "ack", "seq": 4})
            await response(new)
            await until(lambda: host.ack_cursor == 4)
            assert await native_control(
                new, new_channel, new_endpoint, AttachmentOperation.INSPECT_COMMIT,
                recovery_snapshot,
            ) == {"refusal": "private-ipc-authority-refused"}
            recovered, recovered_channel, recovered_endpoint = await frontend(
                recovery_address, recovery_listener, 3,
            )
            publish(tmp_path, recovered_endpoint, new_endpoint)
            refused = await native_control(
                recovered, recovered_channel, recovered_endpoint, AttachmentOperation.RESERVE,
                recovery_snapshot,
            )
            assert refused == {"refusal": "private-ipc-authority-refused"}
            # No CAPTURE was requested after commit. Recovery uses the private
            # reservation receipt only after the retained committed owner exits.
            send(new, {"action": "exit"})
            assert await asyncio.to_thread(new.wait, 3) == 0
            await until(lambda: host._front is None)
            reservation = await native_control(
                recovered, recovered_channel, recovered_endpoint, AttachmentOperation.RESERVE,
                recovery_snapshot,
            )
            assert reservation["recovery_generation"] == 3
            send(recovered, {"action": "prepare", "ticket": reservation["ticket"]})
            await response(recovered)
            await until(lambda: owner._stage.front is not None)
            child.stdin.write(b"buffered-for-postcommit-recovery\n")
            await child.stdin.drain()
            await until(lambda: host.max_seq == 5)
            assert await native_control(
                recovered, recovered_channel, recovered_endpoint, AttachmentOperation.ACTIVATE,
                recovery_snapshot,
            ) == {"attached": True, "attachment_generation": 3, "ack_cursor": 4}
            send(recovered, {"action": "read"})
            assert await response(recovered) == {"hello": True, "max_seq": 5, "child_pid": child.pid}
            send(recovered, {"action": "read"})
            assert await response(recovered) == {"seq": 5, "payload": "buffered-for-postcommit-recovery\n"}
            send(recovered, {"action": "write", "data": "recovered-owner-small-frame\n"})
            await response(recovered)
            send(recovered, {"action": "read"})
            assert await response(recovered) == {"seq": 6, "payload": "recovered-owner-small-frame\n"}
            assert child.returncode is None
        finally:
            for process in processes:
                if process.poll() is None:
                    send(process, {"action": "exit"})
                assert await asyncio.to_thread(process.wait, 5) == 0
                process.stdin.close()
                process.stdout.close()
                process.stderr.close()
            child.stdin.close()
            assert await asyncio.wait_for(child.wait(), 3) == 0
            await host.close()
            for channel in channels:
                channel.close()
            for reference in references:
                reference.close()
            old_listener.close()
            new_listener.close()
            recovery_listener.close()
    asyncio.run(scenario())

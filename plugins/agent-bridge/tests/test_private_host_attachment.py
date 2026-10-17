"""Host-owned replay and attachment fencing, without ordinary claim/recovery."""

from __future__ import annotations

import asyncio
import warnings
from dataclasses import replace

import pytest
from zdd.private_protocol import Deadline, PeerIdentity, PrivateIPCError
from zdd.routing import Endpoint

from agent_bridge.session_host import protocol
from agent_bridge.session_host.host import SessionHost
from agent_bridge.session_host.client import SessionHostClient
from agent_bridge.session_host import private_attachment as module
from agent_bridge.session_host.private_attachment import AttachmentOperation, PrivateAttachment
from test_predecessor_runtime import NativeChannel
from test_runtime_controls import publish
from test_session_host import _FakeChild


async def until(predicate) -> None:
    async def wait() -> None:
        while not predicate():
            await asyncio.sleep(0.001)
    await asyncio.wait_for(wait(), 1)


def control(owner, channel, endpoint, operation, *, snapshot=None, deadline=None):
    grant = owner.register(
        channel.peer, endpoint, operation, deadline or Deadline(2), snapshot=snapshot,
    )
    return owner.execute(channel, {"grant": grant})


async def staged_connection(owner, host, channel, endpoint, *, snapshot=None, deadline=None):
    result = control(
        owner, channel, endpoint, AttachmentOperation.RESERVE,
        snapshot=snapshot, deadline=deadline,
    )
    reader, writer = await asyncio.open_connection(
        "127.0.0.1", host._server.sockets[0].getsockname()[1],
    )
    await protocol.write_message(writer, protocol.MsgType.ATTACH_PRIVATE, result["ticket"].encode())
    await until(lambda: owner._stage.front is not None)
    assert host._front is owner._owned_front
    return reader, writer


def test_ticket_timeout_catches_distinct_pre_311_asyncio_exception(tmp_path, monkeypatch):
    class LegacyTimeout(Exception):
        pass

    async def expire(operation, *, timeout):
        operation.close()
        raise LegacyTimeout()

    async def scenario():
        host, owner, old, _new, endpoint, _new_ep = setup(tmp_path, monkeypatch)
        await host.serve()
        try:
            control(owner, old, endpoint, AttachmentOperation.RESERVE)
            with monkeypatch.context() as patch:
                patch.setattr(asyncio, "TimeoutError", LegacyTimeout)
                patch.setattr(asyncio, "wait_for", expire)
                with pytest.raises(PrivateIPCError, match="private-ipc-protocol-refused"):
                    await owner.read_ticket(asyncio.StreamReader())
            assert host.child_alive and host._front is None and owner._owner is None
        finally:
            await host.close()

    asyncio.run(scenario())


def setup(tmp_path, monkeypatch, *, acp_wire_boundary=False):
    monkeypatch.setattr(module, "_native_channel_type", lambda: NativeChannel)
    host = SessionHost(_FakeChild(), unexpected_reap_seconds=0)
    current = module._self()
    old = PeerIdentity(current.pid + 1000, current.start_time, current.principal)
    new = replace(old, pid=old.pid + 1)
    old_endpoint = Endpoint("127.0.0.1", 12345, old.pid, "test", 1, old.start_time)
    new_endpoint = replace(old_endpoint, pid=new.pid, generation=2)
    owner = PrivateAttachment(
        host, tmp_path, "a" * 64, "synthetic-session", acp_wire_boundary=acp_wire_boundary,
    )
    publish(tmp_path, old_endpoint)
    return host, owner, NativeChannel(old), NativeChannel(new), old_endpoint, new_endpoint


@pytest.mark.parametrize("progress", ["pending", "partial", "notification", "settled"])
def test_native_acp_capture_refuses_unsupported_live_wire_state(tmp_path, monkeypatch, progress):
    async def scenario():
        host, owner, old, _, old_ep, _ = setup(tmp_path, monkeypatch, acp_wire_boundary=True)
        await host.serve()
        writer = None
        try:
            reader, writer = await staged_connection(owner, host, old, old_ep)
            control(owner, old, old_ep, AttachmentOperation.ACTIVATE)
            await protocol.read_message(reader)
            initial = control(owner, old, old_ep, AttachmentOperation.CAPTURE)["snapshot"]
            request = b'{"jsonrpc":"2.0","id":5,"method":"session/prompt","params":{}}\n'
            sent = request[:17] if progress == "partial" else request
            await protocol.write_message(writer, protocol.MsgType.WRITE, sent)
            await until(lambda: host._child.stdin.buffer == sent)
            if progress in ("notification", "settled"):
                response = (
                    b'{"jsonrpc":"2.0","method":"session/update","params":{}}\n'
                    if progress == "notification" else b'{"jsonrpc":"2.0","id":5,"result":{}}\n'
                )
                host._child.stdout.feed_data(response)
                assert (await protocol.read_message(reader))[0] is protocol.MsgType.FRAME
                await protocol.write_message(writer, protocol.MsgType.ACK, protocol.pack_u64(1))
                await until(lambda: host.ack_cursor == 1)
            if progress == "settled":
                captured = control(owner, old, old_ep, AttachmentOperation.CAPTURE)
                assert captured["snapshot"] != initial
                assert owner._snapshot_acp.next_client_request_id == 6
            else:
                with pytest.raises(PrivateIPCError):
                    control(owner, old, old_ep, AttachmentOperation.CAPTURE)
                assert owner._snapshot == initial
            assert host.child_alive and host.child_pid == 4242
            assert host._child.stdin.buffer == sent
        finally:
            await host.close()
            if writer is not None:
                writer.close()
                await writer.wait_closed()
    asyncio.run(scenario())


@pytest.mark.parametrize("gate", ["reserve", "activate"])
def test_native_acp_progress_after_capture_refuses_even_after_rpc_settles(tmp_path, monkeypatch, gate):
    async def scenario():
        host, owner, old, new, old_ep, new_ep = setup(
            tmp_path, monkeypatch, acp_wire_boundary=True,
        )
        await host.serve()
        writers = []
        try:
            reader, writer = await staged_connection(owner, host, old, old_ep)
            writers.append(writer)
            control(owner, old, old_ep, AttachmentOperation.ACTIVATE)
            await protocol.read_message(reader)
            captured = control(owner, old, old_ep, AttachmentOperation.CAPTURE)["snapshot"]
            old.api.signalled = True
            publish(tmp_path, new_ep, old_ep)
            if gate == "activate":
                _, new_writer = await staged_connection(owner, host, new, new_ep, snapshot=captured)
                writers.append(new_writer)
            # A pending outbound notification can complete while the original
            # owner's exact-exit evidence is already available.
            host._child.stdout.feed_data(
                b'{"jsonrpc":"2.0","method":"session/update","params":{}}\n',
            )
            await until(lambda: host.max_seq == 1)
            host._on_ack(1)
            with pytest.raises(PrivateIPCError):
                control(
                    owner, new, new_ep,
                    AttachmentOperation.RESERVE if gate == "reserve" else AttachmentOperation.ACTIVATE,
                    snapshot=captured,
                )
            assert owner._generation == 1 and host.child_alive
        finally:
            await host.close()
            for writer in writers:
                writer.close()
                await writer.wait_closed()
    asyncio.run(scenario())


def test_native_acp_unchanged_idle_stamp_survives_committed_owner_exit(tmp_path, monkeypatch):
    async def scenario():
        host, owner, old, new, old_ep, new_ep = setup(
            tmp_path, monkeypatch, acp_wire_boundary=True,
        )
        await host.serve()
        writers = []
        try:
            reader, writer = await staged_connection(owner, host, old, old_ep)
            writers.append(writer)
            control(owner, old, old_ep, AttachmentOperation.ACTIVATE)
            await protocol.read_message(reader)
            # Initial recovery custody alone cannot restore an ACP client.
            assert owner._snapshot_acp is None
            await protocol.write_message(
                writer, protocol.MsgType.WRITE,
                b'{"jsonrpc":"2.0","id":4,"method":"initialize","params":{}}\n',
            )
            await until(lambda: b"initialize" in host._child.stdin.buffer)
            host._child.stdout.feed_data(b'{"jsonrpc":"2.0","id":4,"result":{}}\n')
            assert (await protocol.read_message(reader))[0] is protocol.MsgType.FRAME
            await protocol.write_message(writer, protocol.MsgType.ACK, protocol.pack_u64(1))
            await until(lambda: host.ack_cursor == 1)
            captured = control(owner, old, old_ep, AttachmentOperation.CAPTURE)["snapshot"]
            stamp = owner._snapshot_acp
            old.api.signalled = True
            publish(tmp_path, new_ep, old_ep)
            new_reader, new_writer = await staged_connection(
                owner, host, new, new_ep, snapshot=captured,
            )
            writers.append(new_writer)
            recovery = owner._stage.recovery_snapshot
            with pytest.raises(PrivateIPCError):
                control(owner, new, new_ep, AttachmentOperation.INSPECT_COMMIT, snapshot=recovery)
            result = control(owner, new, new_ep, AttachmentOperation.ACTIVATE, snapshot=captured)
            assert result["ack_cursor"] == 1 and result["attachment_generation"] == 2
            assert (await protocol.read_message(new_reader))[0] is protocol.MsgType.HELLO
            assert owner._snapshot == recovery and owner._snapshot_acp == stamp
            committed = control(
                owner, new, new_ep, AttachmentOperation.INSPECT_COMMIT, snapshot=recovery,
            )
            assert committed["snapshot"] == recovery
            assert committed["attachment_generation"] == 2
            assert owner._snapshot == recovery and owner._snapshot_acp == stamp
            with pytest.raises(PrivateIPCError):
                control(owner, new, new_ep, AttachmentOperation.INSPECT_COMMIT, snapshot=captured)
            assert stamp.next_client_request_id == 5 and host.child_pid == 4242
            assert host._child.stdin.buffer.count(b"initialize") == 1
            control(owner, new, new_ep, AttachmentOperation.CAPTURE)
            with pytest.raises(PrivateIPCError):
                control(owner, new, new_ep, AttachmentOperation.INSPECT_COMMIT, snapshot=recovery)
        finally:
            await host.close()
            for writer in writers:
                writer.close()
                await writer.wait_closed()
    asyncio.run(scenario())


def test_fresh_native_grants_exact_exit_and_host_cursor_preserve_live_child(tmp_path, monkeypatch):
    async def scenario():
        host, owner, old, new, old_ep, new_ep = setup(tmp_path, monkeypatch)
        await host.serve()
        writers = []
        try:
            old_reader, old_writer = await staged_connection(owner, host, old, old_ep)
            writers.append(old_writer)
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(old_reader.read(1), 0.01)
            result = control(owner, old, old_ep, AttachmentOperation.ACTIVATE)
            assert result == {"attached": True, "attachment_generation": 1, "ack_cursor": 0}
            assert (await protocol.read_message(old_reader))[0] is protocol.MsgType.HELLO
            host._child.stdout.feed_data(b'{"first":1}\n')
            frame = await protocol.read_message(old_reader)
            assert protocol.unpack_frame(frame[1]) == (1, b'{"first":1}\n')
            await protocol.write_message(old_writer, protocol.MsgType.ACK, protocol.pack_u64(1))
            await until(lambda: host.ack_cursor == 1)
            snapshot = control(owner, old, old_ep, AttachmentOperation.CAPTURE)
            assert snapshot["ack_cursor"] == snapshot["max_seq"] == 1
            assert snapshot["attachment_generation"] == 1
            captured_front = host._front
            publish(tmp_path, new_ep, old_ep)
            with pytest.raises(PrivateIPCError):
                control(owner, new, new_ep, AttachmentOperation.RESERVE, snapshot=snapshot["snapshot"])
            assert host._front is captured_front and not old_writer.is_closing()
            # Closing the native channel alone is not exit evidence.
            old.close()
            with pytest.raises(PrivateIPCError):
                control(owner, new, new_ep, AttachmentOperation.RESERVE, snapshot=snapshot["snapshot"])
            old.api.signalled = True
            new_reader, new_writer = await staged_connection(
                owner, host, new, new_ep, snapshot=snapshot["snapshot"],
            )
            writers.append(new_writer)
            host._child.stdout.feed_data(b'{"second":2}\n')
            await until(lambda: host.max_seq == 2)
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(new_reader.read(1), 0.01)
            result = control(
                owner, new, new_ep, AttachmentOperation.ACTIVATE, snapshot=snapshot["snapshot"],
            )
            assert result == {"attached": True, "attachment_generation": 2, "ack_cursor": 1}
            assert (await protocol.read_message(new_reader))[0] is protocol.MsgType.HELLO
            frame = await protocol.read_message(new_reader)
            assert protocol.unpack_frame(frame[1]) == (2, b'{"second":2}\n')
            await protocol.write_message(new_writer, protocol.MsgType.WRITE, b"unchanged-acp\n")
            await until(lambda: host._child.stdin.buffer == b"unchanged-acp\n")
            assert host.child_alive and host.child_pid == 4242
            assert old.api.closed == 1 and new.api.closed == 0
            assert owner._generation == 2 and host._front is owner._owned_front
        finally:
            await host.close()
            for writer in writers:
                writer.close()
                await writer.wait_closed()
        assert new.api.closed == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("state", ["prepared", "discarded", "recaptured"])
def test_planned_recovery_token_requires_exact_committed_stage(tmp_path, monkeypatch, state):
    async def scenario():
        host, owner, old, new, old_ep, new_ep = setup(tmp_path, monkeypatch)
        await host.serve()
        writers = []
        try:
            reservation = control(owner, old, old_ep, AttachmentOperation.RESERVE)
            assert reservation["recovery_generation"] == 1
            recovery = reservation["recovery_snapshot"]
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", host._server.sockets[0].getsockname()[1],
            )
            writers.append(writer)
            await protocol.write_message(
                writer, protocol.MsgType.ATTACH_PRIVATE, reservation["ticket"].encode(),
            )
            await until(lambda: owner._stage.front is not None)
            if state == "discarded":
                owner._discard_stage()
            elif state == "recaptured":
                control(owner, old, old_ep, AttachmentOperation.ACTIVATE)
                assert (await protocol.read_message(reader))[0] is protocol.MsgType.HELLO
                captured = control(owner, old, old_ep, AttachmentOperation.CAPTURE)
                assert captured["snapshot"] != recovery
                old.api.signalled = True
            publish(tmp_path, new_ep, old_ep)
            with pytest.raises(PrivateIPCError):
                control(owner, new, new_ep, AttachmentOperation.RESERVE, snapshot=recovery)
            assert owner._generation == (1 if state == "recaptured" else 0)
            assert host.child_alive
        finally:
            await host.close()
            for writer in writers:
                writer.close()
                await writer.wait_closed()
    asyncio.run(scenario())


@pytest.mark.parametrize("case", [
    "route", "closed-native", "wrong-peer", "expired-grant", "expired-reservation",
    "foreign-front", "cancelled", "snapshot", "generation",
    "config", "host-generation", "listener",
])
def test_refusals_never_replace_owner_or_forward_acp(tmp_path, monkeypatch, case):
    async def scenario():
        host, owner, old, new, old_ep, new_ep = setup(tmp_path, monkeypatch)
        await host.serve()
        writers = []
        try:
            reader, writer = await staged_connection(owner, host, old, old_ep)
            writers.append(writer)
            control(owner, old, old_ep, AttachmentOperation.ACTIVATE)
            await protocol.read_message(reader)
            snapshot = control(owner, old, old_ep, AttachmentOperation.CAPTURE)["snapshot"]
            old.api.signalled = True
            publish(tmp_path, new_ep, old_ep)
            staged_reader, staged_writer = await staged_connection(
                owner, host, new, new_ep, snapshot=snapshot,
            )
            writers.append(staged_writer)
            original = owner._owned_front
            deadline = Deadline(1)
            token = owner.register(
                new.peer, new_ep, AttachmentOperation.ACTIVATE, deadline, snapshot=snapshot,
            )
            if case == "route":
                publish(tmp_path, replace(new_ep, generation=3), new_ep)
            elif case == "closed-native":
                new.close()
            elif case == "wrong-peer":
                new.peer = old.peer
            elif case == "expired-grant":
                deadline._expires = 0
            elif case == "expired-reservation":
                owner._stage.deadline._expires = 0
            elif case == "foreign-front":
                host._front = object()
            elif case == "cancelled":
                owner._stage.front.closed = True
            elif case == "snapshot":
                owner._snapshot = "changed"
            elif case == "generation":
                owner._stage.endpoint = replace(new_ep, generation=99)
            elif case == "config":
                owner._identity = "c" * 64
            elif case == "host-generation":
                owner._generation += 1
            else:
                owner._server = object()
            with pytest.raises(PrivateIPCError):
                owner.execute(new, {"grant": token})
            with pytest.raises(PrivateIPCError):
                owner.execute(new, {"grant": token})
            assert owner._owned_front is original and owner._generation == (2 if case == "host-generation" else 1)
            assert host._child.stdin.buffer == b""
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(staged_reader.read(1), 0.01)
        finally:
            host._front = owner._owned_front
            await host.close()
            for writer in writers:
                writer.close()
                await writer.wait_closed()
    asyncio.run(scenario())


def test_graph_refusal_consumes_grant_even_if_graph_is_later_restored(tmp_path, monkeypatch):
    async def scenario():
        host, owner, old, _new, endpoint, _new_ep = setup(tmp_path, monkeypatch)
        await host.serve()
        try:
            token = owner.register(old.peer, endpoint, AttachmentOperation.RESERVE, Deadline(1))
            identity = owner._identity
            owner._identity = "c" * 64
            with pytest.raises(PrivateIPCError):
                owner.execute(old, {"grant": token})
            owner._identity = identity
            with pytest.raises(PrivateIPCError):
                owner.execute(old, {"grant": token})
            assert owner._stage is None and old.retained == 0
        finally:
            await host.close()
    asyncio.run(scenario())


@pytest.mark.parametrize(("field", "value"), [
    ("pid", True), ("process_start_time", "0"), ("generation", True),
    ("generation", 0), ("port", True), ("port", 65536), ("version", None), ("bind", ""),
])
def test_invalid_independent_registration_never_duplicates_native_reference(
    tmp_path, monkeypatch, field, value,
):
    async def scenario():
        host, owner, old, _new, endpoint, _new_ep = setup(tmp_path, monkeypatch)
        await host.serve()
        try:
            with pytest.raises(PrivateIPCError):
                owner.register(
                    old.peer, replace(endpoint, **{field: value}),
                    AttachmentOperation.RESERVE, Deadline(1),
                )
            assert owner._stage is None and old.retained == 0
        finally:
            await host.close()
    asyncio.run(scenario())


def test_stage_expiry_releases_only_owned_connection_and_native_reference(tmp_path, monkeypatch):
    async def scenario():
        host, owner, old, _new, endpoint, _new_ep = setup(tmp_path, monkeypatch)
        await host.serve()
        try:
            reader, writer = await staged_connection(
                owner, host, old, endpoint, deadline=Deadline(0.05),
            )
            await until(lambda: owner._stage is None)
            assert await reader.read() == b""
            assert old.api.closed == 1 and owner._owner is None and host._front is None
            assert host.child_alive
            writer.close()
            await writer.wait_closed()
        finally:
            await host.close()
    asyncio.run(scenario())


def test_legacy_attachment_history_refuses_retrofit_even_after_disconnect(tmp_path):
    async def scenario():
        host = SessionHost(_FakeChild(), unexpected_reap_seconds=0)
        port = await host.serve()
        try:
            client = await SessionHostClient.connect(port=port)
            await client.attach()
            await client.close()
            await until(lambda: host._front is None)
            assert host._attachment_epoch == 1
            with pytest.raises(PrivateIPCError):
                PrivateAttachment(host, tmp_path, "a" * 64, "synthetic-session")
            assert host._private_attachment is None and host.child_alive
        finally:
            await host.close()
    asyncio.run(scenario())


def test_disconnected_prepared_front_cannot_activate_or_mutate_child(tmp_path, monkeypatch):
    async def scenario():
        host, owner, old, _new, endpoint, _new_ep = setup(tmp_path, monkeypatch)
        await host.serve()
        try:
            _reader, writer = await staged_connection(owner, host, old, endpoint)
            writer.close()
            await writer.wait_closed()
            await until(lambda: owner._stage.front.reader.at_eof())
            with pytest.raises(PrivateIPCError):
                control(owner, old, endpoint, AttachmentOperation.ACTIVATE)
            assert host._front is None and host.child_alive and owner._generation == 0
        finally:
            await host.close()
    asyncio.run(scenario())


def test_expired_handshake_budget_refuses_before_creating_read_coroutine(tmp_path, monkeypatch):
    async def scenario():
        host, owner, old, _new, endpoint, _new_ep = setup(tmp_path, monkeypatch)
        await host.serve()
        try:
            control(owner, old, endpoint, AttachmentOperation.RESERVE)
            owner._stage.deadline._expires = 0
            with warnings.catch_warnings(record=True) as seen:
                warnings.simplefilter("always")
                with pytest.raises(PrivateIPCError):
                    await owner.read_ticket(asyncio.StreamReader())
            assert not [warning for warning in seen if warning.category is RuntimeWarning]
            assert host._front is None and host.child_alive
        finally:
            await host.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("payload", [b"bad", b"A" + b"x" * 64, b"P" + b"x" * 65])
def test_opt_in_host_refuses_legacy_wrong_ticket_and_oversized_handshake(tmp_path, monkeypatch, payload):
    async def scenario():
        host, owner, old, _new, endpoint, _new_ep = setup(tmp_path, monkeypatch)
        await host.serve()
        try:
            control(owner, old, endpoint, AttachmentOperation.RESERVE)
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", host._server.sockets[0].getsockname()[1],
            )
            writer.write(protocol._U32.pack(len(payload)) + payload)
            await writer.drain()
            assert await asyncio.wait_for(reader.read(), 1) == b""
            assert owner._stage.front is None and host._front is None and host.child_alive
            writer.close()
            await writer.wait_closed()
        finally:
            await host.close()
    asyncio.run(scenario())

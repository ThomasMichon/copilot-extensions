"""Exact-port staging has real sockets but no native adoption authority."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from credential_relay.server import CredentialRelayServer
from zdd.private_protocol import Deadline, PrivateIPCError, Refusal

from agent_bridge.staged_relay import StagedRelay


@contextmanager
def port() -> Iterator[int]:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        yield sock.getsockname()[1]


def free_port() -> int:
    with port() as value:
        return value


async def ping(value: int) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", value)
    try:
        writer.write(b"ping\n\n")
        await writer.drain()
        try:
            return await asyncio.wait_for(reader.read(), timeout=1)
        except (ConnectionResetError, BrokenPipeError):
            return b""
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionResetError, BrokenPipeError):
            pass


def test_staged_relay_real_listener_denies_until_commit_and_keeps_exact_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        relay = CredentialRelayServer(port=free_port())
        original_port = relay.port

        async def forbidden() -> None:
            pytest.fail("ordinary startup can evict or choose another port")

        monkeypatch.setattr(relay, "start", forbidden)
        stage = StagedRelay(relay, Deadline(3))
        try:
            assert not relay.running
            with pytest.raises(OSError), socket.socket() as rival:
                rival.bind(("127.0.0.1", relay.port))
            with pytest.raises(OSError), socket.socket() as rival:
                rival.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                rival.bind(("127.0.0.1", relay.port))
                rival.listen()
            with pytest.raises(PrivateIPCError):
                stage.activate(Deadline(1))
            await stage.prepare()
            assert await ping(original_port) == b""
            assert relay.stats.total_requests == 0
            assert not relay.running
            assert relay.port == original_port
            stage.activate(Deadline(1))
            assert relay.running and stage.active
            assert await ping(original_port) == b"pong\n\n"
            with pytest.raises(PrivateIPCError):
                stage.activate(Deadline(1))
            with pytest.raises(PrivateIPCError):
                await stage.prepare()
            stage.verify()
        finally:
            await stage.wait_closed(Deadline(1))
        assert not stage.active and not relay.running
        assert stage._transport.sockets == ()
        with socket.socket() as probe:
            probe.settimeout(1)
            assert probe.connect_ex(("127.0.0.1", original_port)) != 0

    asyncio.run(scenario())


@pytest.mark.parametrize("value", [0, True, -1, 65536, "12345"])
def test_invalid_capture_port_never_reserves(value: object) -> None:
    async def scenario() -> None:
        relay = CredentialRelayServer()
        relay.port = value
        with pytest.raises(PrivateIPCError) as caught:
            StagedRelay(relay, Deadline(1))
        assert caught.value.reason is Refusal.AUTHORITY
        assert relay._server is None

    asyncio.run(scenario())


def test_occupied_port_is_not_evicted_retried_or_changed() -> None:
    async def scenario(value: int) -> None:
        relay = CredentialRelayServer(port=value)
        with pytest.raises(PrivateIPCError) as caught:
            StagedRelay(relay, Deadline(1))
        assert caught.value.reason is Refusal.AUTHORITY
        assert relay.port == value and relay._server is None

    with port() as value:
        asyncio.run(scenario(value))


def test_foreign_thread_cannot_release_owner_reservation() -> None:
    async def scenario() -> None:
        relay = CredentialRelayServer(port=free_port())
        stage = StagedRelay(relay, Deadline(2))
        try:
            with pytest.raises(PrivateIPCError):
                await asyncio.to_thread(stage.close)
            assert not stage._closed
            stage.verify()
        finally:
            await stage.wait_closed(Deadline(1))

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["before", "prepare", "commit"])
def test_original_deadline_not_renewed_and_failed_prepare_releases_owned_port(
    phase: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        relay = CredentialRelayServer(port=free_port())
        deadline = Deadline(2)
        if phase == "before":
            deadline._expires = 0
            with pytest.raises(PrivateIPCError) as caught:
                StagedRelay(relay, deadline)
            assert caught.value.reason is Refusal.DEADLINE
            return
        stage = StagedRelay(relay, deadline)
        try:
            if phase == "prepare":
                deadline._expires = 0
                with pytest.raises(PrivateIPCError) as caught:
                    await stage.prepare()
                assert caught.value.reason is Refusal.DEADLINE
                assert stage._closed
            else:
                await stage.prepare()
                deadline._expires = 0
                with pytest.raises(PrivateIPCError) as caught:
                    stage.activate(Deadline(100))
                assert caught.value.reason is Refusal.DEADLINE
                assert await ping(relay.port) == b""
            assert not relay.running
        finally:
            await stage.wait_closed(Deadline(1))
        assert relay._server is None

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["io", "cancel", "late"])
def test_async_transport_failure_closes_owned_socket_without_admission(
    failure: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        relay = CredentialRelayServer(port=free_port())
        deadline = Deadline(1)
        stage = StagedRelay(relay, deadline)
        original = asyncio.start_server

        async def fail(*args: object, **kwargs: object) -> asyncio.Server:
            if failure == "cancel":
                raise asyncio.CancelledError()
            if failure == "late":
                result = await original(*args, **kwargs)
                deadline._expires = 0
                return result
            raise OSError("fixture-sensitive-message")

        monkeypatch.setattr(asyncio, "start_server", fail)
        expected = asyncio.CancelledError if failure == "cancel" else PrivateIPCError
        with pytest.raises(expected) as caught:
            await stage.prepare()
        assert "fixture-sensitive-message" not in str(caught.value)
        assert stage._closed and not relay.running
        await stage.wait_closed(Deadline(1))
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", relay.port))

    asyncio.run(scenario())


@pytest.mark.parametrize("drift", ["port", "source", "owner", "transport", "handler"])
def test_commit_refuses_changed_captured_resource_or_graph(drift: str) -> None:
    async def scenario() -> None:
        relay = CredentialRelayServer(port=free_port())
        stage = StagedRelay(relay, Deadline(2))
        foreign = None
        try:
            await stage.prepare()
            if drift == "port":
                relay.port += 1
            elif drift == "source":
                relay.policy.allowed_hosts.append("changed.example")
            elif drift == "owner":
                foreign = await asyncio.start_server(
                    lambda _reader, writer: writer.close(), "127.0.0.1", 0,
                )
                relay._server = foreign
            elif drift == "transport":
                stage._transport.close()
            else:
                relay._handle_client = lambda *_args: None
            with pytest.raises(PrivateIPCError):
                stage.activate(Deadline(1))
            assert not stage.active
        finally:
            await stage.wait_closed(Deadline(1))
            if foreign is not None:
                assert relay._server is foreign and foreign.is_serving()
                foreign.close()
                await foreign.wait_closed()

    asyncio.run(scenario())

"""Real attachment/response regressions and deterministic reconnect accounting."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

from aiohttp import WSMsgType
import pytest

from fleet_manager.gateway_service_channel import (
    BODY_BYTES, Channel, TransportError, encode_frame,
)
from fleet_manager.gateway_service_connector import ServiceConnector
from fleet_manager.gateway_service_server import STATE, Ticket, start
from gateway_service_transport_fixtures import eventually, running


def test_abandoned_replacement_ticket_keeps_healthy_channel(tmp_path) -> None:
    async def scenario() -> None:
        async with running(tmp_path) as rig:
            connector = await rig.connect()
            active = rig.state.channels["service-a"]
            token = await connector._admit(rig.session)
            async with await rig.request() as response:
                assert response.status == 200
            pending = rig.state.tickets[token]
            rig.state.tickets[token] = Ticket(pending.candidate, 0)
            await rig.state.sweep()
            assert token not in rig.state.tickets
            assert rig.state.channels["service-a"] is active and not active.closed
            async with await rig.request() as response:
                assert response.status == 200
    asyncio.run(scenario())


def test_replacement_confirms_before_routing_while_predecessor_close_waits(
    tmp_path, monkeypatch,
) -> None:
    async def scenario() -> None:
        async with running(tmp_path) as rig:
            await rig.connect()
            predecessor = rig.state.channels["service-a"]
            close_entered, release_close = asyncio.Event(), asyncio.Event()
            close = Channel.close

            async def delayed_close(channel, code="channel-disconnected") -> None:
                if channel is predecessor and code == "channel-replaced":
                    close_entered.set()
                    await release_close.wait()
                await close(channel, code)

            monkeypatch.setattr(Channel, "close", delayed_close)
            pending = None
            try:
                replacement = await rig.connect()
                await asyncio.wait_for(close_entered.wait(), 3)
                assert replacement.connected.is_set()
                pending = asyncio.create_task(rig.request())
                await eventually(lambda: len(rig.backends[0].requests) == 1)
                assert rig.state.channels["service-a"] is not predecessor
                release_close.set()
                async with await pending as response:
                    assert response.status == 200
            finally:
                release_close.set()
                if pending is not None:
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
    asyncio.run(scenario())


def test_combined_frame_limit_is_known_before_mutation_dispatch(tmp_path) -> None:
    async def scenario() -> None:
        async with running(tmp_path) as rig:
            await rig.connect()
            async with await rig.request(
                "/api/echo", "POST", data=b"x" * BODY_BYTES,
                headers={
                    "Accept": '"' * 4096,
                    "Accept-Language": '"' * 4096,
                    "Content-Type": '"' * 4096,
                },
            ) as response:
                assert response.status == 413
                assert await response.json() == {"error": "frame-limit"}
            assert rig.backends[0].requests == []
            assert rig.state.pending == 0
    asyncio.run(scenario())


def test_non_streaming_asset_uses_response_not_request_limit(tmp_path) -> None:
    async def scenario() -> None:
        async with running(tmp_path) as rig:
            await rig.connect(1)
            async with await rig.request("/assets/asset.js") as response:
                assert response.status == 200
                assert await response.read() == b"x" * 40000
            async with await rig.request("/assets/large") as response:
                assert response.status == 502
                assert await response.json() == {"error": "response-limit"}
    asyncio.run(scenario())


def test_expected_connected_and_invalid_reload_status_is_truthful(tmp_path) -> None:
    async def scenario() -> None:
        async with running(tmp_path) as rig:
            before = rig.state.status()
            assert before.revision == 1
            assert all(role.state == "unavailable" for role in before.roles)
            await rig.connect()
            connected = rig.state.status()
            assert connected.roles[0].state == "ready"
            assert connected.roles[1].state == "unavailable"
            rig.policy.write_bytes(b"invalid")
            await eventually(lambda: rig.state.status().last_error == "invalid-json")
            assert rig.state.status().revision == 1
            async with await rig.request() as response:
                assert response.status == 200
            rig.state.stopping = True
            assert all(role.state == "unavailable" for role in rig.state.status().roles)
            async with await rig.request() as response:
                assert response.status == 503
                assert await response.json() == {"error": "gateway-unavailable"}
    asyncio.run(scenario())


def test_real_connector_reconnects_to_replaced_gateway_without_replaying_requests(tmp_path) -> None:
    async def scenario() -> None:
        async with running(tmp_path) as rig:
            async def access() -> str:
                return rig.access("service-a", 1)

            connector = ServiceConnector(
                slot="service-a", gateway_origin=rig.origin,
                backend_origin=rig.origins[0], access_token_callback=access,
                proof_key=rig.h.keys[1].get_op_key("sign"), gateway_ca=rig.ca)
            stop = asyncio.Event()
            task = asyncio.create_task(connector.run(stop))
            rig.tasks.append(task)
            await asyncio.wait_for(connector.connected.wait(), 3)
            async with await rig.request() as response:
                assert response.status == 200
            previous = rig.state
            await rig.serving.close()
            listener = replace(
                rig.listener, port=urlsplit(rig.origin).port, origin=rig.origin)
            rig.serving = await start(
                listener, rig.policy, reload_interval=0.05, clock=rig.h.clock)
            rig.state = rig.serving.app[STATE]
            rig.nonce = None
            try:
                await eventually(
                    lambda: "service-a" in rig.state.channels and connector.connected.is_set())
                assert rig.state is not previous
                assert connector.connected.is_set()
                assert len(rig.backends[0].requests) == 1
                async with await rig.request() as response:
                    assert response.status == 200
                assert len(rig.backends[0].requests) == 2
            finally:
                stop.set()
                await asyncio.wait_for(task, 3)
    asyncio.run(scenario())


def test_long_pre_attachment_failures_do_not_reset_retry_budget(monkeypatch) -> None:
    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        sampled = [loop.time()]
        original_time = loop.time
        calls = []

        async def access() -> str:
            return "synthetic-unused"

        connector = ServiceConnector(
            slot="example", gateway_origin="https://gateway.example",
            backend_origin="http://127.0.0.1", access_token_callback=access,
            proof_key=None)

        async def failed_admission() -> None:
            calls.append(True)
            assert len(calls) <= 5
            sampled[0] += 61
            raise TransportError("admission-unavailable")

        async def no_backoff(awaitable, timeout) -> None:
            awaitable.close()
            raise asyncio.TimeoutError

        monkeypatch.setattr(connector, "_connect", failed_admission)
        monkeypatch.setattr(asyncio, "wait_for", no_backoff)
        monkeypatch.setattr(loop, "time", lambda: sampled[0])
        try:
            with pytest.raises(TransportError, match="^reconnect-exhausted$"):
                await connector.run(asyncio.Event())
        finally:
            monkeypatch.setattr(loop, "time", original_time)
        assert len(calls) == 5
    asyncio.run(scenario())


def test_buffered_cancels_retire_workers_that_never_start(monkeypatch) -> None:
    async def scenario() -> None:
        connector = ServiceConnector(
            slot="example", gateway_origin="https://gateway.example",
            backend_origin="http://127.0.0.1", access_token_callback=AsyncMock(),
            proof_key=None)
        correlations = [f"{number:048x}" for number in range(9)]
        frames = []
        for correlation in correlations:
            frames.extend([
                {"type": "request", "id": correlation, "method": "GET",
                 "target": "/", "headers": {}, "body": "", "timeout": 1},
                {"type": "cancel", "id": correlation},
            ])

        class BufferedSocket:
            async def send_str(self, value):
                pass

            async def receive(self, **kwargs):
                return SimpleNamespace(type=WSMsgType.TEXT, data='{"type":"attached"}')

            async def close(self, **kwargs):
                return True

            def __aiter__(self):
                return self

            async def __anext__(self):
                # No scheduling point: each cancel is already buffered behind its request.
                if not frames:
                    raise StopAsyncIteration
                return SimpleNamespace(type=WSMsgType.TEXT, data=encode_frame(frames.pop(0)))

        @asynccontextmanager
        async def socket_context(*args, **kwargs):
            yield BufferedSocket()

        @asynccontextmanager
        async def session_context(**kwargs):
            yield SimpleNamespace(ws_connect=socket_context)

        monkeypatch.setattr("fleet_manager.gateway_service_connector.aiohttp.ClientSession",
                            session_context)
        monkeypatch.setattr("fleet_manager.gateway_service_connector.aiohttp.TCPConnector",
                            lambda **kwargs: None)
        monkeypatch.setattr(connector, "_admit", AsyncMock(return_value="synthetic-ticket"))
        proxy = AsyncMock()
        monkeypatch.setattr(connector, "_proxy", proxy)
        await connector.connect_once()
        proxy.assert_not_awaited()
        assert connector.active == {}
        assert list(connector.retired) == correlations
    asyncio.run(scenario())


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
def test_backend_disconnect_never_implicitly_replays_mutation(tmp_path, method) -> None:
    async def scenario() -> None:
        async with running(tmp_path) as rig:
            rig.h.document["manifest"]["slots"][0]["routes"][0]["methods"].append(method)
            rig.h.document["callers"][0]["methods"].append(method)
            await rig.update()
            await rig.connect()
            async with await rig.request(
                "/api/lost-mutation", method, data=b"accepted-once",
            ) as response:
                assert response.status == 502
                assert await response.json() == {"error": "outcome-unknown"}
            assert rig.backends[0].mutations == 1
            assert len(rig.backends[0].requests) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["reload", "shutdown"])
def test_stalled_close_drain_cannot_block_reload_or_shutdown(
    tmp_path, monkeypatch, caplog, operation,
) -> None:
    async def scenario() -> None:
        async with running(tmp_path) as rig:
            await rig.connect()
            channel = rig.state.channels["service-a"]
            writer = channel.ws._payload_writer
            drain = type(writer).drain
            stalled, canceled = asyncio.Event(), asyncio.Event()

            async def stalled_drain(selected):
                if selected is writer:
                    stalled.set()
                    try:
                        await asyncio.Future()
                    finally:
                        canceled.set()
                else:
                    await drain(selected)

            monkeypatch.setattr(type(writer), "drain", stalled_drain)
            monkeypatch.setattr("fleet_manager.gateway_service_channel.SEND_TIMEOUT", 0.1)
            if operation == "reload":
                rig.h.document["manifest"]["slots"][0]["role"] = "assets"
                await rig.update()
                await asyncio.wait_for(stalled.wait(), 1)
                await rig.update()
                assert rig.state.context.manifest.revision == 3
            else:
                await asyncio.wait_for(rig.serving.close(), 2)
                assert stalled.is_set()
            await asyncio.wait_for(canceled.wait(), 1)
            assert channel.closed and channel.ws.closed
            assert "service-channel-closed code=close-timeout" in caplog.text
    asyncio.run(scenario())

"""Real response streaming, downstream backpressure, cancellation and fencing."""

import asyncio
import copy
import socket

import aiohttp
from aiohttp import web
from joserfc.jwk import ECKey
import pytest

from fleet_manager.gateway_service_channel import (
    CHUNK_BYTES, GLOBAL_REQUESTS, QUEUE_SIZE, RESPONSE_BYTES,
)
from fleet_manager.gateway_service_connector import ServiceConnector, verified_tls
from fleet_manager.gateway_service_server import STATE
from fleet_manager import gateway_service_server as server
from gateway_service_transport_fixtures import eventually, running


def test_genuine_sse_first_chunk_arrives_before_backend_finishes(tmp_path):
    async def scenario():
        async with running(tmp_path) as rig:
            connector = await rig.connect()
            async with await rig.request("/api/events") as response:
                assert response.status == 200
                assert await response.content.readexactly(13) == b"data: first\n\n"
                assert not rig.backends[0].stopped.is_set()
                assert rig.state.pending == len(connector.active) == 1
                await rig.backends[0].stream.put(b"data: second\n\n")
                assert await response.content.readexactly(14) == b"data: second\n\n"
                await rig.backends[0].stream.put(None)
                assert await response.read() == b""
            await eventually(lambda: not connector.active and rig.state.pending == 0)
    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["slot", "caller", "trust", "expiry"])
def test_no_post_revocation_chunks_or_successful_eof(tmp_path, change):
    async def scenario():
        async with running(tmp_path) as rig:
            await rig.connect()
            response = await rig.request("/api/events")
            try:
                assert await response.content.readexactly(13) == b"data: first\n\n"
                if change == "slot":
                    rig.h.document["manifest"]["slots"][0]["role"] = "assets"
                elif change == "caller":
                    rig.h.document["callers"] = []
                elif change == "trust":
                    keys = list(rig.h.keys)
                    keys[0] = ECKey.generate_key("P-256")
                    rig.h.keys = tuple(keys)
                    rig.h.document["identity"]["jwks"]["keys"] = [
                        {**keys[0].as_dict(private=False), "kid": "issuer-1"}]
                else:
                    rig.h.clock.value += 3600
                await rig.update()
                await rig.backends[0].stream.put(b"data: forbidden\n\n")
                with pytest.raises((aiohttp.ClientError, ConnectionError)):
                    await asyncio.wait_for(response.read(), 3)
                await eventually(lambda: rig.state.pending == 0)
            finally:
                response.close()
    asyncio.run(scenario())


def test_client_disconnect_cancels_fixed_backend_and_releases_request(tmp_path):
    async def scenario():
        async with running(tmp_path) as rig:
            connector = await rig.connect()
            response = await rig.request("/api/events")
            assert await response.content.readexactly(13) == b"data: first\n\n"
            response.close()
            await eventually(lambda: not connector.active and rig.state.pending == 0)
            await asyncio.wait_for(rig.backends[0].stopped.wait(), 3)
            assert rig.state.channels["service-a"].pending == {}
    asyncio.run(scenario())


def test_ack_timeout_aborts_prepared_stream_before_json_fallback(tmp_path, monkeypatch):
    async def scenario():
        prepare, send, json_response = web.StreamResponse.prepare, server.send, web.json_response
        transports, unsafe_fallbacks = [], []

        async def observe_prepare(response, request):
            if STATE in request.app and request.path == "/api/events":
                transports.append(request.transport)
            return await prepare(response, request)

        async def timed_out_ack(ws, frame):
            if frame.get("type") == "ack":
                raise asyncio.TimeoutError
            await send(ws, frame)

        def observe_json(value, **kwargs):
            if value.get("error") == "request-timeout" and transports:
                if not transports[0].is_closing():
                    unsafe_fallbacks.append(True)
            return json_response(value, **kwargs)

        monkeypatch.setattr(web.StreamResponse, "prepare", observe_prepare)
        monkeypatch.setattr(server, "send", timed_out_ack)
        monkeypatch.setattr(web, "json_response", observe_json)
        async with running(tmp_path) as rig:
            connector = await rig.connect()
            response = await rig.request("/api/events")
            try:
                assert response.status == 200
                with pytest.raises((aiohttp.ClientError, ConnectionError)):
                    await asyncio.wait_for(response.read(), 3)
                await eventually(lambda: not connector.active and rig.state.pending == 0)
                assert transports and transports[0].is_closing()
                assert not unsafe_fallbacks
            finally:
                response.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["caller", "slot", "trust", "expiry"])
def test_buffered_response_retains_authority_through_downstream_preparation(
    tmp_path, monkeypatch, change,
):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        prepare = web.StreamResponse.prepare

        async def pause_preparation(response, request):
            result = await prepare(response, request)
            if STATE in request.app and request.path == "/assets/index.html":
                entered.set()
                await release.wait()
            return result

        monkeypatch.setattr(web.StreamResponse, "prepare", pause_preparation)
        async with running(tmp_path) as rig:
            await rig.connect(1)
            task = asyncio.create_task(rig.request("/assets/index.html"))
            response = None
            try:
                await asyncio.wait_for(entered.wait(), 3)
                assert rig.state.pending == 1
                channel = rig.state.channels["service-b"]
                assert len(channel.pending) == 1
                if change == "caller":
                    rig.h.document["callers"] = []
                elif change == "slot":
                    rig.h.document["manifest"]["slots"][1]["role"] = "assets"
                elif change == "trust":
                    rig.h.document["identity"]["jwks"]["keys"] = [
                        {**ECKey.generate_key("P-256").as_dict(private=False),
                         "kid": "issuer-1"}]
                else:
                    rig.h.clock.value += 3600
                await rig.update()
                release.set()
                response = await asyncio.wait_for(task, 3)
                assert response.status == 200
                with pytest.raises((aiohttp.ClientError, ConnectionError)):
                    await asyncio.wait_for(response.read(), 3)
                await eventually(lambda: rig.state.pending == 0 and not channel.pending)
            finally:
                release.set()
                if response is not None:
                    response.close()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_slow_real_client_stalls_chunk_ack_and_bounds_queues(tmp_path, monkeypatch):
    prepare = web.StreamResponse.prepare

    async def small_buffers(response, request):
        if STATE in request.app and request.path == "/api/large" and request.transport is not None:
            request.transport.set_write_buffer_limits(high=16384, low=4096)
            sock = request.transport.get_extra_info("socket")
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 8192)
        return await prepare(response, request)

    monkeypatch.setattr(web.StreamResponse, "prepare", small_buffers)

    def small_receiver(address):
        family, kind, protocol, _, _ = address
        peer = socket.socket(family, kind, protocol)
        peer.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8192)
        return peer

    async def scenario():
        async with running(tmp_path) as rig:
            connector = await rig.connect()
            selected = await rig.auth("/api/large")
            client = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(
                    ssl=verified_tls(rig.ca), socket_factory=small_receiver),
                read_bufsize=1024, trust_env=False)
            response = await client.get(rig.origin + "/api/large", headers=selected)
            try:
                await eventually(lambda: bool(connector.active))
                exchange = next(iter(connector.active.values()))
                channel = rig.state.channels["service-a"]
                await eventually(lambda: exchange.ack is not None and not exchange.ack.done())
                await asyncio.sleep(0.2)
                pending = next(iter(channel.pending.values()))
                first = pending.sequence
                await asyncio.sleep(0.2)
                assert pending.sequence == first
                assert 0 < pending.total < RESPONSE_BYTES
                assert pending.queue.maxsize == QUEUE_SIZE
                assert pending.queue.qsize() <= 1
                assert pending.unacknowledged
                assert not exchange.ack.done()
                assert CHUNK_BYTES == 16384
            finally:
                response.close()
                await client.close()
            await eventually(lambda: not connector.active and rig.state.pending == 0)
    asyncio.run(scenario())


def test_response_limit_aborts_without_success_and_cleans_resources(tmp_path):
    async def scenario():
        async with running(tmp_path) as rig:
            connector = await rig.connect()
            response = await rig.request("/api/large")
            total = 0
            try:
                with pytest.raises((aiohttp.ClientError, ConnectionError)):
                    async for data in response.content.iter_chunked(CHUNK_BYTES):
                        total += len(data)
                assert total <= RESPONSE_BYTES
                await eventually(lambda: not connector.active and rig.state.pending == 0)
            finally:
                response.close()
    asyncio.run(scenario())


def test_disconnect_after_mutation_dispatch_is_outcome_unknown_not_retry(tmp_path):
    async def scenario():
        async with running(tmp_path) as rig:
            await rig.connect()
            task = asyncio.create_task(rig.request("/api/hold", "POST", data=b"accepted"))
            try:
                await asyncio.wait_for(rig.backends[0].started.wait(), 3)
                await rig.state.channels["service-a"].close()
                response = await asyncio.wait_for(task, 3)
                async with response:
                    assert response.status == 502
                    assert await response.json() == {"error": "outcome-unknown"}
                assert len(rig.backends[0].requests) == 1
                assert rig.state.pending == 0
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_actual_channel_and_global_pending_limits(tmp_path):
    async def scenario():
        async with running(tmp_path) as rig:
            for name, prefix in (("service-c", "/third"), ("service-d", "/fourth")):
                slot = copy.deepcopy(rig.h.document["manifest"]["slots"][0])
                slot["name"] = name
                slot["routes"][0]["prefix"] = prefix
                rig.h.document["manifest"]["slots"].append(slot)
                rig.h.document["callers"][0]["slots"].append(name)
            await rig.update()
            await rig.connect()
            await rig.connect(1)
            for name in ("service-c", "service-d"):
                async def access():
                    return rig.access("service-a", 1)
                connector = ServiceConnector(
                    slot=name, gateway_origin=rig.origin, backend_origin=rig.origins[0],
                    access_token_callback=access,
                    proof_key=rig.h.keys[1].get_op_key("sign"), gateway_ca=rig.ca)
                rig.connectors.append(connector)
                task = asyncio.create_task(connector.connect_once())
                rig.tasks.append(task)
                await asyncio.wait_for(connector.connected.wait(), 3)
            pending = []
            try:
                for prefix in ("/api", "/assets", "/third", "/fourth"):
                    for _ in range(8):
                        pending.append(asyncio.create_task(rig.request(prefix + "/hold")))
                    await eventually(
                        lambda: rig.state.pending == len(pending))
                    if prefix == "/api":
                        async with await rig.request("/api/hold") as response:
                            assert response.status == 429
                assert rig.state.pending == GLOBAL_REQUESTS == 32
                async with await rig.request("/api/hold") as response:
                    assert response.status == 429
                assert all(len(c.pending) == 8 for c in rig.state.channels.values())
            finally:
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                await eventually(lambda: rig.state.pending == 0)
    asyncio.run(scenario())

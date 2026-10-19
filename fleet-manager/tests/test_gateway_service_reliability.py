"""Real attachment/response regressions and deterministic reconnect accounting."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from urllib.parse import urlsplit

import pytest

from fleet_manager.gateway_service_channel import TransportError
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

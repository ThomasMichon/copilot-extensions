"""Opt-in response budgets use the original authenticated exchange and emitter."""

from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from fleet_manager.gateway_service_channel import (
    BODY_BYTES, CHUNK_BYTES, RESPONSE_BYTES, TransportError,
)
from fleet_manager.gateway_service_connector import Exchange, ServiceConnector
from fleet_manager.gateway_service_handler import (
    BoundedServiceResponse, CallerContext, HandlerBinding, RequestDisconnected,
    RequestLifetime, ServiceRequest, ServiceResponse, respond,
)
from gateway_service_transport_fixtures import eventually, running
from test_gateway_service_caller import caller
from test_gateway_service_handler import connect_handler


@pytest.mark.parametrize("limit", [True, BODY_BYTES - 1, RESPONSE_BYTES + 1, float("inf")])
def test_invalid_binding_budget_refuses_before_handler(limit: object) -> None:
    async def handle(request: ServiceRequest) -> ServiceResponse:
        pytest.fail("invalid selection invoked a handler")

    with pytest.raises(TransportError, match="invalid-response-limit"):
        HandlerBinding("fleet-a", "gateway-a", handle, response_limit=limit)


def test_response_construction_preserves_legacy_bound_and_hides_large_body() -> None:
    body = b"private-response" * BODY_BYTES
    with pytest.raises(TransportError, match="response-limit"):
        ServiceResponse(200, body=body)
    selected = BoundedServiceResponse(200, body=body)
    assert selected.body == body and "private-response" not in repr(selected)
    with pytest.raises(TransportError, match="response-limit"):
        BoundedServiceResponse(200, body=b"x" * (RESPONSE_BYTES + 1))


@pytest.mark.parametrize("size", [BODY_BYTES + 1, 4 * CHUNK_BYTES + 3])
def test_real_tls_caller_connector_and_gateway_deliver_complete_selected_body(
    tmp_path: Path, size: int,
) -> None:
    async def scenario() -> None:
        async with running(tmp_path) as rig:
            body = ("snowman:\u2603\r\n".encode() * size)[:size]
            received = []

            async def handle(request: ServiceRequest) -> BoundedServiceResponse:
                received.append(request)
                assert request.response_limit == size
                assert request.context.principal == "caller-a"
                return BoundedServiceResponse(200, (("content-type", "text/plain"),), body)

            connector = await connect_handler(rig, handle, response_limit=size)
            result = await caller(rig).request("GET", "/api/report", response_limit=size)
            assert result.status == 200 and result.body == body
            await eventually(lambda: not connector.active and rig.state.pending == 0)
            assert len(received) == 1 and not rig.backends[0].requests
            with pytest.raises(RequestDisconnected):
                received[0].lifetime.check()
    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["handler-budget", "caller-budget"])
def test_real_transport_never_returns_a_truncated_over_budget_response(
    tmp_path: Path, failure: str,
) -> None:
    async def scenario() -> None:
        async with running(tmp_path) as rig:
            limit = BODY_BYTES + 1
            received = []

            async def handle(request: ServiceRequest) -> BoundedServiceResponse:
                received.append(request)
                return BoundedServiceResponse(200, body=b"x" * (limit + 1))

            connector = await connect_handler(
                rig, handle, response_limit=limit if failure == "handler-budget" else limit + 1,
            )
            client = caller(rig)
            if failure == "handler-budget":
                result = await client.request("GET", "/api/report", response_limit=limit)
                assert result.status == 502
                assert json.loads(result.body) == {"error": "response-limit"}
            else:
                with pytest.raises(TransportError, match="gateway-response-limit"):
                    await client.request("GET", "/api/report", response_limit=limit)
            await eventually(lambda: not connector.active and rig.state.pending == 0)
            assert len(received) == 1 and client._active == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("selection", ["legacy", "over-budget", "request-mismatch"])
def test_large_response_refuses_before_headers_or_mismatched_handler(selection: str) -> None:
    async def scenario() -> None:
        sent, handled = [], []
        limit = BODY_BYTES if selection == "legacy" else BODY_BYTES + 1
        lifetime = RequestLifetime()
        request = ServiceRequest(
            CallerContext("fleet-a", "gateway-a", "service-a", "caller-a", 2**52),
            "GET", "/", (), b"", lifetime, response_limit=limit,
        )
        assert request.lifetime is lifetime
        if selection == "request-mismatch":
            request = replace(request, response_limit=limit + 1)

        async def handle(incoming: ServiceRequest) -> BoundedServiceResponse:
            handled.append(incoming)
            return BoundedServiceResponse(200, body=b"x" * (BODY_BYTES + 2))

        class Socket:
            async def send_str(self, raw: str) -> None:
                sent.append(json.loads(raw))

        with pytest.raises(TransportError):
            await respond(
                Socket(), Exchange("1" * 48),
                HandlerBinding("fleet-a", "gateway-a", handle, response_limit=limit), request,
            )
        assert not sent
        assert len(handled) == (0 if selection == "request-mismatch" else 1)
    asyncio.run(scenario())


@pytest.mark.parametrize("loss", ["expiry", "disconnect", "deadline"])
def test_large_response_ack_backpressure_retains_original_lifetime_and_never_ends(
    monkeypatch: pytest.MonkeyPatch, loss: str,
) -> None:
    async def scenario() -> None:
        sent, received = [], []
        clock = [1]
        monkeypatch.setattr("fleet_manager.gateway_service_handler.time",
                            SimpleNamespace(time=lambda: clock[0]))
        limit = BODY_BYTES + CHUNK_BYTES
        body = b"private-chunks" * (limit // 14) + b"!"
        started = asyncio.Event()

        async def handle(request: ServiceRequest) -> BoundedServiceResponse:
            received.append(request)
            return BoundedServiceResponse(200, body=body)

        binding = HandlerBinding("fleet-a", "gateway-a", handle, response_limit=limit)
        connector = ServiceConnector(
            slot="service-a", gateway_origin="https://gateway.example",
            handler=binding, access_token_callback=lambda: None, proof_key=None,
        )
        spec = connector._request({
            "type": "request", "id": "1" * 48, "method": "GET", "target": "/",
            "headers": {}, "body": "", "timeout": 0.1 if loss == "deadline" else 60,
            "context": {"fleet": "fleet-a", "gateway": "gateway-a", "slot": "service-a",
                        "principal": "caller-a", "expires_at": 10},
        })
        request = spec[-1]
        exchange = Exchange("1" * 48, request=request)

        class Socket:
            async def send_str(self, raw: str) -> None:
                frame = json.loads(raw)
                sent.append(frame)
                if frame["type"] == "chunk":
                    started.set()

        pending = asyncio.create_task(connector._exchange(Socket(), None, exchange, spec))
        try:
            await asyncio.wait_for(started.wait(), 1)
            await asyncio.sleep(0)
            assert [frame["type"] for frame in sent] == ["headers", "chunk"]
            assert base64.b64decode(sent[-1]["data"]) == body[:CHUNK_BYTES]
            assert received == [request] and request.response_limit == limit
            if loss == "expiry":
                clock[0] = 10
                exchange.ack.set_result(None)
            elif loss == "disconnect":
                request.lifetime.close()
                exchange.ack.set_result(None)
            await asyncio.wait_for(pending, 1)
            assert not any(frame["type"] == "end" for frame in sent)
            assert sent[-1]["type"] == "error"
            with pytest.raises(RequestDisconnected):
                request.lifetime.check()
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
    asyncio.run(scenario())

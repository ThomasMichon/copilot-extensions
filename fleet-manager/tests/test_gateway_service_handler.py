"""Caller custody crosses real TLS/PoP/WebSocket, never caller-supplied labels."""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from fleet_manager.gateway_service_channel import BODY_BYTES, TransportError
from fleet_manager.gateway_service_connector import ServiceConnector
from fleet_manager.gateway_service_handler import (
    PROFILE, CallerContext, HandlerBinding, ServiceRequest, ServiceResponse,
)
from gateway_service_transport_fixtures import eventually, running
from test_gateway_service_policy import Harness
from test_runtime_identity import NOW, pair


async def connect_handler(rig, handle, *, fleet="fleet-a", gateway="gateway-a"):
    async def access():
        return rig.access("service-a", 1)
    connector = ServiceConnector(
        slot="service-a", gateway_origin=rig.origin,
        handler=HandlerBinding(fleet, gateway, handle),
        access_token_callback=access, proof_key=rig.h.keys[1].get_op_key("sign"),
        gateway_ca=rig.ca)
    task = asyncio.create_task(connector.connect_once())
    rig.connectors.append(connector)
    rig.tasks.append(task)
    await asyncio.wait_for(connector.connected.wait(), 3)
    return connector


def test_retained_caller_context_is_not_constructible_grant_authority():
    h = Harness()
    h.document["identity"]["trust_valid_until"] = NOW + 10
    h.owner.reload()
    grant = h.caller()
    scope = h.owner.revalidate_caller(grant)
    assert scope.principal == "caller-a"
    assert scope.expires_at == NOW + 10 < grant.expires_at
    with pytest.raises(FrozenInstanceError):
        scope.principal = "caller-b"
    h.document["callers"] = []
    h.update()
    with pytest.raises(ValueError, match="invalid-grant"):
        h.owner.revalidate_caller(grant)


def test_real_channel_identity_ignores_body_query_and_headers(tmp_path):
    async def scenario():
        async with running(tmp_path) as rig:
            received = []

            async def handle(request):
                received.append(request)
                return ServiceResponse(200, (("content-type", "application/json"),), b'{"ok":true}')

            await connect_handler(rig, handle)
            assert rig.state.channels["service-a"].caller_context
            async with await rig.request(
                "/api/register?principal=caller-b&account=foreign", "POST",
                headers={"X-Identity": "caller-b", "X-Fleet-Caller": "caller-b",
                         "Cookie": "principal=caller-b", "Accept": "application/json"},
                data=b'{"principal":"caller-b","account":"foreign"}',
            ) as response:
                assert response.status == 200
                assert await response.json() == {"ok": True}
                assert response.headers["Cache-Control"] == "no-store"
            request = received[0]
            assert type(request) is ServiceRequest
            assert request.context == CallerContext(
                "fleet-a", "gateway-a", "service-a", "caller-a",
                rig.h.clock.value + 3600)
            assert request.method == "POST"
            assert request.target == "/register?principal=caller-b&account=foreign"
            assert request.body == b'{"principal":"caller-b","account":"foreign"}'
            assert dict(request.headers) == {"accept": "application/json",
                                            "content-type": "application/octet-stream"}
            with pytest.raises(FrozenInstanceError):
                request.context.principal = "caller-b"
            assert rig.backends[0].requests == []
            selected = await rig.auth("/api")
            selected["X-Identity"] = "caller-a"
            selected.pop("Authorization")
            async with rig.session.get(rig.origin + "/api", headers=selected) as response:
                assert response.status == 401
            assert len(received) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("owner", ["fleet", "gateway"])
def test_wrong_handler_owner_never_invokes_backend(tmp_path, owner):
    async def scenario():
        async with running(tmp_path) as rig:
            received = []

            async def handle(request):
                received.append(request)
                return ServiceResponse(200)

            await connect_handler(rig, handle, **{owner: "foreign"})
            async with await rig.request() as response:
                assert response.status == 502
            await eventually(lambda: rig.tasks[-1].done())
            assert isinstance(rig.tasks[-1].exception(), TransportError)
            assert received == [] and rig.backends[0].requests == []
    asyncio.run(scenario())


def test_real_caller_withdrawal_cancels_handler_before_completion(tmp_path):
    async def scenario():
        async with running(tmp_path) as rig:
            started, stopped = asyncio.Event(), asyncio.Event()

            async def handle(request):
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.set()

            await connect_handler(rig, handle)
            pending = asyncio.create_task(rig.request("/api/register", "POST"))
            try:
                await asyncio.wait_for(started.wait(), 3)
                rig.h.document["callers"] = []
                await rig.update()
                async with await pending as response:
                    assert response.status == 403
                await asyncio.wait_for(stopped.wait(), 3)
                assert not rig.connectors[-1].active
            finally:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
    asyncio.run(scenario())


def test_two_admitted_subjects_keep_separate_contexts_on_one_channel(tmp_path):
    async def scenario():
        async with running(tmp_path) as rig:
            received = []

            async def handle(request):
                received.append(request.context)
                return ServiceResponse(200)

            await connect_handler(rig, handle)
            rig.h.document["callers"].append({
                "principal": "caller-b", "key_source": "caller-a",
                "slots": ["service-a"], "methods": ["GET"],
            })
            await rig.update()
            for subject in ("caller-a", "caller-b"):
                selected = await rig.auth("/api", subject=subject)
                async with rig.session.get(rig.origin + "/api", headers=selected) as response:
                    assert response.status == 200
            assert [context.principal for context in received] == ["caller-a", "caller-b"]
            assert rig.connectors[-1].connected.is_set()
            assert rig.backends[0].requests == []
    asyncio.run(scenario())


def test_handler_context_is_mandatory_and_never_accepted_by_proxy():
    async def unused(request):
        raise AssertionError("not invoked")
    options = dict(slot="service-a", gateway_origin="https://gateway.example",
                   access_token_callback=lambda: None, proof_key=None)
    handler = ServiceConnector(**options, handler=HandlerBinding("fleet-a", "gateway-a", unused))
    proxy = ServiceConnector(**options, backend_origin="http://localhost")
    frame = {"type": "request", "id": "1" * 48, "method": "GET", "target": "/",
             "headers": {}, "body": "", "timeout": 60}
    with pytest.raises(TransportError):
        handler._request(frame)
    frame["context"] = {"fleet": "fleet-a", "gateway": "gateway-a", "slot": "service-a",
                        "principal": "caller-a", "expires_at": 2**52}
    with pytest.raises(TransportError):
        proxy._request(frame)
    assert handler._request(frame)[-1].context.principal == "caller-a"


@pytest.mark.parametrize("failure", ["oversize", "exception", "wrong-result"])
def test_handler_failure_is_explicit_and_value_free(tmp_path, caplog, failure):
    async def scenario():
        async with running(tmp_path) as rig:
            async def handle(request):
                if failure == "exception":
                    raise RuntimeError("synthetic-private-handler-detail")
                if failure == "wrong-result":
                    return {"secret": "synthetic-private-handler-detail"}
                return ServiceResponse(200, body=b"x" * (BODY_BYTES + 1))

            await connect_handler(rig, handle)
            async with await rig.request() as response:
                assert response.status == 502
                assert await response.json() == {
                    "error": "response-limit" if failure == "oversize"
                    else "invalid-backend-response"}
            assert "synthetic-private-handler-detail" not in caplog.text
    asyncio.run(scenario())


def test_bounded_handler_response_uses_chunk_ack_and_no_http_backend(tmp_path):
    async def scenario():
        async with running(tmp_path) as rig:
            async def handle(request):
                return ServiceResponse(200, body=b"x" * BODY_BYTES)

            connector = await connect_handler(rig, handle)
            async with await rig.request() as response:
                assert response.status == 200
                assert await response.read() == b"x" * BODY_BYTES
            await eventually(lambda: not connector.active and rig.state.pending == 0)
            assert rig.backends[0].requests == []
    asyncio.run(scenario())


def test_unknown_profile_does_not_allocate_admission_ticket(tmp_path):
    async def scenario():
        async with running(tmp_path) as rig:
            selected = await rig.auth("/_fleet/service-admit", "POST",
                                      subject="service-a", key=1)
            async with rig.session.post(
                rig.origin + "/_fleet/service-admit",
                json={"slot": "service-a", "access": selected["Authorization"][5:],
                      "proof": selected["DPoP"], "profile": PROFILE + "-unknown"},
            ) as response:
                assert response.status == 400
                assert await response.json() == {"error": "unsupported-profile"}
            assert not rig.state.tickets
    asyncio.run(scenario())


@pytest.mark.parametrize("change", [
    {"principal": None}, {"principal": "other/operator"}, {"expires_at": True},
    {"expires_at": 0}, {"extra": "unknown"}, {"slot": "service-b"},
    {"fleet": "foreign"}, {"gateway": "foreign"},
])
def test_context_schema_and_binding_refuse_before_handler(change):
    async def unused(request):
        raise AssertionError("not invoked")
    binding = HandlerBinding("fleet-a", "gateway-a", unused)
    context = {"fleet": "fleet-a", "gateway": "gateway-a", "slot": "service-a",
               "principal": "caller-a", "expires_at": 2**52}
    with pytest.raises(TransportError):
        binding.context(dict(context, **change), "service-a")


def test_structural_context_parse_does_not_expire_the_shared_channel():
    async def unused(request):
        raise AssertionError("not invoked")
    binding = HandlerBinding("fleet-a", "gateway-a", unused)
    context = binding.context({
        "fleet": "fleet-a", "gateway": "gateway-a", "slot": "service-a",
        "principal": "caller-a", "expires_at": 1}, "service-a")
    assert context.expires_at == 1


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_expiry_in_transit_refuses_one_request_not_other_active_handlers(
    tmp_path, monkeypatch, method,
):
    async def scenario():
        async with running(tmp_path) as rig:
            rig.h.document["callers"].append({
                "principal": "caller-b", "key_source": "caller-a",
                "slots": ["service-a"], "methods": ["GET", "POST"],
            })
            await rig.update()
            clock = [rig.h.clock.value]
            monkeypatch.setattr("fleet_manager.gateway_service_handler.time",
                                SimpleNamespace(time=lambda: clock[0]))
            access = rig.access

            def selected_access(subject, key):
                if subject != "caller-b":
                    return access(subject, key)
                return pair(
                    (rig.h.keys[0], rig.h.keys[key]), SimpleNamespace(nonce="unused"),
                    now=rig.h.clock.value,
                    access={"sub": subject, "exp": rig.h.clock.value + 5})[0]

            rig.access = selected_access
            started, release, stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()
            received = []

            async def handle(request):
                received.append(request.context.principal)
                started.set()
                try:
                    await release.wait()
                    return ServiceResponse(200, body=b"still-valid")
                finally:
                    stopped.set()

            connector = await connect_handler(rig, handle)
            valid = asyncio.create_task(rig.request("/api/hold"))
            try:
                await asyncio.wait_for(started.wait(), 3)
                # The Gateway's clock still admits this caller; it expires in transit.
                clock[0] += 10
                selected = await rig.auth("/api", method, subject="caller-b")
                async with rig.session.request(
                    method, rig.origin + "/api", headers=selected,
                ) as response:
                    assert response.status == 502
                    assert await response.json() == {
                        "error": "caller-context-expired" if method == "GET" else "outcome-unknown"}
                assert connector.connected.is_set()
                assert not rig.tasks[-1].done()
                assert not stopped.is_set()
                assert received == ["caller-a"]
                release.set()
                async with await valid as response:
                    assert response.status == 200
                    assert await response.read() == b"still-valid"
                assert connector.connected.is_set()
                async with await rig.request() as response:
                    assert response.status == 200
            finally:
                release.set()
                valid.cancel()
                await asyncio.gather(valid, return_exceptions=True)
    asyncio.run(scenario())


def test_connector_requires_exactly_one_explicit_backend():
    async def unused(request):
        return ServiceResponse(200)
    options = dict(slot="service-a", gateway_origin="https://gateway.example",
                   access_token_callback=lambda: None, proof_key=None)
    with pytest.raises(TransportError, match="select-one-backend"):
        ServiceConnector(**options)
    with pytest.raises(TransportError, match="select-one-backend"):
        ServiceConnector(**options, backend_origin="http://localhost",
                         handler=HandlerBinding("fleet-a", "gateway-a", unused))

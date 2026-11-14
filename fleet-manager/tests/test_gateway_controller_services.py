"""Controller offers cross real TLS, authentication and the owned channel."""

import asyncio
import copy
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest
from aiohttp import web

from fleet_manager.gateway_controller_services import (
    ControllerService, ControllerSourceBinding, services,
)
from fleet_manager.gateway_local_handler import LocalHandlerBinding
from fleet_manager.gateway_manifest import GatewayManifest
from fleet_manager.gateway_service_channel import TransportError
from fleet_manager.gateway_service_connector import ServiceConnector
from fleet_manager.gateway_service_handler import HandlerBinding, ServiceResponse
from fleet_manager.gateway_service_server import ServiceRuntime
from fleet_manager.machine_identity import (
    PROFILE as MACHINE_PROFILE, machine_headers, make_machine_proof,
)
from gateway_service_transport_fixtures import Rig, eventually
from test_runtime_identity import AUDIENCE


VAULT = "vault-available-v1"
NOTICE = "device-notice-v1"


@asynccontextmanager
async def controllers(root):
    rig = Rig(root)
    rig.h.document["manifest"]["slots"][0]["routes"].append({
        "prefix": "/tools", "methods": ["GET", "POST"], "capabilities": ["http"],
    })
    try:
        await rig.start()
        yield rig
    finally:
        await rig.close()


async def connect(rig, index=0, offers=None, handle=None):
    if offers is None:
        offers = (ControllerService(VAULT, "/api" if index == 0 else "/assets"),)

    async def default(request):
        return ServiceResponse(200, body=(request.service + ":" + request.target).encode())

    async def access():
        return rig.access("service-a" if index == 0 else "service-b", index + 1)

    connector = ServiceConnector(
        slot="service-a" if index == 0 else "service-b", gateway_origin=rig.origin,
        handler=HandlerBinding("fleet-a", "gateway-a", handle or default),
        services=offers, access_token_callback=access,
        proof_key=rig.h.keys[index + 1].get_op_key("sign"), gateway_ca=rig.ca)
    task = asyncio.create_task(connector.connect_once())
    rig.connectors.append(connector)
    rig.tasks.append(task)
    ready = asyncio.create_task(connector.connected.wait())
    try:
        done, _ = await asyncio.wait({task, ready}, timeout=3,
                                     return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            await task
        assert ready in done
    finally:
        ready.cancel()
        await asyncio.gather(ready, return_exceptions=True)
    return connector


def test_multiple_services_share_one_current_authenticated_controller_channel(tmp_path):
    async def scenario():
        async with controllers(tmp_path) as rig:
            first = await connect(rig, offers=(
                ControllerService(VAULT, "/api"), ControllerService(NOTICE, "/tools")))
            second = await connect(rig, 1)
            offers = rig.state.service_offers(VAULT)
            assert len(offers) == len(rig.state.channels) == 2
            assert {item.principal for item in offers} == {"service-a", "service-b"}
            assert len({item.connection for item in offers}) == 2
            for item in offers:
                rig.state.check_service_offer(item)
            assert len(rig.state.service_offers(NOTICE)) == 1
            for path, expected in (("/api/token", VAULT + ":/token"),
                                   ("/assets/token", VAULT + ":/token"),
                                   ("/tools/notice", NOTICE + ":/notice")):
                async with await rig.request(path) as response:
                    assert response.status == 200
                    assert await response.text() == expected
            await first.advertise((ControllerService(NOTICE, "/tools"),))
            assert len(rig.state.service_offers(VAULT)) == 1
            with pytest.raises(TransportError, match="controller-service-unavailable"):
                rig.state.check_service_offer(offers[0])
            async with await rig.request("/api/token") as response:
                assert response.status == 503
            await first.advertise((ControllerService(VAULT, "/api"),))
            assert len(rig.state.service_offers(VAULT)) == 2
            with pytest.raises(TransportError, match="controller-service-unavailable"):
                rig.state.check_service_offer(offers[0])
            assert not rig.state.service_offers(NOTICE)
            assert len(rig.state.channels) == 2
            await first.advertise(())
            await second.advertise(())
            assert not rig.state.service_offers(VAULT)
            assert first.connected.is_set() and second.connected.is_set()
    asyncio.run(scenario())


def test_replacement_and_disconnect_fence_previous_connection_observations(tmp_path):
    async def scenario():
        async with controllers(tmp_path) as rig:
            await connect(rig)
            old = rig.state.service_offers(VAULT)[0]
            old_channel = rig.state.channels["service-a"]
            await connect(rig)
            current = rig.state.service_offers(VAULT)[0]
            assert current.connection != old.connection
            with pytest.raises(TransportError, match="controller-service-unavailable"):
                rig.state.check_service_offer(old)
            await old_channel.close()
            rig.state.check_service_offer(current)
            await rig.state.channels["service-a"].close()
            assert not rig.state.service_offers(VAULT)
            with pytest.raises(TransportError, match="controller-service-unavailable"):
                rig.state.check_service_offer(current)
    asyncio.run(scenario())


def test_withdrawal_racing_an_inflight_request_does_not_close_other_services(tmp_path):
    async def scenario():
        async with controllers(tmp_path) as rig:
            received = []

            async def handle(request):
                received.append(request.service)
                return ServiceResponse(200)

            connector = await connect(rig, handle=handle, offers=(
                ControllerService(VAULT, "/api"), ControllerService(NOTICE, "/tools")))
            channel = rig.state.channels["service-a"]
            original = channel.update_services
            arrived, release = asyncio.Event(), asyncio.Event()

            async def delayed(value):
                arrived.set()
                await release.wait()
                await original(value)

            channel.update_services = delayed
            retired = asyncio.Event()
            original_retire = connector._retire

            def retire(exchange):
                original_retire(exchange)
                retired.set()

            connector._retire = retire
            task = asyncio.create_task(
                connector.advertise((ControllerService(NOTICE, "/tools"),)))
            pending = None
            try:
                await asyncio.wait_for(arrived.wait(), 3)
                # The Gateway has not observed withdrawal; its old request is
                # refused locally without withdrawing the unrelated service.
                pending = asyncio.create_task(rig.request("/api/token"))
                await asyncio.wait_for(retired.wait(), 3)
                assert connector.connected.is_set() and not received
                release.set()
                await task
                async with await pending as response:
                    assert response.status != 200
                async with await rig.request("/tools/notice") as response:
                    assert response.status == 200
                assert received == [NOTICE] and rig.state.channels["service-a"] is channel
            finally:
                release.set()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                if pending is not None:
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
    asyncio.run(scenario())


def test_offer_withdrawal_cancels_pending_and_preserves_original_caller(tmp_path):
    async def scenario():
        async with controllers(tmp_path) as rig:
            started, stopped = asyncio.Event(), asyncio.Event()
            received = []

            async def handle(request):
                received.append(request)
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.set()

            connector = await connect(rig, handle=handle)
            task = asyncio.create_task(rig.request("/api/token"))
            try:
                await asyncio.wait_for(started.wait(), 3)
                assert received[0].context.principal == "caller-a"
                assert received[0].context.slot == "service-a"
                assert received[0].service == VAULT
                await connector.advertise(())
                async with await task as response:
                    assert response.status == 503
                    assert await response.json() == {"error": "controller-service-withdrawn"}
                await asyncio.wait_for(stopped.wait(), 3)
                await eventually(lambda: not connector.active)
                with pytest.raises(TransportError, match="request-disconnected"):
                    received[0].lifetime.check()
                assert rig.state.pending == 0
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_withdraw_readd_while_post_body_waits_never_dispatches_the_cancelled_request(tmp_path):
    async def scenario():
        async with controllers(tmp_path) as rig:
            received = []
            started, release = asyncio.Event(), asyncio.Event()

            async def handle(request):
                received.append(request.method)
                return ServiceResponse(200)

            async def payload():
                yield b"{"
                started.set()
                await release.wait()
                yield b"}"

            connector = await connect(rig, handle=handle)
            task = asyncio.create_task(rig.request("/api/token", "POST", data=payload()))
            try:
                await asyncio.wait_for(started.wait(), 3)
                await eventually(lambda: rig.state.pending == 1)
                channel = rig.state.channels["service-a"]
                pending = next(iter(channel.pending.values()))
                assert not pending.dispatched
                await connector.advertise(())
                assert pending.phase == "failed" and pending.id in channel.retired
                await connector.advertise((ControllerService(VAULT, "/api"),))
                release.set()
                async with await task as response:
                    assert response.status == 503
                async with await rig.request("/api/probe") as response:
                    assert response.status == 200
                assert received == ["GET"]
                assert rig.state.pending == 0
            finally:
                release.set()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize("when", ["admission", "update"])
def test_controller_cannot_offer_another_slots_namespace(tmp_path, when):
    async def scenario():
        async with controllers(tmp_path) as rig:
            if when == "admission":
                with pytest.raises(TransportError, match="gateway-admission-refused"):
                    await connect(rig, offers=(ControllerService(VAULT, "/assets"),))
                assert not rig.state.channels and not rig.state.tickets
                assert not rig.state.authority._candidates
            else:
                connector = await connect(rig)
                with pytest.raises(TransportError, match="controller-channel-unavailable"):
                    await connector.advertise((ControllerService(VAULT, "/assets"),))
                await eventually(lambda: not rig.state.service_offers(VAULT))
            assert rig.backends[0].requests == rig.backends[1].requests == []
    asyncio.run(scenario())


def test_unauthenticated_admission_cannot_enumerate_slot_route_mappings(tmp_path):
    async def scenario():
        async with controllers(tmp_path) as rig:
            results = []
            for slot, prefix in (("service-a", "/api"), ("service-a", "/assets"),
                                  ("unknown", "/api")):
                async with rig.session.post(rig.origin + "/_fleet/service-admit", json={
                    "profile": "controller-services-v1", "slot": slot,
                    "services": [{"service": VAULT, "prefix": prefix}],
                    "access": "invalid", "proof": "invalid",
                }) as response:
                    results.append((response.status, await response.json()))
            assert results == [(401, {"error": "authorization-denied"})] * 3
            assert not rig.state.tickets and not rig.state.authority._candidates
    asyncio.run(scenario())


def test_policy_revocation_and_caller_denial_are_not_overridden_by_offers(tmp_path):
    async def scenario():
        async with controllers(tmp_path) as rig:
            await connect(rig)
            offer = rig.state.service_offers(VAULT)[0]
            rig.h.document["callers"][0]["slots"] = ["service-b"]
            await rig.update()
            rig.state.check_service_offer(offer)
            async with await rig.request() as response:
                assert response.status == 403
            rig.h.document["manifest"]["slots"][0]["configuration_ref"] = "config/replaced"
            await rig.update()
            assert not rig.state.service_offers(VAULT)
            with pytest.raises(TransportError, match="controller-service-unavailable"):
                rig.state.check_service_offer(offer)
    asyncio.run(scenario())


def test_selected_controller_profile_also_retains_machine_body_and_resource_auth(tmp_path):
    async def scenario():
        rig = Rig(tmp_path)
        rig.h.document["version"] = 4
        rig.h.document["identity"] = {
            "profile": MACHINE_PROFILE, "audience": AUDIENCE,
            "trust_valid_until": rig.h.clock.value + 3600,
        }
        rig.h.document["enrollment"] = {"mappings": [
            {"principal": name, "kind": kind, "resource": "service-a",
             "key_source": name, "expires_at": rig.h.clock.value + 600}
            for name, kind in (("service-a", "service"), ("caller-a", "caller"))
        ]}
        received = []

        async def handle(request):
            received.append(request)
            return ServiceResponse(200)

        try:
            await rig.start()
            connector = ServiceConnector(
                slot="service-a", gateway_origin=rig.origin,
                handler=HandlerBinding("fleet-a", "gateway-a", handle),
                services=(ControllerService(VAULT, "/api"),),
                proof_key=rig.h.keys[1], gateway_ca=rig.ca,
                machine_principal="service-a", machine_audience=AUDIENCE)
            task = asyncio.create_task(connector.connect_once())
            rig.tasks.append(task)
            await asyncio.wait_for(connector.connected.wait(), 3)
            proof = make_machine_proof(
                rig.h.keys[3], principal="caller-a", audience=AUDIENCE,
                kind="caller", resource="service-a", method="GET",
                target_uri=rig.origin + "/api", body=b"",
                nonce=rig.state.authority.challenge(), now=rig.h.clock.value)
            async with rig.session.get(
                rig.origin + "/api", headers=machine_headers("caller-a", proof),
            ) as response:
                assert response.status == 200
            assert received[0].context.principal == "caller-a"
            assert received[0].service == VAULT and received[0].origin is None
            assert rig.state.service_offers(VAULT)[0].principal == "service-a"
        finally:
            await rig.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("declaration", [None, {"nonce": "unused"},
                                       {"nonce": "unused", "profile": "unknown"},
                                       {"nonce": None, "profile": "controller-services-v1"},
                                       {"nonce": "x" * 42, "profile": "controller-services-v1"},
                                       {"nonce": "+" * 43, "profile": "controller-services-v1"}])
def test_legacy_or_unsupported_running_peer_refuses_before_credentials_or_admission(
    tmp_path, declaration,
):
    async def scenario():
        rig = Rig(tmp_path)
        invoked = []

        async def discovery(request):
            invoked.append("discovery")
            return (web.json_response(declaration) if declaration is not None
                    else web.Response(status=404))

        async def admission(request):
            invoked.append("admission")
            return web.Response(status=500)

        async def access():
            invoked.append("credentials")
            return "unused"

        async def handle(request):
            raise AssertionError("not dispatched")

        app = web.Application()
        app.router.add_get("/_fleet/controller-challenge", discovery)
        app.router.add_post("/_fleet/service-admit", admission)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        try:
            await web.TCPSite(runner, "127.0.0.1", 0,
                              ssl_context=rig.listener.tls()).start()
            connector = ServiceConnector(
                slot="service-a",
                gateway_origin=f"https://localhost:{runner.addresses[0][1]}",
                handler=HandlerBinding("fleet-a", "gateway-a", handle),
                services=(ControllerService(VAULT, "/api"),),
                access_token_callback=access, proof_key=rig.h.keys[1].get_op_key("sign"),
                gateway_ca=rig.ca)
            with pytest.raises(TransportError):
                await connector.connect_once()
            assert invoked == ["discovery"]
            assert not connector.connected.is_set()
        finally:
            await runner.cleanup()
    asyncio.run(scenario())


@pytest.mark.parametrize("value", [
    None, {}, [{"service": VAULT, "prefix": "/api", "extra": True}],
    [{"service": VAULT, "prefix": "/api"}] * 2,
    [{"service": VAULT, "prefix": "/api?foreign"}],
    [{"service": VAULT, "prefix": "/"}],
    [{"service": VAULT, "prefix": "/api/"}],
    [{"service": "bad value", "prefix": "/api"}],
    [{"service": f"service-{i}", "prefix": f"/route-{i}"} for i in range(17)],
])
def test_offer_parser_is_strict_and_bounded(value):
    with pytest.raises(TransportError):
        services(value)


def test_maximum_inventory_has_one_authority_check_per_channel_and_constant_offer_lookup():
    checks = []

    def revalidate(grant):
        checks.append(grant)
        return SimpleNamespace(principal=grant)

    runtime = object.__new__(ServiceRuntime)
    runtime.stopping = False
    runtime.authority = SimpleNamespace(revalidate_service=revalidate)
    runtime.channels = {}
    for controller in range(64):
        selected = (ControllerService(VAULT, f"/controller-{controller}"), *(
            ControllerService(f"service-{i}", f"/route-{controller}-{i}") for i in range(15)))
        runtime.channels[f"controller-{controller}"] = SimpleNamespace(
            closed=False, services=selected, incarnation=str(controller),
            grant=f"principal-{controller}", offer_epochs={item: "current" for item in selected})
    offers = runtime.service_offers(VAULT)
    assert len(offers) == len(checks) == 64
    checks.clear()
    runtime.check_service_offer(offers[0])
    assert checks == [offers[0].principal]


@asynccontextmanager
async def gateway_owner(root, handle, *, allowed=("service-a", "service-b"),
                        caller_sources=("service-a", "service-b"), controller_routes=None):
    rig = Rig(root)
    if controller_routes is not None:
        rig.h.document["manifest"]["slots"][0]["routes"] = controller_routes
    slot = copy.deepcopy(rig.h.document["manifest"]["slots"][0])
    slot.update(name="vault", routes=[{
        "prefix": "/vault", "methods": ["GET", "POST"], "capabilities": ["http"],
    }])
    rig.h.document["manifest"]["slots"].append(slot)
    rig.h.document["callers"][0]["slots"] = ["vault", *caller_sources]
    declared = GatewayManifest.load(rig.h.document["manifest"])
    binding = LocalHandlerBinding(
        next(item for item in declared.slots if item.name == "vault"), handle,
        (ControllerSourceBinding(VAULT, allowed),))
    try:
        await rig.start(local_handlers=(binding,))
        yield rig
    finally:
        await rig.close()


def test_local_owner_dispatch_uses_real_channels_and_retains_original_admission(tmp_path):
    async def scenario():
        received, retained = [], []

        async def local(request):
            retained.append(request)
            offers = request.service_offers(VAULT)
            assert len(offers) == 2
            responses = [await request.call_service(
                offer, target="/available", body=request.request.body) for offer in offers]
            return ServiceResponse(200, body=b",".join(item.body for item in responses))

        async def remote(request):
            received.append(request)
            return ServiceResponse(200, body=request.context.slot.encode())

        async with gateway_owner(tmp_path, local) as rig:
            await connect(rig, handle=remote)
            await connect(rig, 1, handle=remote)
            async with await rig.request(
                "/vault/token", "POST", data=b'{"principal":"forged"}',
            ) as response:
                assert response.status == 200
                assert await response.read() == b"service-a,service-b"
            assert len(received) == 2
            for request in received:
                assert request.context.principal == "caller-a"
                assert request.service == VAULT and request.method == "POST"
                assert request.target == "/available"
                assert request.body == b'{"principal":"forged"}'
                assert request.origin.slot == "vault"
                assert request.origin.method == "POST"
                assert request.origin.target_uri == rig.origin + "/vault/token"
                assert not ({"authorization", "dpop"} & dict(request.headers).keys())
            assert retained[0]._grant.slot == "vault"
            with pytest.raises(TransportError, match="request-disconnected"):
                retained[0].service_offers(VAULT)
            assert rig.state.pending == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("denial", ["caller", "binding", "forged-offer"])
def test_internal_dispatch_requires_independent_caller_source_and_binding_grants(tmp_path, denial):
    async def scenario():
        received = []

        async def remote(request):
            received.append(request)
            return ServiceResponse(200)

        async def local(request):
            offers = request.service_offers(VAULT)
            if denial == "caller":
                assert not offers
                return ServiceResponse(503)
            assert {item.controller for item in offers} == {"service-b"}
            offer = rig.state.service_offers(VAULT)[0]
            if denial == "forged-offer":
                offer = replace(offers[0], generation="foreign")
            return await request.call_service(offer, target="/available")

        async with gateway_owner(
            tmp_path, local, allowed=("service-b",),
            caller_sources=() if denial == "caller" else ("service-a", "service-b"),
        ) as rig:
            await connect(rig, handle=remote)
            await connect(rig, 1, handle=remote)
            async with await rig.request("/vault/token") as response:
                assert response.status == (403 if denial == "binding" else 503)
            assert not received and rig.state.pending == 0
    asyncio.run(scenario())


def test_unadvertised_service_with_missing_epoch_cannot_borrow_a_post_route(tmp_path):
    async def scenario():
        received = []

        async def remote(request):
            received.append(request)
            return ServiceResponse(200)

        async def local(request):
            offer = request.service_offers(VAULT)[0]
            return await request.call_service(
                replace(offer, prefix="/tools", generation=None), target="/available")

        routes = [
            {"prefix": "/api", "methods": ["GET"], "capabilities": ["http"]},
            {"prefix": "/tools", "methods": ["POST"], "capabilities": ["http"]},
        ]
        async with gateway_owner(
            tmp_path, local, allowed=("service-a",), controller_routes=routes,
        ) as rig:
            await connect(rig, handle=remote, offers=(
                ControllerService(VAULT, "/api"), ControllerService(NOTICE, "/tools")))
            async with await rig.request("/vault/token", "POST") as response:
                assert len(received) == 0
                assert response.status == 503
            assert received == [] and rig.state.pending == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("stop", ["timeout", "caller", "offer", "replacement"])
def test_internal_pending_is_fenced_by_deadline_caller_offer_and_connection(tmp_path, stop):
    async def scenario():
        started, stopped = asyncio.Event(), asyncio.Event()
        received = []

        async def remote(request):
            received.append(request)
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        async def local(request):
            return await request.call_service(
                request.service_offers(VAULT)[0], target="/available",
                timeout=0.1 if stop == "timeout" else 3)

        async with gateway_owner(tmp_path, local) as rig:
            connector = await connect(rig, handle=remote)
            task = asyncio.create_task(rig.request("/vault/token"))
            try:
                await asyncio.wait_for(started.wait(), 3)
                if stop == "caller":
                    rig.h.document["callers"] = []
                    await rig.update()
                elif stop == "offer":
                    await connector.advertise(())
                elif stop == "replacement":
                    await connect(rig, handle=remote)
                async with await task as response:
                    assert response.status != 200
                await asyncio.wait_for(stopped.wait(), 3)
                assert len(received) == 1
                await eventually(lambda: not connector.active)
                assert rig.state.pending == 0
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())

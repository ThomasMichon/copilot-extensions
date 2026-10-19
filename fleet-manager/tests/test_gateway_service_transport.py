"""Real direct TLS, outbound service channels, routing and coherent reload."""

import asyncio
import copy
import json
import os
from dataclasses import replace

import aiohttp
import jwt
import pytest
from joserfc.jwk import ECKey
from yarl import URL

from fleet_manager.gateway_auth import make_proof
from fleet_manager.gateway_contract import GatewayError
from fleet_manager.gateway_service_channel import TransportError, send, target
from fleet_manager.gateway_service_connector import ServiceConnector
from fleet_manager.gateway_service_server import Listener
from gateway_service_transport_fixtures import eventually, running


def test_real_tls_two_outbound_services_and_absence(tmp_path):
    async def scenario():
        async with running(tmp_path) as rig:
            async with await rig.request() as response:
                assert response.status == 503
                assert await response.json() == {"error": "service-unavailable"}
            await rig.connect()
            async with await rig.request("/assets") as response:
                assert response.status == 503
            await rig.connect(1)
            for path, name in (("/api", "a"), ("/assets", "b"),
                               ("/api/echo?opaque=%2f&target=http://ignored.invalid", "a")):
                async with await rig.request(path, headers={
                    "Cookie": "private=synthetic", "X-Forwarded-Host": "foreign.invalid",
                    "X-Identity": "administrator", "Accept": "application/json",
                }) as response:
                    assert response.status == 200
                    result = await response.json()
                    assert result["service"] == name
                    assert "Set-Cookie" not in response.headers
                    assert "X-Identity" not in response.headers
            received = rig.backends[0].requests[-1]
            assert received[1] == "/echo?opaque=%2f&target=http://ignored.invalid"
            assert received[2]["Accept"] == "application/json"
            for name in ("Authorization", "DPoP", "Cookie", "X-Forwarded-Host", "X-Identity"):
                assert name not in received[2]
            async with await rig.request("/assets/index.html") as response:
                assert response.status == 200
                assert response.content_type == "text/html"
                assert "synthetic UX" in await response.text()
            async with await rig.request("/api/echo", "POST", data=b"mutation") as response:
                assert (await response.json())["body"] == "mutation"
            async with rig.session.get(rig.origin + "/api") as response:
                assert response.status == 401
            async with rig.session.get(rig.origin + "/_fleet/challenge") as response:
                assert set(await response.json()) == {"nonce"}
            with pytest.raises(aiohttp.ClientConnectorCertificateError):
                async with aiohttp.ClientSession() as untrusted:
                    await untrusted.get(rig.origin + "/_fleet/challenge")
    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["method", "target", "principal", "key", "scope", "replay"])
def test_real_caller_crypto_binding_and_scope(tmp_path, failure):
    async def scenario():
        async with running(tmp_path) as rig:
            await rig.connect()
            options = {
                "method": {"proof_method": "POST"},
                "target": {"proof_target": rig.origin + "/assets"},
                "principal": {"subject": "service-a", "key": 1},
                "key": {"key": 2}, "scope": {}, "replay": {},
            }[failure]
            if failure == "scope":
                rig.h.document["callers"][0]["slots"] = ["service-b"]
                await rig.update()
            selected = await rig.auth("/api", **options)
            if failure == "replay":
                async with rig.session.get(rig.origin + "/api", headers=selected) as response:
                    assert response.status == 200
            async with rig.session.get(rig.origin + "/api", headers=selected) as response:
                assert response.status in (401, 403)
            assert len(rig.backends[0].requests) == (1 if failure == "replay" else 0)
    asyncio.run(scenario())


@pytest.mark.parametrize("path,status", [
    ("/", 404), ("/api-other", 404), ("/v1/private", 404),
    ("/api/%2e%2e", 400), ("/api//x", 400), ("/api/../x", 400),
    ("/api/x%2fy", 400), ("/api/x\\y", 400),
], ids=["root", "boundary", "reserved", "encoded", "double", "dot", "slash", "backslash"])
def test_canonical_route_refusals(tmp_path, path, status):
    async def scenario():
        async with running(tmp_path) as rig:
            async with rig.session.get(URL(rig.origin + path, encoded=True)) as response:
                assert response.status == status
    asyncio.run(scenario())


def test_method_upgrade_and_request_body_bounds(tmp_path):
    async def scenario():
        async with running(tmp_path) as rig:
            await rig.connect()
            async with rig.session.delete(rig.origin + "/api") as response:
                assert response.status == 405
            async with await rig.request(headers={"Upgrade": "websocket"}) as response:
                assert response.status == 400
            for size, status in ((32768, 200), (32769, 413)):
                async with await rig.request("/api/echo", "POST", data=b"x" * size) as response:
                    assert response.status == status
                    await response.read()
            assert rig.state.pending == 0
    asyncio.run(scenario())


def test_actual_live_reload_noop_invalid_lkg_and_unrelated_slot(tmp_path):
    async def scenario():
        async with running(tmp_path) as rig:
            await rig.connect()
            pid, app = os.getpid(), rig.serving.app
            channel = rig.state.channels["service-a"]
            nonce = rig.state.authority.challenge()
            await asyncio.sleep(0.12)
            assert rig.state.channels["service-a"] is channel
            assert rig.state.authority.challenge() == nonce
            original = copy.deepcopy(rig.h.document)
            rig.policy.write_text('{"invalid":true}')
            await eventually(lambda: rig.state.authority.status().last_error == "invalid-fields")
            assert rig.state.context.manifest.revision == 1
            async with await rig.request() as response:
                assert response.status == 200
            rig.h.document = original
            rig.h.document["manifest"]["slots"][1]["configuration_ref"] = "config/changed"
            await rig.update()
            assert os.getpid() == pid and rig.serving.app is app
            assert rig.state.channels["service-a"] is channel
            async with await rig.request() as response:
                assert response.status == 200
    asyncio.run(scenario())


def test_replacement_fences_old_close_and_old_correlations(tmp_path):
    async def scenario():
        async with running(tmp_path) as rig:
            await rig.connect()
            old = rig.state.channels["service-a"]
            await rig.connect()
            current = rig.state.channels["service-a"]
            assert current is not old
            assert old.closed
            await old.close()
            assert rig.state.channels["service-a"] is current
            assert rig.state.authority.revalidate_service(current.grant).name == "service-a"
            with pytest.raises(TransportError, match="unknown-correlation"):
                current.receive({"type": "end", "id": "0" * 48})
            async with await rig.request() as response:
                assert response.status == 200
    asyncio.run(scenario())


def test_role_withdrawal_and_key_rotation_require_new_admission(tmp_path):
    async def scenario():
        async with running(tmp_path) as rig:
            await rig.connect()
            old = rig.state.channels["service-a"]
            keys = list(rig.h.keys)
            keys[1] = ECKey.generate_key("P-256")
            rig.h.keys = tuple(keys)
            rig.h.document["bindings"][0]["jwk"] = keys[1].as_dict(private=False)
            await rig.update()
            await eventually(lambda: old.closed)
            async with await rig.request() as response:
                assert response.status == 503
            await rig.connect()
            async with await rig.request() as response:
                assert response.status == 200
            rig.h.document["manifest"]["slots"].pop(0)
            rig.h.document["callers"][0]["slots"] = ["service-b"]
            await rig.update()
            async with await rig.request() as response:
                assert response.status == 404
    asyncio.run(scenario())


def test_service_admission_denial_and_single_use_attachment_ticket(tmp_path):
    async def scenario():
        async with running(tmp_path) as rig:
            for subject, key, slot in (("service-b", 2, "service-a"),
                                       ("service-a", 1, "service-b"),
                                       ("caller-a", 3, "service-a")):
                selected = await rig.auth("/_fleet/service-admit", "POST",
                                          subject=subject, key=key)
                scheme = "DP" + "oP "
                async with rig.session.post(
                    rig.origin + "/_fleet/service-admit",
                    json={"slot": slot, "access": selected["Authorization"][len(scheme):],
                          "proof": selected["DPoP"]},
                ) as response:
                    assert response.status == 403
            selected = await rig.auth("/_fleet/service-admit", "POST",
                                      subject="service-a", key=1)
            async with rig.session.post(
                rig.origin + "/_fleet/service-admit",
                json={"slot": "service-a", "access": selected["Authorization"][5:],
                      "proof": selected["DPoP"]},
            ) as response:
                assert response.status == 200
                ticket = (await response.json())["ticket"]
            async with rig.session.ws_connect(rig.origin + "/_fleet/service-channel") as first:
                await send(first, {"ticket": ticket})
                assert json.loads((await first.receive()).data) == {"type": "attached"}
                async with rig.session.ws_connect(rig.origin + "/_fleet/service-channel") as second:
                    await send(second, {"ticket": ticket})
                    assert (await second.receive()).type in (
                        aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED)
                assert rig.state.channels["service-a"].grant.slot == "service-a"
    asyncio.run(scenario())


def test_listener_strict_bootstrap_and_no_implicit_remote_binding(tmp_path):
    async def scenario():
        async with running(tmp_path) as rig:
            listener = rig.listener
            value = {"schema": "fleet-manager.gateway-service-listener", "version": 1,
                     "fleet": listener.fleet, "gateway": listener.gateway,
                     "host": listener.host, "port": 0, "origin": listener.origin,
                     "certificate": listener.certificate, "key": listener.key, "remote": False}
            path = tmp_path / "listener.json"
            path.write_text(json.dumps(value))
            assert Listener.load(path) == listener
            for changed in (
                {"remote": False, "host": "0.0.0.0"}, {"origin": "http://localhost:0"},
                {"origin": "https://user@localhost:0"}, {"origin": "https://localhost:0/"},
                {"origin": "https://localhost:42"}, {"origin": "https://foreign.invalid:0"},
            ):
                with pytest.raises(TransportError):
                    replace(listener, **changed).tls()
            value["unknown"] = True
            path.write_text(json.dumps(value))
            with pytest.raises(TransportError):
                Listener.load(path)
            with pytest.raises(TransportError):
                ServiceConnector(slot="service-a", gateway_origin=rig.origin,
                                 backend_origin="http://127.0.0.1/path",
                                 access_token_callback=lambda: None, proof_key=None)
    asyncio.run(scenario())


def test_method_aware_proof_keeps_legacy_post_default(tmp_path):
    async def scenario():
        async with running(tmp_path) as rig:
            access = rig.access("caller-a", 3)
            options = dict(target_uri=rig.origin + "/api", nonce=rig.state.authority.challenge())
            key = rig.h.keys[3].get_op_key("sign")
            for method in ("POST", "GET"):
                proof = make_proof(key, access, **options,
                                   **({"method": method} if method == "GET" else {}))
                assert jwt.decode(proof, options={"verify_signature": False})["htm"] == method
            for method in ("get", "TRACE", "", "GET\r\n"):
                with pytest.raises(GatewayError):
                    make_proof(key, access, **options, method=method)
            assert target("/x?opaque=%2f") == ("/x", "?opaque=%2f")
    asyncio.run(scenario())

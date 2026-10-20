"""Explicit public PoP authority across actual HTTP/stream/WS verified TLS hops."""

from __future__ import annotations

import asyncio
import json
import ssl
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
import pytest

from fleet_manager.gateway_cloudflare import ingress, main
from fleet_manager.gateway_service_channel import TransportError
from fleet_manager.gateway_service_listener import Listener, external_origin
from fleet_manager.gateway_service_connector import verified_tls
from gateway_service_proxy_fixtures import proxied
from gateway_service_transport_fixtures import Rig


@pytest.mark.parametrize("value", [
    None, "", "http://gateway.example", "https://gateway.example/",
    "https://user@gateway.example", "https://gateway.example?x",
    "https://gateway.example#", "https://gateway.example:0",
    "https://gateway.example:443", "https://GATEWAY.example",
    "https://gateway.example.", "https://-gateway.example", "https://gateway_.example",
    "https://gateway.example:", "https://gateway.example:99999",
    "https://gateway.example\n", "https://[::01]", "https://gateway..example",
], ids=lambda value: repr(value))
def test_external_origin_rejects_noncanonical_authorities(value: object) -> None:
    with pytest.raises(TransportError):
        external_origin(value)


@pytest.mark.parametrize("value", [
    "https://gateway.example", "https://gateway.example:8443",
    "https://localhost:1234", "https://127.0.0.1:1234", "https://[::1]:1234",
])
def test_external_origin_accepts_exact_canonical_authority(value: str) -> None:
    assert external_origin(value) == value


def listener_document(listener: Listener) -> dict:
    return {"schema": "fleet-manager.gateway-service-listener", "version": 1,
            "fleet": listener.fleet, "gateway": listener.gateway, "host": listener.host,
            "port": listener.port, "origin": listener.origin, "certificate": listener.certificate,
            "key": listener.key, "remote": listener.remote}


def test_listener_optional_mode_and_private_tls_remain_independent(tmp_path: Path) -> None:
    listener = Rig(tmp_path).listener
    document = listener_document(listener)
    path = tmp_path / "listener.json"
    path.write_text(json.dumps(document))
    assert Listener.load(path) == listener
    document["external_origin"] = "https://gateway.example"
    path.write_text(json.dumps(document))
    selected = Listener.load(path)
    selected.tls()
    assert selected.external_origin == "https://gateway.example"
    selected.check_host(["gateway.example"])
    for values in ([], ["localhost:0"], ["gateway.example", "gateway.example"]):
        with pytest.raises(TransportError, match="origin-host-mismatch"):
            selected.check_host(values)
    with pytest.raises(TransportError, match="origin-certificate-mismatch"):
        replace(selected, origin="https://foreign.example:0").tls()
    document["external_origin"] = None
    path.write_text(json.dumps(document))
    with pytest.raises(TransportError):
        Listener.load(path)


def test_cloudflare_fragment_and_real_module_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture,
) -> None:
    listener = replace(Rig(tmp_path).listener, port=8443, origin="https://localhost:8443",
                       external_origin="https://gateway.example")
    expected = {"ingress": [
        {"hostname": "gateway.example", "service": "https://localhost:8443",
         "originRequest": {"originServerName": "localhost", "httpHostHeader": "gateway.example",
                           "noTLSVerify": False, "http2Origin": False,
                           "disableChunkedEncoding": False, "caPool": "/config/ca.pem"}},
        {"service": "http_status:404"},
    ]}
    assert ingress(listener, ca_pool="/config/ca.pem") == expected
    document = listener_document(listener) | {"external_origin": listener.external_origin}
    path = tmp_path / "listener.json"
    path.write_text(json.dumps(document))
    assert main(["--tls-config", str(path), "--ca-pool", "/config/ca.pem"]) == 0
    assert json.loads(capsys.readouterr().out) == expected
    assert "caPool" not in ingress(listener)["ingress"][0]["originRequest"]
    for selected in (replace(listener, external_origin=None),
                     replace(listener, external_origin="https://gateway.example:8443"),
                     replace(listener, external_origin="https://127.0.0.1"),
                     replace(listener, external_origin="https://localhost"),
                     replace(listener, port=0, origin="https://localhost:0")):
        with pytest.raises(TransportError):
            ingress(selected)
    for ca in ("", "ca\ninjection", "x" * 4097):
        with pytest.raises(TransportError, match="invalid-ca-pool"):
            ingress(listener, ca_pool=ca)
    document["external_origin"] = "http://invalid.example"
    path.write_text(json.dumps(document))
    assert main(["--tls-config", str(path)]) == 1
    assert capsys.readouterr().out == ""
    assert "http://invalid.example" not in caplog.text
    assert "cloudflare-ingress-refused" in caplog.text


@pytest.mark.parametrize("private", [
    "https://[private-origin.example]", "https://private-origin.example:bad", None, {},
])
def test_renderer_validates_private_origin_before_parsing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture,
    private: object,
) -> None:
    listener = Rig(tmp_path).listener
    document = listener_document(listener) | {
        "external_origin": "https://gateway.example", "origin": private,
    }
    path = tmp_path / "listener.json"
    path.write_text(json.dumps(document))
    assert main(["--tls-config", str(path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "Traceback" not in captured.err
    assert "private-origin.example" not in caplog.text
    assert "cloudflare-ingress-refused code=invalid-origin" in caplog.text


@pytest.mark.parametrize("field", ["fleet", "gateway"])
@pytest.mark.parametrize("value", ["private/identifier", None, {}])
def test_renderer_classifies_listener_identifier_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture,
    field: str, value: object,
) -> None:
    document = listener_document(Rig(tmp_path).listener) | {
        "external_origin": "https://gateway.example", field: value,
    }
    path = tmp_path / "listener.json"
    path.write_text(json.dumps(document))
    assert main(["--tls-config", str(path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "Traceback" not in captured.err
    assert "private/identifier" not in caplog.text
    assert "cloudflare-ingress-refused code=invalid-identifier" in caplog.text


def test_dual_tls_proxy_routes_two_outbound_channels_and_unfinished_stream(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with proxied(tmp_path) as (rig, proxy):
            assert rig.serving.origin != rig.origin
            assert rig.state.origin == rig.origin == rig.listener.external_origin
            await rig.connect()
            await rig.connect(1)
            for path, name in (("/api/echo?opaque=%2f", "a"), ("/assets/index.html", "b")):
                async with await rig.request(path, headers={
                    "Forwarded": "host=attacker.example;proto=http",
                    "X-Forwarded-Host": "attacker.example", "X-Forwarded-Proto": "http",
                    "Cf-Access-Jwt-Assertion": "not-fleet-authority",
                }) as response:
                    assert response.status == 200
                    assert response.headers["Cache-Control"] == "no-store"
                    if name == "a":
                        assert (await response.json())["path"] == "/echo?opaque=%2f"
                    else:
                        assert response.content_type == "text/html"
                        assert "synthetic UX" in await response.text()
            assert "/_fleet/service-admit" in proxy.paths
            assert "/_fleet/service-channel" in proxy.paths
            received = rig.backends[0].requests[-1][2]
            assert not any(k.lower().startswith(("cf-", "x-forwarded")) for k in received)
            assert "Forwarded" not in received
            async with await rig.request("/api/events") as response:
                assert response.status == 200
                first = await asyncio.wait_for(response.content.readexactly(13), 2)
                assert first == b"data: first\n\n"
                assert not rig.backends[0].stopped.is_set()
                await rig.backends[0].stream.put(b"data: second\n\n")
                await rig.backends[0].stream.put(None)
                assert await response.read() == b"data: second\n\n"
            with pytest.raises(aiohttp.ClientConnectorCertificateError):
                async with aiohttp.ClientSession() as untrusted:
                    await untrusted.get(rig.origin + "/_fleet/challenge")
            private = urlsplit(rig.serving.origin)
            for context, name in ((verified_tls(None), "localhost"),
                                  (verified_tls(rig.ca), "gateway.example")):
                with pytest.raises(ssl.SSLCertVerificationError):
                    await asyncio.open_connection(
                        "127.0.0.1", private.port, ssl=context, server_hostname=name)
            channel = rig.state.channels["service-a"]
            app = rig.serving.app
            rig.h.document["manifest"]["slots"][1]["configuration_ref"] = "config/changed"
            await rig.update()
            assert rig.serving.app is app and rig.state.channels["service-a"] is channel
            async with await rig.request() as response:
                assert response.status == 200
    asyncio.run(scenario())


def test_proxy_headers_cannot_grant_identity_or_rebind_proofs(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with proxied(tmp_path) as (rig, proxy):
            await rig.connect()
            async with rig.session.get(rig.origin + "/api", headers={
                "Cf-Access-Jwt-Assertion": "synthetic-identity",
                "Cf-Access-Authenticated-User-Email": "admin@example.invalid",
                "X-Identity": "caller-a", "Forwarded": "host=localhost;proto=https",
            }) as response:
                assert response.status == 401
            private_proof = await rig.auth("/api", proof_target=rig.serving.origin + "/api")
            async with rig.session.get(rig.origin + "/api", headers=private_proof) as response:
                assert response.status == 401
            service = await rig.auth(
                "/_fleet/service-admit", "POST", subject="service-a", key=1,
                proof_target=rig.serving.origin + "/_fleet/service-admit")
            async with rig.session.post(rig.origin + "/_fleet/service-admit", json={
                "slot": "service-a", "access": service["Authorization"][5:], "proof": service["DPoP"],
            }) as response:
                assert response.status == 401
            proxy.host_override = ["attacker.example"]
            selected = await rig.auth("/api")
            async with rig.session.get(rig.origin + "/api", headers=selected) as response:
                assert response.status == 400
                assert await response.json() == {"error": "origin-host-mismatch"}
            private = urlsplit(rig.serving.origin)
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", private.port, ssl=verified_tls(rig.ca), server_hostname="localhost")
            try:
                host = urlsplit(rig.origin).netloc
                writer.write(
                    f"GET /_fleet/challenge HTTP/1.1\r\nHost: {host}\r\nHost: {host}\r\n"
                    "Connection: close\r\n\r\n".encode("ascii"))
                await writer.drain()
                assert (await asyncio.wait_for(reader.readline(), 2)).split()[1] == b"400"
            finally:
                writer.close()
                await writer.wait_closed()
            proxy.host_override = None
            async with await rig.request() as response:
                assert response.status == 200
            assert len(rig.backends[0].requests) == 1
    asyncio.run(scenario())

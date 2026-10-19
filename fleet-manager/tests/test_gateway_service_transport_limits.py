"""Actual wire refusal boundaries and the deliberately narrow CLI selection."""

import asyncio
import base64
import json
import sys
import time
from dataclasses import replace
from types import SimpleNamespace
from urllib.parse import urlsplit

import aiohttp
import pytest

from fleet_manager.__main__ import main
from fleet_manager.gateway_service_channel import send
from fleet_manager.gateway_service_connector import verified_tls
from gateway_service_transport_fixtures import eventually, running


async def admitted(rig):
    selected = await rig.auth("/_fleet/service-admit", "POST", subject="service-a", key=1)
    async with rig.session.post(
        rig.origin + "/_fleet/service-admit",
        json={"slot": "service-a", "access": selected["Authorization"][5:],
              "proof": selected["DPoP"]},
    ) as response:
        assert response.status == 200
        return (await response.json())["ticket"]


def test_unverified_socket_bound_and_first_message_deadline(tmp_path):
    async def scenario():
        async with running(tmp_path) as rig:
            sockets = []
            try:
                for _ in range(8):
                    sockets.append(await rig.session.ws_connect(
                        rig.origin + "/_fleet/service-channel"))
                assert rig.state.unverified == 8
                with pytest.raises(aiohttp.WSServerHandshakeError) as caught:
                    await rig.session.ws_connect(rig.origin + "/_fleet/service-channel")
                assert caught.value.status == 429
                message = await asyncio.wait_for(sockets[0].receive(), 7)
                assert message.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED)
            finally:
                await asyncio.gather(*(ws.close() for ws in sockets))
            await eventually(lambda: rig.state.unverified == 0)
    asyncio.run(scenario())


def test_expired_attachment_ticket_never_becomes_channel(tmp_path):
    async def scenario():
        async with running(tmp_path) as rig:
            token = await admitted(rig)
            ticket = rig.state.tickets[token]
            assert 0 < ticket.deadline - time.monotonic() <= 30
            rig.state.tickets[token] = replace(ticket, deadline=time.monotonic() - 1)
            async with rig.session.ws_connect(rig.origin + "/_fleet/service-channel") as ws:
                await send(ws, {"ticket": token})
                assert (await ws.receive()).type in (
                    aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED)
            assert not rig.state.channels
            assert token not in rig.state.tickets
    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["unknown", "oversize", "sequence", "frame-limit"])
def test_malformed_real_response_frames_close_generation(tmp_path, mode):
    async def scenario():
        async with running(tmp_path) as rig:
            token = await admitted(rig)
            async with rig.session.ws_connect(rig.origin + "/_fleet/service-channel") as ws:
                await send(ws, {"ticket": token})
                assert json.loads((await ws.receive()).data) == {"type": "attached"}
                request = asyncio.create_task(rig.request("/api"))
                response = None
                try:
                    forwarded = json.loads((await ws.receive()).data)
                    correlation = forwarded["id"]
                    if mode == "unknown":
                        await send(ws, {"type": "end", "id": "f" * 48})
                    elif mode == "frame-limit":
                        await ws.send_str("x" * 65537)
                    else:
                        await send(ws, {"type": "headers", "id": correlation,
                                        "status": 200, "headers": {}})
                        await send(ws, {
                            "type": "chunk", "id": correlation,
                            "seq": 2 if mode == "sequence" else 1,
                            "data": base64.b64encode(
                                b"x" * (16385 if mode == "oversize" else 1)).decode(),
                        })
                    await eventually(lambda: rig.state.channels.get("service-a") is None)
                    try:
                        response = await request
                        if response.status == 200:
                            with pytest.raises(aiohttp.ClientError):
                                await response.read()
                        else:
                            assert response.status == 502
                    except aiohttp.ClientError:
                        assert request.done()
                    await eventually(lambda: rig.state.pending == 0)
                finally:
                    if response is not None:
                        response.close()
                    request.cancel()
                    await asyncio.gather(request, return_exceptions=True)
    asyncio.run(scenario())


def test_host_headers_cannot_select_authenticated_origin(tmp_path):
    async def scenario():
        async with running(tmp_path) as rig:
            await rig.connect()
            async with await rig.request(headers={
                "Host": "foreign.invalid", "Forwarded": "host=foreign.invalid;proto=http",
            }) as response:
                assert response.status == 200
            selected = await rig.auth("/api", proof_target="https://foreign.invalid/api")
            async with rig.session.get(
                rig.origin + "/api", headers={**selected, "Host": "foreign.invalid"},
            ) as response:
                assert response.status == 401
    asyncio.run(scenario())


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("header,value", [
    ("If-Match", '"stale"'),
    ("If-Unmodified-Since", "Wed, 21 Oct 2015 07:28:00 GMT"),
])
def test_conditional_mutation_reaches_backend_with_precondition(
    tmp_path, method, header, value,
):
    async def scenario():
        async with running(tmp_path) as rig:
            rig.h.document["manifest"]["slots"][0]["routes"][0]["methods"].append(method)
            rig.h.document["callers"][0]["methods"].append(method)
            await rig.update()
            await rig.connect()
            async with await rig.request(
                "/api/conditional", method, headers={header: value},
            ) as response:
                assert response.status == 412
                assert await response.json() == {"error": "stale-precondition"}
            forwarded = {key.lower(): item
                         for key, item in rig.backends[0].requests[-1][2].items()}
            assert forwarded[header.lower()] == value
            assert rig.backends[0].mutations == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("header", [
    "If-Match", "If-Unmodified-Since", "If-None-Match", "If-Modified-Since",
])
def test_connection_nominated_precondition_refuses_instead_of_being_dropped(tmp_path, header):
    async def scenario():
        async with running(tmp_path) as rig:
            await rig.connect()
            async with await rig.request("/api/conditional", "POST", headers={
                header: "stale", "Connection": header,
            }) as response:
                assert response.status == 400
                assert await response.json() == {"error": "invalid-headers"}
            assert not rig.backends[0].requests
    asyncio.run(scenario())


def test_buffered_head_omits_unknown_representation_length(tmp_path):
    async def scenario():
        async with running(tmp_path) as rig:
            rig.h.document["manifest"]["slots"][1]["routes"][0]["methods"].append("HEAD")
            rig.h.document["callers"][0]["methods"].append("HEAD")
            await rig.update()
            await rig.connect(1)
            async with await rig.request("/assets/index.html") as response:
                assert response.status == 200
                assert len(await response.read()) > 0
                assert int(response.headers["Content-Length"]) > 0
            async with await rig.request("/assets/index.html", "HEAD") as response:
                assert response.status == 200
                assert await response.read() == b""
                assert "Content-Length" not in response.headers
    asyncio.run(scenario())


def test_cacheable_variants_retain_backend_freshness_and_variation(tmp_path):
    async def scenario():
        async with running(tmp_path) as rig:
            await rig.connect()
            for language in ("en", "fr"):
                async with await rig.request(
                    "/api/cacheable", headers={"Accept-Language": language},
                ) as response:
                    assert response.status == 200
                    assert await response.text() == language
                    assert response.headers["Cache-Control"] == "public, max-age=300"
                    assert response.headers["Vary"] == "Accept-Language"
                    assert response.headers["Date"] == "Wed, 21 Oct 2015 07:28:00 GMT"
                    assert response.headers["Age"] == "290"
                    assert response.headers["Expires"] == "Wed, 21 Oct 2015 07:33:00 GMT"
    asyncio.run(scenario())


def test_http_10_stream_request_refuses_before_backend_dispatch(tmp_path):
    async def scenario():
        async with running(tmp_path) as rig:
            await rig.connect()
            selected = await rig.auth("/api/events")
            peer = urlsplit(rig.origin)
            reader, writer = await asyncio.open_connection(
                "127.0.0.1", peer.port, ssl=verified_tls(rig.ca),
                server_hostname=peer.hostname)
            try:
                head = "GET /api/events HTTP/1.0\r\nHost: localhost\r\n"
                head += "".join(f"{name}: {value}\r\n" for name, value in selected.items())
                writer.write((head + "\r\n").encode("ascii"))
                await writer.drain()
                response = await asyncio.wait_for(reader.read(), 3)
                assert response.startswith(b"HTTP/1.0 505 ")
                assert response.endswith(b'{"error": "stream-http-version-required"}')
                assert not rig.backends[0].requests
                assert rig.state.pending == 0
            finally:
                writer.close()
                await writer.wait_closed()
    asyncio.run(scenario())


def test_refusals_do_not_log_access_proof_or_cookie_values(tmp_path, caplog):
    async def scenario():
        async with running(tmp_path) as rig:
            selected = await rig.auth("/api")
            selected["DPoP"] = "synthetic-invalid-proof-value"
            selected["Cookie"] = "synthetic-private-cookie-value"
            async with rig.session.get(rig.origin + "/api", headers=selected) as response:
                assert response.status == 401
                assert await response.json() == {"error": "authorization-denied"}
            for value in selected.values():
                assert value not in caplog.text
            assert "service-request-refused code=" in caplog.text
    asyncio.run(scenario())


def test_cli_profiles_are_mutually_exclusive_and_legacy_dispatch_is_unchanged(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "fleet_manager.gateway_server", SimpleNamespace(
        serve=lambda path: calls.append(("legacy", path.name)) or 0))
    monkeypatch.setitem(sys.modules, "fleet_manager.gateway_service_server", SimpleNamespace(
        serve=lambda policy, tls: calls.append(("service", policy.name, tls.name)) or 0))
    assert main(["serve", "--role", "gateway", "--config", "old.json"]) == 0
    assert calls == [("legacy", "old.json")]
    assert main(["serve", "--role", "gateway", "--service-policy", "policy.json",
                 "--tls-config", "listener.json"]) == 0
    assert calls[-1] == ("service", "policy.json", "listener.json")
    count = len(calls)
    for arguments in (
        ["--config", "old.json", "--service-policy", "policy.json"],
        ["--service-policy", "policy.json"],
        ["--config", "old.json", "--tls-config", "listener.json"],
    ):
        assert main(["serve", "--role", "gateway", *arguments]) == 1
        assert len(calls) == count

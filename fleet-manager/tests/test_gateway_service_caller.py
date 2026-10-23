"""Real TLS/PoP caller requests and finite disconnect/uncertain-result behavior."""

import asyncio
import json
import ssl
from contextlib import asynccontextmanager

import pytest
from aiohttp import web
from joserfc.jwk import ECKey

from fleet_manager.gateway_service_caller import ServiceCaller
from fleet_manager.gateway_service_channel import BODY_BYTES, CHANNEL_REQUESTS, TransportError
from gateway_fixtures import Fixtures
from gateway_service_transport_fixtures import eventually, running


def caller(rig, **options):
    async def access():
        return rig.access("caller-a", 3)
    return ServiceCaller(
        gateway_origin=rig.origin, gateway_ca=rig.ca,
        proof_key=rig.h.keys[3].get_op_key("sign"),
        access_token_callback=options.pop("access_token_callback", access), **options)


def test_current_credentials_real_tls_method_target_and_opaque_query(tmp_path, monkeypatch):
    async def scenario():
        async with running(tmp_path) as rig:
            await rig.connect()
            tokens = []
            challenges = []
            current_challenge = rig.state.authority.challenge

            def challenge():
                challenges.append(True)
                return current_challenge()

            monkeypatch.setattr(rig.state.authority, "challenge", challenge)

            async def access():
                tokens.append(rig.access("caller-a", 3))
                return tokens[-1]

            client = caller(rig, access_token_callback=access)
            monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
            for method, path, body in (
                ("GET", "/api/echo?opaque=%2f&url=https://other.invalid", b""),
                ("POST", "/api/echo", b"changed"),
            ):
                response = await client.request(
                    method, path, body=body,
                    request_headers=(("Accept", "application/json"),))
                assert response.status == 200
                assert json.loads(response.body)["body"] == body.decode()
                received = rig.backends[0].requests[-1]
                assert received[:2] == (method, path.removeprefix("/api"))
                assert received[3] == body
                assert "Authorization" not in received[2] and "DPoP" not in received[2]
                assert "Set-Cookie" not in dict(response.headers)
            assert len(tokens) == len(challenges) == 2 and tokens[0] != tokens[1]
            assert client._active == 0
    asyncio.run(scenario())


def test_authority_withdrawal_and_recovery_is_a_new_request(tmp_path):
    async def scenario():
        async with running(tmp_path) as rig:
            await rig.connect()
            client = caller(rig)
            assert (await client.request("GET", "/api")).status == 200
            rig.h.document["callers"][0]["slots"] = ["service-b"]
            await rig.update()
            assert (await client.request("POST", "/api", body=b"denied")).status == 403
            assert len(rig.backends[0].requests) == 1
            rig.h.document["callers"][0]["slots"] = ["service-a", "service-b"]
            await rig.update()
            assert (await client.request("GET", "/api")).status == 200
            assert len(rig.backends[0].requests) == 2
    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["exception", "invalid", "principal", "key", "tls"])
def test_credentials_and_trust_fail_closed_without_backend_or_secret_diagnostics(tmp_path, failure):
    async def scenario():
        async with running(tmp_path) as rig:
            await rig.connect()

            async def access():
                if failure == "exception":
                    raise ValueError("synthetic-private-detail")
                if failure == "invalid":
                    return "synthetic\r\nprivate"
                return rig.access("service-a" if failure == "principal" else "caller-a", 3)

            client = caller(rig, access_token_callback=access)
            if failure == "key":
                client._key = rig.h.keys[2].get_op_key("sign")
            if failure == "tls":
                client = ServiceCaller(
                    gateway_origin=rig.origin, access_token_callback=access,
                    proof_key=rig.h.keys[3].get_op_key("sign"))
            if failure in ("exception", "invalid", "tls"):
                with pytest.raises(TransportError) as caught:
                    await client.request("POST", "/api")
                assert caught.value.code != "outcome-unknown"
                assert "synthetic" not in str(caught.value)
            else:
                assert (await client.request("GET", "/api")).status in (401, 403)
            assert rig.backends[0].requests == []
    asyncio.run(scenario())


@pytest.mark.parametrize("method,code", [("GET", "gateway-timeout"), ("POST", "outcome-unknown")])
def test_total_deadline_no_retry_and_no_retained_slot(tmp_path, method, code):
    async def scenario():
        async with running(tmp_path) as rig:
            await rig.connect()
            client = caller(rig, timeout=0.5)
            with pytest.raises(TransportError, match=code):
                await client.request(method, "/api/hold")
            assert rig.backends[0].started.is_set()
            assert len(rig.backends[0].requests) == 1
            assert client._active == 0
            rig.backends[0].release.set()
            assert (await client.request("GET", "/api")).status == 200
    asyncio.run(scenario())


def test_lost_mutation_is_not_reissued(tmp_path):
    async def scenario():
        async with running(tmp_path) as rig:
            await rig.connect()
            result = await caller(rig).request("POST", "/api/lost-mutation", body=b"original-id")
            assert result.status == 502
            assert json.loads(result.body) == {"error": "outcome-unknown"}
            assert rig.backends[0].mutations == 1
            assert len(rig.backends[0].requests) == 1
    asyncio.run(scenario())


def test_response_bound_and_cancellation_cleanup(tmp_path):
    async def scenario():
        async with running(tmp_path) as rig:
            await rig.connect()
            client = caller(rig)
            with pytest.raises(TransportError, match="gateway-response-limit"):
                await client.request("GET", "/api/asset.js", response_limit=100)
            pending = asyncio.create_task(client.request("POST", "/api/hold"))
            try:
                await asyncio.wait_for(rig.backends[0].started.wait(), 3)
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
                assert client._active == 0
                await eventually(lambda: rig.state.pending == 0)
            finally:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
                rig.backends[0].release.set()
    asyncio.run(scenario())


def test_concurrent_request_bound_has_no_waiting_queue(tmp_path):
    async def scenario():
        async with running(tmp_path) as rig:
            entered = 0
            release = asyncio.Event()

            async def access():
                nonlocal entered
                entered += 1
                await release.wait()
                return rig.access("caller-a", 3)

            client = caller(rig, access_token_callback=access)
            tasks = [asyncio.create_task(client.request("GET", "/api"))
                     for _ in range(CHANNEL_REQUESTS)]
            try:
                await eventually(lambda: entered == CHANNEL_REQUESTS)
                with pytest.raises(TransportError, match="request-limit"):
                    await client.request("GET", "/api")
                assert entered == CHANNEL_REQUESTS
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            assert client._active == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("options", [
    {"method": "get"}, {"method": "CONNECT"}, {"request_target": "https://foreign.invalid"},
    {"request_target": "/api/../private"}, {"body": b"x" * (BODY_BYTES + 1)},
    {"request_headers": (("Authorization", "synthetic"),)},
    {"request_headers": (("Accept", "ok"), ("accept", "again"))},
    {"response_limit": True}, {"response_limit": 0}, {"body": "not-bytes"},
])
def test_invalid_requests_do_not_acquire_credentials_or_contact_gateway(tmp_path, options):
    async def scenario():
        async with running(tmp_path) as rig:
            async def access():
                pytest.fail("invalid request reached credential callback")
            client = caller(rig, access_token_callback=access)
            with pytest.raises(TransportError):
                await client.request(**({"method": "GET", "request_target": "/api"} | options))
            assert client._active == 0
            assert rig.backends[0].requests == []
    asyncio.run(scenario())


@asynccontextmanager
async def endpoint(root, handler):
    Fixtures(root)
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    runner = web.AppRunner(app, access_log=None, shutdown_timeout=1,
                           handler_cancellation=True)
    try:
        await runner.setup()
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(str(root / "tls.pem"), str(root / "tls-key.pem"))
        await web.TCPSite(runner, "127.0.0.1", 0, ssl_context=tls).start()
        yield f"https://localhost:{runner.addresses[0][1]}"
    finally:
        await runner.cleanup()


@pytest.mark.parametrize("failure", [
    "status", "oversized", "shape", "utf8", "nonce", "encoded", "disconnect",
])
def test_challenge_refusal_never_emits_token_or_attempts_application(tmp_path, failure):
    async def scenario():
        paths = []

        async def handle(request):
            paths.append(request.path)
            assert "Authorization" not in request.headers and "DPoP" not in request.headers
            if failure == "status":
                return web.Response(status=302, headers={"Location": "/foreign"})
            if failure == "oversized":
                return web.Response(body=b"x" * 16385)
            if failure == "utf8":
                return web.Response(body=b"\xff")
            if failure == "encoded":
                return web.Response(body=b"encoded", headers={"Content-Encoding": "gzip"})
            if failure == "disconnect":
                request.transport.close()
                return web.Response()
            return web.json_response({"nonce": "short"} if failure == "nonce" else {"extra": True})

        async def access():
            pytest.fail("invalid challenge acquired credentials")

        async with endpoint(tmp_path, handle) as selected:
            client = ServiceCaller(
                gateway_origin=selected, gateway_ca=str(tmp_path / "tls.pem"),
                access_token_callback=access,
                proof_key=ECKey.generate_key("P-256").get_op_key("sign"))
            with pytest.raises(TransportError) as caught:
                await client.request("POST", "/api")
            assert caught.value.code != "outcome-unknown"
            assert paths == ["/_fleet/challenge"]
    asyncio.run(scenario())


@pytest.mark.parametrize("timeout", [0, -1, True, float("nan"), float("inf"), 61])
def test_invalid_timeout_is_rejected_before_requests(timeout):
    async def access():
        pytest.fail("constructor acquired credentials")
    with pytest.raises(TransportError, match="invalid-timeout"):
        ServiceCaller(gateway_origin="https://example.invalid",
                      access_token_callback=access, proof_key=None, timeout=timeout)


@pytest.mark.parametrize("failure", ["redirect", "encoded", "stream-limit", "disconnect"])
@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "DELETE"])
def test_response_failures_never_retry_or_redirect_and_mutations_are_uncertain(
    tmp_path, failure, method,
):
    async def scenario():
        paths = []
        calls = 0

        async def handle(request):
            paths.append(request.path)
            if request.path == "/_fleet/challenge":
                assert "Authorization" not in request.headers
                return web.json_response({"nonce": "n" * 43})
            scheme = "DP" + "oP "
            assert request.headers["Authorization"] == scheme + "synthetic-access"
            assert request.headers["DPoP"].count(".") == 2
            if failure == "redirect":
                return web.Response(status=302, headers={"Location": "/foreign"})
            if failure == "encoded":
                return web.Response(body=b"encoded", headers={"Content-Encoding": "gzip"})
            if failure == "disconnect":
                request.transport.close()
                return web.Response()
            response = web.StreamResponse()
            await response.prepare(request)
            await response.write(b"x" * 200)
            await response.write_eof()
            return response

        async def access():
            nonlocal calls
            calls += 1
            return "synthetic-access"

        async with endpoint(tmp_path, handle) as selected:
            client = ServiceCaller(
                gateway_origin=selected, gateway_ca=str(tmp_path / "tls.pem"),
                access_token_callback=access,
                proof_key=ECKey.generate_key("P-256").get_op_key("sign"))
            with pytest.raises(TransportError) as caught:
                await client.request(method, "/api?opaque=%2f", response_limit=100)
            assert (caught.value.code == "outcome-unknown") == (method != "GET")
            assert paths == ["/_fleet/challenge", "/api"]
            assert calls == 1 and client._active == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("method,status", [("HEAD", 200), ("GET", 304)])
def test_bodyless_responses_bound_actual_bytes_not_representation_length(tmp_path, method, status):
    async def scenario():
        async def handle(request):
            if request.path == "/_fleet/challenge":
                return web.json_response({"nonce": "n" * 43})
            return web.Response(status=status, headers={"Content-Length": "100000000"})

        async def access():
            return "synthetic-access"

        async with endpoint(tmp_path, handle) as selected:
            client = ServiceCaller(
                gateway_origin=selected, gateway_ca=str(tmp_path / "tls.pem"),
                access_token_callback=access,
                proof_key=ECKey.generate_key("P-256").get_op_key("sign"))
            response = await client.request(method, "/api", response_limit=100)
            assert response.status == status and response.body == b""
    asyncio.run(scenario())

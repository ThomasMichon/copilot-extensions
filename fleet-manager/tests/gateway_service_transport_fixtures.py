"""In-process real TLS/HTTP/WebSocket fixtures, synthetic identities only."""

from __future__ import annotations

import asyncio
import contextlib
import time
from pathlib import Path
from types import SimpleNamespace

import aiohttp
from aiohttp import web
from yarl import URL

from fleet_manager import planning
from fleet_manager.gateway_auth import make_proof
from fleet_manager.gateway_service_connector import ServiceConnector, verified_tls
from fleet_manager.gateway_service_server import Listener, STATE, start
from gateway_fixtures import Fixtures
from test_gateway_service_policy import Harness
from test_runtime_identity import pair


async def eventually(predicate, timeout=3):
    async def poll():
        while not predicate():
            await asyncio.sleep(0.01)
    await asyncio.wait_for(poll(), timeout)


class Backend:
    def __init__(self, name):
        self.name = name
        self.requests = []
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()
        self.release = asyncio.Event()
        self.stream = asyncio.Queue()
        self.writes = 0
        self.mutations = 0
        self.runner = None

    async def handle(self, request):
        body = await request.read()
        self.requests.append((request.method, request.raw_path, dict(request.headers), body))
        if request.path == "/lost-mutation":
            self.mutations += 1
            request.transport.close()
            return web.Response()
        if request.path == "/conditional":
            if (request.headers.get("If-Match", '"current"') != '"current"'
                    or request.headers.get("If-Unmodified-Since", "current") != "current"):
                return web.json_response({"error": "stale-precondition"}, status=412)
            self.mutations += 1
            return web.json_response({"mutations": self.mutations})
        if request.path == "/events":
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            try:
                await response.write(b"data: first\n\n")
                self.started.set()
                while True:
                    value = await self.stream.get()
                    if value is None:
                        break
                    await response.write(value)
                await response.write_eof()
                return response
            finally:
                self.stopped.set()
        if request.path == "/large":
            response = web.StreamResponse()
            await response.prepare(request)
            try:
                for _ in range(1024):
                    await response.write(b"x" * 16384)
                    self.writes += 1
                await response.write_eof()
                return response
            finally:
                self.stopped.set()
        if request.path == "/hold":
            self.started.set()
            await self.release.wait()
        if request.path == "/index.html":
            return web.Response(text="<html><body>synthetic UX</body></html>",
                                content_type="text/html")
        if request.path == "/asset.js":
            return web.Response(body=b"x" * 40000, content_type="text/javascript")
        if request.path == "/cacheable":
            return web.Response(text=request.headers.get("Accept-Language", "en"), headers={
                "Cache-Control": "public, max-age=300", "Vary": "Accept-Language",
                "Date": "Wed, 21 Oct 2015 07:28:00 GMT", "Age": "290",
                "Expires": "Wed, 21 Oct 2015 07:33:00 GMT",
            })
        if request.path == "/redirect":
            return web.Response(status=int(request.query["status"]),
                                headers={"Location": "/login"})
        if request.path == "/not-modified":
            return web.Response(status=304, headers={"ETag": '"unchanged"'})
        if request.path == "/encoded":
            return web.Response(
                body=b"unsupported-encoded-bytes",
                headers=[("Content-Encoding", value) for value in request.query.getall("value")])
        return web.json_response(
            {"service": self.name, "path": request.raw_path, "body": body.decode()},
            headers={"Set-Cookie": "fixture=not-forwarded", "X-Identity": "not-forwarded",
                     "Cache-Control": "no-store"})

    async def start(self):
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self.handle)
        self.runner = web.AppRunner(app, access_log=None, handler_cancellation=True,
                                    shutdown_timeout=1)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", 0).start()
        return f"http://127.0.0.1:{self.runner.addresses[0][1]}"


class Rig:
    def __init__(self, root: Path):
        self.root = root
        fixture = Fixtures(root)
        self.ca = str(root / "tls.pem")
        self.h = Harness()
        self.h.clock.value = int(time.time())
        self.h.document["identity"]["trust_valid_until"] = int(time.time()) + 86400
        self.h.document["callers"][0]["slots"].append("service-b")
        self.h.document["manifest"]["slots"][0]["routes"][0]["capabilities"].append(
            "response-stream")
        self.policy = root / "policy.json"
        self.listener = Listener("fleet-a", "gateway-a", "127.0.0.1", 0,
                                 "https://localhost:0", str(fixture.root / "tls.pem"),
                                 str(fixture.root / "tls-key.pem"))
        self.backends = [Backend("a"), Backend("b")]
        self.origins = []
        self.connectors = []
        self.tasks = []
        self.serving = None
        self.session = None
        self.nonce = None

    def save(self):
        self.policy.write_bytes(planning.canonical(self.h.document))

    async def start(self, *, identity_provider=None, local_handlers=()):
        self.save()
        for backend in self.backends:
            self.origins.append(await backend.start())
        self.serving = await start(self.listener, self.policy, reload_interval=0.05,
                                   clock=self.h.clock, identity_provider=identity_provider,
                                   local_handlers=local_handlers)
        self.origin = self.serving.origin
        self.state = self.serving.app[STATE]
        self.session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=verified_tls(self.ca)),
            trust_env=False, cookie_jar=aiohttp.DummyCookieJar())
        return self

    def access(self, subject, key):
        return pair((self.h.keys[0], self.h.keys[key]), SimpleNamespace(nonce="unused"),
                    now=self.h.clock.value, access={"sub": subject})[0]

    async def auth(self, path, method="GET", *, subject="caller-a", key=3,
                   proof_method=None, proof_target=None):
        if self.nonce is None:
            async with self.session.get(self.origin + "/_fleet/challenge") as response:
                assert response.status == 200
                self.nonce = (await response.json())["nonce"]
        access = self.access(subject, key)
        proof = make_proof(
            self.h.keys[key].get_op_key("sign"), access, nonce=self.nonce,
            target_uri=proof_target or self.origin + path.split("?")[0],
            method=proof_method or method)
        scheme = "DP" + "oP "
        result = {"Authorization": scheme + access, "DPoP": proof}
        assert result["Authorization"].endswith(access)
        return result

    async def request(self, path="/api", method="GET", **kwargs):
        selected = await self.auth(path, method)
        selected.update(kwargs.pop("headers", {}))
        return await self.session.request(
            method, URL(self.origin + path, encoded=True), headers=selected, **kwargs)

    async def connect(self, index=0):
        async def access():
            return self.access("service-a" if index == 0 else "service-b", index + 1)
        connector = ServiceConnector(
            slot="service-a" if index == 0 else "service-b", gateway_origin=self.origin,
            backend_origin=self.origins[index], access_token_callback=access,
            proof_key=self.h.keys[index + 1].get_op_key("sign"), gateway_ca=self.ca)
        task = asyncio.create_task(connector.connect_once())
        self.connectors.append(connector)
        self.tasks.append(task)
        await asyncio.wait_for(connector.connected.wait(), 3)
        return connector

    async def update(self):
        self.h.document["manifest"]["revision"] += 1
        self.save()
        revision = self.h.document["manifest"]["revision"]
        await eventually(lambda: self.state.context.manifest.revision == revision)
        self.nonce = None

    async def close(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.session:
            await self.session.close()
        if self.serving:
            await self.serving.close()
        for backend in self.backends:
            backend.release.set()
            if backend.runner:
                await backend.runner.cleanup()


@contextlib.asynccontextmanager
async def running(root):
    rig = Rig(root)
    try:
        await rig.start()
        yield rig
    finally:
        await rig.close()

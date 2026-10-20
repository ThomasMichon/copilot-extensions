"""Synthetic verified TLS reverse proxy, not a live cloudflared witness."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web
from yarl import URL

from fleet_manager.gateway_service_connector import verified_tls
from gateway_service_transport_fixtures import Rig


class Proxy:
    def __init__(self, rig: Rig) -> None:
        self.rig = rig
        self.origin = ""
        self.runner: web.AppRunner | None = None
        self.session: aiohttp.ClientSession | None = None
        self.host_override: list[str] | None = None
        self.paths: list[str] = []

    async def handle(self, request: web.Request) -> web.StreamResponse:
        assert self.session is not None and self.rig.serving is not None
        self.paths.append(request.raw_path)
        target = URL(self.rig.serving.origin + request.raw_path, encoded=True)
        hosts = self.host_override or [urlsplit(self.origin).netloc]
        headers = [("Host", value) for value in hosts]
        if request.headers.get("Upgrade", "").lower() == "websocket":
            async with self.session.ws_connect(target, headers=headers) as upstream:
                downstream = web.WebSocketResponse()
                await downstream.prepare(request)

                async def relay(
                    source: aiohttp.ClientWebSocketResponse | web.WebSocketResponse,
                    destination: aiohttp.ClientWebSocketResponse | web.WebSocketResponse,
                ) -> None:
                    async for message in source:
                        if message.type == aiohttp.WSMsgType.TEXT:
                            await destination.send_str(message.data)
                        elif message.type == aiohttp.WSMsgType.BINARY:
                            await destination.send_bytes(message.data)
                        else:
                            break

                tasks = [asyncio.create_task(relay(upstream, downstream)),
                         asyncio.create_task(relay(downstream, upstream))]
                try:
                    done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        task.result()
                finally:
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    await downstream.close()
                return downstream
        headers.extend((k, v) for k, v in request.headers.items() if k.lower() not in {
            "host", "connection", "transfer-encoding", "content-length",
        })
        async with self.session.request(
            request.method, target, headers=headers, data=await request.read(),
            allow_redirects=False,
        ) as upstream:
            response = web.StreamResponse(status=upstream.status, headers={
                k: v for k, v in upstream.headers.items()
                if k.lower() not in {"connection", "transfer-encoding", "content-length"}
            })
            await response.prepare(request)
            async for chunk in upstream.content.iter_chunked(16384):
                await response.write(chunk)
            await response.write_eof()
            return response

    async def start(self) -> None:
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", self.handle)
        self.runner = web.AppRunner(app, access_log=None, handler_cancellation=True,
                                    shutdown_timeout=1)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", 0,
                          ssl_context=self.rig.listener.tls()).start()
        self.origin = f"https://localhost:{self.runner.addresses[0][1]}"
        self.rig.listener = replace(self.rig.listener, external_origin=self.origin)
        await self.rig.start()
        self.rig.origin = self.origin
        self.session = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=verified_tls(self.rig.ca)),
            timeout=aiohttp.ClientTimeout(total=10),
            cookie_jar=aiohttp.DummyCookieJar(), trust_env=False, auto_decompress=False)

    async def close(self) -> None:
        await self.rig.close()
        if self.runner:
            await self.runner.cleanup()
        if self.session:
            await self.session.close()


@contextlib.asynccontextmanager
async def proxied(root: Path) -> AsyncIterator[tuple[Rig, Proxy]]:
    proxy = Proxy(Rig(root))
    try:
        await proxy.start()
        yield proxy.rig, proxy
    finally:
        await proxy.close()

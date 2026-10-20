"""Synthetic loopback HTTP backend embedding the installed real connector SDK."""

from __future__ import annotations

import asyncio
import json
import signal
import sys
from pathlib import Path

from aiohttp import web
from joserfc.jwk import ECKey

from fleet_manager.gateway_service_connector import ServiceConnector


async def serve(letter: str, instance: str) -> None:
    release = asyncio.Event()
    finished = asyncio.Event()
    started = asyncio.Event()

    async def backend(request: web.Request) -> web.StreamResponse:
        if request.path == "/events":
            release.clear()
            finished.clear()
            started.set()
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            await response.write(b"data: first\n\n")
            try:
                await asyncio.wait_for(release.wait(), 12)
                await response.write(b"data: last\n\n")
                finished.set()
                await response.write_eof()
                return response
            finally:
                started.clear()
        if request.path == "/release":
            release.set()
        if request.path == "/state":
            return web.json_response({"started": started.is_set(),
                                      "finished": finished.is_set()})
        if request.path == "/index.html":
            return web.Response(text=f"<html><body>{instance}</body></html>",
                                content_type="text/html")
        return web.json_response({"service": letter, "instance": instance})

    app = web.Application()
    app.router.add_get("/{tail:.*}", backend)
    runner = web.AppRunner(app, access_log=None, shutdown_timeout=1,
                           handler_cancellation=True)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 8080).start()
    root = Path("/credentials")
    slot = "service-" + letter
    key = ECKey.import_key(json.loads((root / (slot + ".key.json")).read_text()))

    async def token() -> str:
        # This refresh callback rereads only the explicitly mounted synthetic reference.
        return (root / (slot + ".token")).read_text(encoding="ascii")

    connector = ServiceConnector(
        slot=slot, gateway_origin="https://gateway:8443",
        backend_origin="http://127.0.0.1:8080", access_token_callback=token,
        proof_key=key.get_op_key("sign"), gateway_ca=str(root / "ca.pem"))
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)
    # The replaced a connector must not reconnect and fight its successor.
    task = asyncio.create_task(connector.connect_once() if letter == "a" else connector.run(stop))
    stopped = asyncio.create_task(stop.wait())
    try:
        await asyncio.wait({task, stopped}, return_when=asyncio.FIRST_COMPLETED)
        if task.done():
            await task
    finally:
        task.cancel()
        stopped.cancel()
        await asyncio.gather(task, stopped, return_exceptions=True)
        await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(serve(sys.argv[1], sys.argv[2]))
    except Exception:
        print('{"result":"fail","code":"synthetic-service-failed"}')
        raise SystemExit(1) from None

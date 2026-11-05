"""Installed Gateway lifetime using the existing TLS serving and shutdown owner."""

from __future__ import annotations

import asyncio
import errno
import hmac
import os
from pathlib import Path
import sys
import time

from agent_procutil.daemon_instances import daemon_entrypoint

from . import gateway_host as host
from .planning import PlanError


@daemon_entrypoint(
    "fleet-manager-service-gateway", source=__file__, purpose="production",
    runtime_root=lambda root, nonce: root / host.STATE,
)
async def run(root: Path, nonce: str) -> None:
    selection = host.optional_selection(root)
    if selection is None:
        raise PlanError("Gateway hosting was not selected")
    with host.runtime_custody(root) as slot:
        await _run(root, nonce, slot, selection)


async def _run(root: Path, nonce: str, slot: Path, selection: dict) -> None:
    from aiohttp import web
    from zdd.diagnostics import process_start_time
    from .gateway_service_server import STATE, start
    from .gateway_host_start import require_launch
    from .gateway_local_handler import settled

    require_launch(root, slot.name, nonce)
    listener, ledger = host.prepare(root, selection)
    startup = host.startup_digest(selection, listener=listener)
    birth = process_start_time(os.getpid())
    if birth is None:
        raise PlanError("Gateway host native identity is unavailable")
    identity = {
        "pid": os.getpid(), "birth": birth, "generation": slot.name,
        "selection": host.selection_digest(selection),
    }
    stopping = asyncio.Event()
    deadline = asyncio.get_running_loop().time() + 10
    while True:
        try:
            serving = await start(listener, Path(selection["policy"]), enrollment_journal=ledger)
            break
        except OSError as error:
            if (
                error.errno not in {errno.EADDRINUSE, 10048}
                or asyncio.get_running_loop().time() >= deadline
                or host.selected_version(root) != slot.name
            ):
                raise
            await asyncio.sleep(.1)
    control = None
    try:
        if host.startup_digest(selection) != startup:
            raise PlanError("Gateway startup inputs changed before readiness")

        def status():
            state = serving.app[STATE]
            return {
                **identity, "schema": "fleet-manager.gateway-host-control", "version": 1,
                "role": "gateway", "origin": serving.origin, "startup": startup,
                "ready": not stopping.is_set() and not state.stopping
                and state.status().last_error is None,
                "revision": state.status().revision,
            }

        async def handler(request):
            token = request.headers.get("X-Fleet-Host", "")
            if (
                "Origin" in request.headers or not token.isascii()
                or not hmac.compare_digest(token, nonce)
            ):
                raise web.HTTPForbidden()
            if request.path == "/stop":
                value = await request.json()
                selected = host.selected_version(root)
                if (
                    type(value) is not dict or set(value) != {"generation", "replacement"}
                    or value["generation"] != slot.name
                    or selected != (value["replacement"] or slot.name)
                    or value["replacement"] == slot.name
                ):
                    raise web.HTTPConflict()
                stopping.set()
            elif request.path != "/status":
                raise web.HTTPNotFound()
            return web.json_response(status())

        app = web.Application(client_max_size=1024)
        app.router.add_post("/{action}", handler)
        control = web.AppRunner(app, access_log=None, shutdown_timeout=2)
        await control.setup()
        site = web.TCPSite(control, "127.0.0.1", 0)
        await site.start()
        await settled(lambda: _publish(root, slot, nonce, identity, control.addresses[0][1]))
        while not stopping.is_set():
            try:
                await asyncio.wait_for(stopping.wait(), timeout=.5)
            except asyncio.TimeoutError:
                if host.selected_version(root) != slot.name:
                    stopping.set()
    finally:
        async def shutdown():
            state = serving.app[STATE]
            state.stopping = True
            deadline = asyncio.get_running_loop().time() + 5
            while state.active and asyncio.get_running_loop().time() < deadline:
                await asyncio.sleep(.05)
            try:
                await serving.close()
            finally:
                if control is not None:
                    await control.cleanup()

        cleanup = asyncio.create_task(shutdown())
        cancelled = False
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                cancelled = True
        await cleanup
        if cancelled:
            raise asyncio.CancelledError


def _publish(root: Path, slot: Path, nonce: str, identity: dict, port: int) -> None:
    from .gateway_host_start import _lock, clear_launch, require_launch

    lock = _lock(root, time.monotonic() + 5)
    try:
        if host.selected_version(root) != slot.name:
            raise PlanError("Gateway selection changed before readiness publication")
        require_launch(root, slot.name, nonce)
        host.write_private(root / host.STATE / host.RUNTIME, {
            **identity, "schema": "fleet-manager.gateway-host-runtime", "version": 1,
            "port": port, "token": nonce,
        })
        clear_launch(root, nonce)
    finally:
        lock.release()


def main() -> int:
    nonce = os.environ.pop("FLEET_MANAGER_HOST_NONCE", "")
    if sys.argv[1:] or len(nonce) != 32 or any(char not in "0123456789abcdef" for char in nonce):
        return 1
    try:
        asyncio.run(run(host.installation_root(), nonce))
        return 0
    except Exception:
        print("fleet-manager: selected Gateway hosting failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

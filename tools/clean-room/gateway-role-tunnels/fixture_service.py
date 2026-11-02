"""Synthetic loopback HTTP backend embedding the installed real connector SDK."""

from __future__ import annotations

import asyncio
import json
import os
import select
import signal
import stat
import sys
from pathlib import Path

from agent_procutil import spawn_daemon
from agent_procutil.daemon_instances import (
    DaemonInstanceSpec, daemon_entrypoint, daemon_test_lifecycle,
)

STATE_ROOT = Path("/state")
RUNNER_CONTROL = Path("/proof/runner.pipe")


@daemon_entrypoint(
    "gateway-role-fixture",
    source=__file__,
    runtime_root=lambda *_args: STATE_ROOT / "gateway-role-runtime",
)
async def serve(letter: str, instance: str) -> None:
    from aiohttp import web
    from joserfc.jwk import ECKey

    from fleet_manager.gateway_service_connector import ServiceConnector

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


def main(letter: str, instance: str) -> None:
    asyncio.run(serve(letter, instance))


def _open_runner(control: Path) -> int:
    info = control.lstat()
    if (not stat.S_ISFIFO(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077 or info.st_nlink != 1):
        raise ValueError("runner-control-unavailable")
    descriptor = os.open(control, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    opened = os.fstat(descriptor)
    if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
        os.close(descriptor)
        raise ValueError("runner-control-changed")
    return descriptor


def _runner_alive(descriptor: int) -> bool:
    try:
        value = os.read(descriptor, 1)
    except BlockingIOError:
        return True
    if value:
        raise ValueError("unexpected-runner-control-data")
    return False


def _supervise(
    command: list[str], source: Path, *, root: Path = STATE_ROOT,
    control: Path = RUNNER_CONTROL, lease=None,
) -> int:
    descriptor = _open_runner(control)
    child = None
    result = None
    try:
        if not _runner_alive(descriptor):
            raise ValueError("runner-control-closed")
        runtime = root / "gateway-role-runtime"
        runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
        with daemon_test_lifecycle(
            owner="gateway-role-proof", registry_root=root / ".gateway-role-instance-registry",
            lease=lease,
        ) as environment:
            child = spawn_daemon(
                command, cwd=root, log_path=runtime / "service.log",
                env={**os.environ, **environment}, breakaway=False,
                instance=DaemonInstanceSpec(
                    "gateway-role-fixture", runtime, source, purpose="unattended-test",
                    owner="gateway-role-proof", lease=lease,
                ),
            )
            while (result := child.poll()) is None:
                if not _runner_alive(descriptor):
                    break
                select.select([descriptor], [], [], 0.25)
    finally:
        try:
            if child is not None:
                child.wait(timeout=5)
        finally:
            os.close(descriptor)
    return result or 0


def supervise(letter: str, instance: str) -> int:
    previous = {}

    def terminated(*_args) -> None:
        raise SystemExit(0)

    try:
        for number in (signal.SIGTERM, signal.SIGINT):
            previous[number] = signal.signal(number, terminated)
        return _supervise(
            [sys.executable, str(Path(__file__).resolve()), "--service", letter, instance],
            Path(__file__).resolve(),
        )
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


if __name__ == "__main__":
    try:
        if len(sys.argv) == 4 and sys.argv[1] == "--service":
            main(sys.argv[2], sys.argv[3])
        elif len(sys.argv) == 3:
            raise SystemExit(supervise(sys.argv[1], sys.argv[2]))
        else:
            raise ValueError("invalid-fixture-command")
    except Exception:
        print('{"result":"fail","code":"synthetic-service-failed"}')
        raise SystemExit(1) from None

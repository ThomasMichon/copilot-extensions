"""A local TCP conduit does not become an HTTP or TLS authority."""

import asyncio
import ssl
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, cast
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web
import pytest

from agent_procutil.daemon_instances import DaemonInstanceGuard, admit_daemon_instance
from fleet_manager import gateway_service_server
from fleet_manager.gateway_connect_route import LoopbackRoute
from fleet_manager.gateway_service_connector import ServiceConnector
from fleet_manager.gateway_service_handler import HandlerBinding, ServiceRequest, ServiceResponse
from fleet_manager.gateway_service_listener import Listener
from gateway_service_transport_fixtures import Rig, running


@asynccontextmanager
async def conduit(origin: str) -> AsyncIterator[tuple[LoopbackRoute, list[asyncio.StreamWriter]]]:
    uri = urlsplit(origin)
    tasks: set[asyncio.Task[None]] = set()
    connections: list[asyncio.StreamWriter] = []

    async def forward(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        tasks.add(task)
        connections.append(writer)
        upstream = None
        try:
            remote, upstream = await asyncio.open_connection("127.0.0.1", uri.port)
            connections.append(upstream)

            async def pump(
                source: asyncio.StreamReader, destination: asyncio.StreamWriter,
            ) -> None:
                while data := await source.read(16384):
                    destination.write(data)
                    await destination.drain()

            pair = [asyncio.create_task(pump(reader, upstream)),
                    asyncio.create_task(pump(remote, writer))]
            try:
                await asyncio.wait(pair, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for pending in pair:
                    pending.cancel()
                await asyncio.gather(*pair, return_exceptions=True)
        finally:
            for stream in (writer, upstream):
                if stream is not None:
                    stream.close()
                    try:
                        await asyncio.wait_for(stream.wait_closed(), 1)
                    except (ConnectionError, asyncio.TimeoutError):
                        stream.transport.abort()
            tasks.discard(task)

    server = await asyncio.start_server(forward, "127.0.0.1", 0)
    try:
        yield LoopbackRoute("127.0.0.1", server.sockets[0].getsockname()[1]), connections
    finally:
        server.close()
        await server.wait_closed()
        closing = tuple(tasks)
        for task in closing:
            task.cancel()
        await asyncio.gather(*closing, return_exceptions=True)


def connector(
    rig: Rig, route: LoopbackRoute, *,
    gateway_origin: str | None = None, gateway_ca: str | None,
) -> ServiceConnector:
    async def access() -> str:
        return cast(Callable[[str, int], str], rig.access)("service-a", 1)

    async def handle(request: ServiceRequest) -> ServiceResponse:
        return ServiceResponse(200, body=b"routed")

    return ServiceConnector(
        slot="service-a", gateway_origin=gateway_origin or rig.origin, gateway_route=route,
        handler=HandlerBinding("fleet-a", "gateway-a", handle),
        access_token_callback=access, proof_key=rig.h.keys[1].get_op_key("sign"),
        gateway_ca=gateway_ca,
    )


def test_actual_tls_and_proof_keep_canonical_identity_over_distinct_tcp_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        async with running(tmp_path) as rig:
            monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
            async with conduit(rig.origin) as (route, connections):
                assert route.port != urlsplit(rig.origin).port
                selected = connector(rig, route, gateway_ca=rig.ca)
                task = asyncio.create_task(selected.connect_once())
                rig.tasks.append(task)
                try:
                    await asyncio.wait_for(selected.connected.wait(), 3)
                    assert connections
                    async with await rig.request() as response:
                        assert response.status == 200 and await response.text() == "routed"
                    assert selected.gateway == rig.origin
                    assert rig.backends[0].requests == []
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["wrong-name", "untrusted", "unavailable"])
def test_route_refuses_bad_tls_or_missing_conduit_without_direct_fallback(
    tmp_path: Path, failure: str,
) -> None:
    async def scenario() -> None:
        async with running(tmp_path) as rig:
            async with conduit(rig.origin) as (route, connections):
                gateway_origin, gateway_ca = rig.origin, rig.ca
                if failure == "wrong-name":
                    gateway_origin = "https://wrong.invalid:443"
                elif failure == "untrusted":
                    gateway_ca = None
                else:
                    unused = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
                    route = LoopbackRoute("127.0.0.1", unused.sockets[0].getsockname()[1])
                    unused.close()
                    await unused.wait_closed()
                selected = connector(rig, route, gateway_origin=gateway_origin, gateway_ca=gateway_ca)
                with pytest.raises((aiohttp.ClientError, OSError)):
                    await selected.connect_once()
                assert not selected.connected.is_set() and not rig.state.channels
                async with rig.session.get(rig.origin + "/_fleet/challenge") as response:
                    assert response.status == 200
    asyncio.run(scenario())


@pytest.mark.parametrize("host,port", [("localhost", 443), ("192.0.2.1", 443),
                                      ("127.0.0.1", True), ("127.0.0.1", 0)])
def test_route_is_literal_loopback_with_bounded_port(host: str, port: int) -> None:
    with pytest.raises(ValueError, match="invalid-gateway-connect-route"):
        LoopbackRoute(host, port)


@pytest.mark.parametrize("ending", ["return", "known-error", "unexpected-error", "admission-refused"])
def test_foreground_gateway_admission_precedes_startup_and_outlives_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ending: str,
) -> None:
    shared_admit = admit_daemon_instance
    guards: list[DaemonInstanceGuard] = []

    def acquire(
        service: str, *, source: str, purpose: Literal["production"], runtime_root: Path,
    ) -> DaemonInstanceGuard:
        if ending == "admission-refused":
            raise ValueError("admission-refused")
        guard = shared_admit(service, source=source, purpose=purpose, runtime_root=runtime_root)
        guards.append(guard)
        return guard

    def load(path: Path) -> Listener:
        assert len(guards) == 1 and not guards[0]._closed
        return Listener(
            "fleet-a", "gateway-a", "127.0.0.1", 443, "https://localhost:443",
            "certificate.pem", "key.pem",
        )

    def tls(listener: Listener) -> ssl.SSLContext:
        assert not guards[0]._closed
        return ssl.create_default_context()

    def run_app(application: Coroutine[None, None, web.Application], **options: object) -> None:
        assert not guards[0]._closed
        application.close()
        if ending == "known-error":
            raise OSError("runner-failed")
        if ending == "unexpected-error":
            raise RuntimeError("runner-failed")

    monkeypatch.setattr(gateway_service_server, "admit_daemon_instance", acquire)
    monkeypatch.setattr(Listener, "load", load)
    monkeypatch.setattr(Listener, "tls", tls)
    monkeypatch.setattr(web, "run_app", run_app)
    policy_path, tls_path = tmp_path / "policy.json", tmp_path / "tls.json"
    if ending == "admission-refused":
        with pytest.raises(ValueError, match="admission-refused"):
            gateway_service_server.serve(policy_path, tls_path)
        assert not guards
    elif ending == "unexpected-error":
        with pytest.raises(RuntimeError, match="runner-failed"):
            gateway_service_server.serve(policy_path, tls_path)
    else:
        assert gateway_service_server.serve(policy_path, tls_path) == (
            1 if ending == "known-error" else 0)
    assert all(guard._closed for guard in guards)

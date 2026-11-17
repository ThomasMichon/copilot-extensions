"""Relay listener activation follows preparation and exact routing ownership."""

from __future__ import annotations

import asyncio
from pathlib import Path
import os
import threading
import time
import socket
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
import pytest
from zdd import routing


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["none", "reselected", "shutdown"])
async def test_relay_preparation_cannot_activate_a_demoted_frontend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    import credential_relay
    from agent_bridge import app as app_module, relay_state
    from agent_bridge.app_lifecycle import start_credential_relay

    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    routing.publish_active(tmp_path, bind="127.0.0.1", port=41001, pid=101, version="old")
    current, _ = routing.publish_active_with_previous(
        tmp_path, bind="127.0.0.1", port=41002, pid=os.getpid(), version="new",
        demote_existing=True,
    )
    app = FastAPI()
    app.state.independent_runtime_start = True
    app.state.bound_port = current.port
    app.state.shutting_down = False
    calls: list[str] = []

    class Server:
        running = True
        port = 41003

        async def start(self, *, reclaim: bool = True) -> None:
            assert reclaim is False
            calls.append("bind")

        async def stop(self) -> None:
            calls.append("stop")

    class Builder:
        empty = False
        sources = {"fixture": object()}

        def build(self) -> Server:
            calls.append("build")
            return Server()

    async def prepare(*_args: Any) -> None:
        calls.append("prepare")
        await asyncio.sleep(0)
        if change == "reselected":
            assert current.pid is not None
            assert routing.restore_previous_if_owner(
                tmp_path, pid=current.pid, generation=current.generation,
            )
        elif change == "shutdown":
            app.state.shutting_down = True

    monkeypatch.setattr(credential_relay, "RelayBuilder", Builder)
    monkeypatch.setattr(app_module, "_run_in_daemon_thread", prepare)
    monkeypatch.setattr(relay_state, "set_live_relay_port", lambda _: calls.append("publish"))
    result = await start_credential_relay(app)
    if change == "none":
        assert result is not None and calls == ["prepare", "build", "bind", "publish"]
    else:
        assert result is None and calls == ["prepare"]
        assert not hasattr(app.state, "credential_relay")


@pytest.mark.asyncio
@pytest.mark.parametrize("publish_error", [False, True])
async def test_relay_binding_and_publication_hold_one_native_routing_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, publish_error: bool,
) -> None:
    import credential_relay
    from agent_bridge import app as app_module, relay_state
    from agent_bridge.app_lifecycle import start_credential_relay

    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    routing.publish_active(
        tmp_path, bind="127.0.0.1", port=41002, pid=os.getpid(), version="current",
    )
    app = FastAPI()
    app.state.publish_on_ready = True
    app.state.bound_port = 41002
    app.state.shutting_down = False
    entered, mutated = threading.Event(), threading.Event()
    calls: list[str] = []
    thread: threading.Thread | None = None

    def replace_route() -> None:
        entered.set()
        with routing.routing_lock(tmp_path, deadline=time.monotonic() + 5):
            mutated.set()

    class Server:
        port = 41003

        async def start(self, *, reclaim: bool = True) -> None:
            assert reclaim is False
            nonlocal thread
            thread = threading.Thread(target=replace_route)
            thread.start()
            assert await asyncio.to_thread(entered.wait, 2)
            assert not mutated.is_set()
            calls.append("bind")

        async def stop(self) -> None:
            assert not mutated.is_set()
            calls.append("stop")

    server = Server()
    builder = SimpleNamespace(empty=False, sources={"fixture": object()}, build=lambda: server)

    async def prepare(*_args: Any) -> None:
        calls.append("prepare")

    def publish(_port: int) -> None:
        assert not mutated.is_set()
        calls.append("publish")
        if publish_error:
            raise OSError("retained port publication failed")

    monkeypatch.setattr(credential_relay, "RelayBuilder", lambda: builder)
    monkeypatch.setattr(app_module, "_run_in_daemon_thread", prepare)
    monkeypatch.setattr(relay_state, "set_live_relay_port", publish)
    try:
        result = await start_credential_relay(app)
        assert calls == (["prepare", "bind", "publish", "stop"] if publish_error else [
            "prepare", "bind", "publish",
        ])
        assert (result is None) is publish_error
    finally:
        if thread is not None:
            await asyncio.to_thread(thread.join, 5)
            assert not thread.is_alive()
    assert mutated.is_set()


@pytest.mark.asyncio
@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("filesystem")
@pytest.mark.effect("network")
async def test_real_fixed_port_conflict_cannot_evict_its_occupant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import credential_relay
    from credential_relay import server as relay_server_module
    from agent_bridge import app as app_module, relay_state
    from agent_bridge.app_lifecycle import start_credential_relay

    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    routing.publish_active(
        tmp_path, bind="127.0.0.1", port=41002, pid=os.getpid(), version="current",
    )
    app = FastAPI()
    app.state.publish_on_ready = True
    app.state.bound_port = 41002
    app.state.shutting_down = False
    occupant = socket.socket()
    occupant.bind(("127.0.0.1", 0))
    occupant.listen(1)
    address = occupant.getsockname()
    server = relay_server_module.CredentialRelayServer(port=address[1])
    builder = SimpleNamespace(empty=False, sources={"fixture": object()}, build=lambda: server)

    async def prepare(*_args: Any) -> None:
        pass

    monkeypatch.setattr(credential_relay, "RelayBuilder", lambda: builder)
    monkeypatch.setattr(app_module, "_run_in_daemon_thread", prepare)
    monkeypatch.setattr(
        relay_server_module, "_reclaim_port",
        lambda _: pytest.fail("Bridge relay activation must never evict a port owner"),
    )
    monkeypatch.setattr(
        relay_state, "set_live_relay_port",
        lambda _: pytest.fail("failed fixed-port activation cannot publish a replacement"),
    )
    try:
        assert await start_credential_relay(app) is None
        assert not server.running and occupant.getsockname() == address
        with socket.create_connection(address, timeout=1):
            accepted, _ = occupant.accept()
            accepted.close()
    finally:
        occupant.close()
        await server.stop()


@pytest.mark.asyncio
@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("network")
async def test_shared_relay_default_conflict_policy_remains_compatible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from credential_relay import server as relay_server_module

    occupant = socket.socket()
    occupant.bind(("127.0.0.1", 0))
    occupant.listen(1)
    port = occupant.getsockname()[1]
    attempted: list[int] = []

    def decline_reclaim(requested: int) -> bool:
        attempted.append(requested)
        return False

    monkeypatch.setattr(relay_server_module, "_reclaim_port", decline_reclaim)
    server = relay_server_module.CredentialRelayServer(port=port)
    try:
        await server.start()
        assert attempted == [port]
        assert server.running and server.port not in (0, port)
    finally:
        await server.stop()
        occupant.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("configured_relay", [False, True])
async def test_actual_passive_readiness_registers_http_sources_without_a_listener(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured_relay: bool,
) -> None:
    import credential_relay
    from credential_relay.http_sources import HttpSourceRegistry
    from agent_bridge import app as app_module, agent_registry, http_provider_sources
    from agent_bridge.agent_registry_resolver import AgentResolver
    from agent_bridge.models import ServiceConfig

    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    prepared: list[str] = []
    builder = SimpleNamespace(empty=True, sources=[], http_sources=HttpSourceRegistry())
    source = SimpleNamespace(name="fixture-http-source")

    def register_http(candidate: Any) -> None:
        candidate.http_sources.register("fixture@tests/credentials", source)
        prepared.append("http")

    monkeypatch.setattr(credential_relay, "RelayBuilder", lambda: builder)
    monkeypatch.setattr(app_module, "daemon_resolver", lambda _: AgentResolver({}, {}))
    monkeypatch.setattr(
        agent_registry, "register_credential_sources",
        lambda _: pytest.fail("an unpromoted passive cannot prepare TCP takeover"),
    )
    monkeypatch.setattr(
        http_provider_sources, "register_http_credential_sources",
        register_http,
    )
    app = app_module.create_app(config=ServiceConfig(
        db_path=str(tmp_path / "passive.db"), enable_credential_relay=configured_relay,
    ), token="fixture-token")
    app.state.passive = True
    app.state.bound_port = 41002
    async with app.router.lifespan_context(app):
        assert app.state.ready
        assert prepared == ["http"]
        assert builder.http_sources.get("fixture@tests/credentials").source is source
        assert not app.state.credential_relay_ready

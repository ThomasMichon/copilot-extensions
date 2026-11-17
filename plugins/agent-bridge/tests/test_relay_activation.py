"""Relay listener activation follows preparation and exact routing ownership."""

from __future__ import annotations

import asyncio
from pathlib import Path
import os
import threading
import time
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

        async def start(self) -> None:
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

        async def start(self) -> None:
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

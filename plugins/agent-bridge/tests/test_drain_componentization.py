"""Compatibility surfaces retained by the drain component extraction."""

from __future__ import annotations

import functools
from types import SimpleNamespace

import pytest
from zdd import routing

from agent_bridge import app, client
from agent_bridge.client_drain import DrainClientMixin


@pytest.mark.contract("agent-bridge.drain-client-compatibility")
def test_drain_componentization_keeps_client_exception_patch_surface(monkeypatch):
    class PatchedClientError(Exception):
        pass

    monkeypatch.setattr(client, "BridgeClientError", PatchedClientError)
    bridge = client.BridgeClient("http://127.0.0.1:1", "unused")
    monkeypatch.setattr(bridge, "health", lambda: None)
    assert isinstance(bridge, DrainClientMixin)
    with pytest.raises(PatchedClientError, match="no mismatch established"):
        bridge.drain()
    with pytest.raises(PatchedClientError, match="graceful-only drain cannot force"):
        bridge.drain_gracefully(force=True)


@pytest.mark.contract("agent-bridge.startup-supersession-compatibility")
@pytest.mark.asyncio
async def test_drain_componentization_keeps_app_thread_and_poll_patch_surfaces(
    monkeypatch, tmp_path,
):
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    routing.publish_active(
        tmp_path, bind="127.0.0.1", port=41001, pid=101, version="old",
    )
    successor, previous = routing.publish_active_with_previous(
        tmp_path, bind="127.0.0.1", port=41002, pid=202, version="new",
        demote_existing=True,
    )
    calls = []
    checks = []
    original_same = app._same_daemon
    original_owns = app._owns_generation

    def same_daemon(left, right):
        checks.append("same")
        return original_same(left, right)

    def owns_generation(left, right):
        checks.append("owns")
        return original_owns(left, right)

    def drain(**kwargs):
        assert kwargs["poll"] == 0.125
        return {"drained": True}

    def shutdown():
        return {"shutting_down": True}

    async def run_in_thread(fn, *args):
        calls.append(fn.func if isinstance(fn, functools.partial) else fn)
        return fn(*args)

    monkeypatch.setattr(app, "_run_in_daemon_thread", run_in_thread)
    monkeypatch.setattr(app, "_SUPERSESSION_DRAIN_POLL_S", 0.125)
    monkeypatch.setattr(app, "_same_daemon", same_daemon)
    monkeypatch.setattr(app, "_owns_generation", owns_generation)
    await app._retire_previous_daemon(
        SimpleNamespace(state=SimpleNamespace(auth_token="unused")), previous, successor,
        make_client=lambda _: SimpleNamespace(drain=drain, shutdown=shutdown),
    )
    assert calls == [drain, shutdown]
    assert checks == ["owns", "same", "same"]

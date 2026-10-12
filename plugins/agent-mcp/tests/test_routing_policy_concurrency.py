"""Routing policy binding, endpoint rotation, and warm lifecycle regressions."""

import asyncio
import copy
import time

import pytest

from agent_mcp.client import OneShotSession
from agent_mcp.config import ConfigError, parse_config
from agent_mcp.routing import isolated_config
from agent_mcp.serve import Server, WarmPool

from .test_routing import MockUpstream, TOOLS, definition


@pytest.mark.parametrize("mapping", [{}, {"organization": "alpha"},
                                    {"organization": "$args.other"}])
@pytest.mark.parametrize("on_error", ["deny", "allow"])
def test_routed_gate_rejects_unscoped_preflight_even_when_fail_open(mapping, on_error):
    data = definition()
    data["decorators"] = [{
        "type": "gate", "match_tools": ["action"],
        "preflight": {"tool": "read", "args_from": mapping},
        "allow_when": {"path": "allowed", "equals": True}, "on_error": on_error,
    }]
    with pytest.raises(ConfigError, match="must map the same selector"):
        parse_config(data)


async def test_valid_gate_preflight_and_call_use_same_target(monkeypatch):
    import agent_mcp.client as client

    state = {"started": [], "tools": {key: copy.deepcopy(TOOLS)
                                    for key in ("alpha", "beta")}}

    class PolicyUpstream(MockUpstream):
        async def send(self, msg):
            if msg["method"] == "tools/call" and msg["params"]["name"] == "read":
                self.sent.append(copy.deepcopy(msg))
                await self._emit_message({"jsonrpc": "2.0", "id": msg["id"], "result": {
                    "content": [], "structuredContent": {"allowed": True}}})
                return
            await super().send(msg)

    monkeypatch.setattr(client, "build_transport", lambda cfg, injector: (
        PolicyUpstream(cfg, injector, state)))
    data = definition()
    for target in data["routing"]["targets"].values():
        target["auth"] = {"kind": "none"}
    data["decorators"] = [{
        "type": "gate", "match_tools": ["action"],
        "preflight": {"tool": "read", "args_from": {"organization": "$args.organization"}},
        "allow_when": {"path": "allowed", "equals": True}, "on_error": "allow",
    }]
    async with OneShotSession(parse_config(data)) as session:
        await session.call_tool("action", {"organization": "beta", "action": "get"})
    calls = [(transport.key, msg) for transport in state["started"] for msg in transport.sent
             if msg["method"] == "tools/call"]
    assert [(key, msg["params"]["name"]) for key, msg in calls] == [
        ("beta", "read"), ("beta", "action")]
    assert all("organization" not in msg["params"]["arguments"] for _, msg in calls)


def test_url_secret_rotation_qualifies_primary_and_url_auth_keys(monkeypatch):
    cfg = parse_config({
        "server": {"type": "http", "url": "https://example.test/${endpoint}",
                   "url_secrets": {"endpoint": {"kind": "env", "source_env": "ENDPOINT_SECRET"}}},
        "auth": {"kind": "static", "value": "primary", "cache": {"key": "same"}},
    })
    monkeypatch.setenv("ENDPOINT_SECRET", "alpha-secret")
    first = isolated_config(cfg)
    monkeypatch.setenv("ENDPOINT_SECRET", "beta-secret")
    second = isolated_config(cfg)
    assert all(a.cache.key != b.cache.key for a, b in zip(first.auths, second.auths))
    assert first.server.url_secrets["endpoint"].cache.key != second.server.url_secrets[
        "endpoint"].cache.key
    assert cfg.auth.cache.key == "same"


@pytest.mark.parametrize("transition", ["close", "replace"])
async def test_warm_distinct_targets_parallel_and_transition_waits(monkeypatch, transition, tmp_path):
    import agent_mcp.client as client

    state = {"started": [], "tools": {key: copy.deepcopy(TOOLS)
                                    for key in ("alpha", "beta")}}
    entered = {key: asyncio.Event() for key in ("alpha", "beta")}
    release = asyncio.Event()

    class BlockingUpstream(MockUpstream):
        async def send(self, msg):
            if msg["method"] == "tools/call":
                entered[self.key].set()
                await release.wait()
            await super().send(msg)

    monkeypatch.setattr(client, "build_transport", lambda cfg, injector: (
        BlockingUpstream(cfg, injector, state)))
    data = definition()
    for target in data["routing"]["targets"].values():
        target["auth"] = {"kind": "none"}
    cfg = parse_config(data)
    pool = WarmPool(idle_timeout=1)
    server = Server(tmp_path / "serve.sock", pool=pool, idle_timeout=1, enable_lease=False)
    calls = [asyncio.create_task(pool.call("common", cfg, "read", {"organization": key}))
             for key in ("alpha", "beta")]
    cleanup = None
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in entered.values())), 3)
        assert pool._entries["common"].active == 2
        pool._entries["common"].last_used = time.monotonic() - 10
        await pool.sweep_idle()
        assert all(not transport.closed for transport in state["started"])
        if transition == "close":
            cleanup = asyncio.create_task(pool.close_all())
        else:
            updated = copy.deepcopy(cfg)
            updated.timeout += 1
            cleanup = asyncio.create_task(pool.call(
                "common", updated, "read", {"organization": "alpha"}))
        await asyncio.sleep(0)
        assert not cleanup.done()
        assert pool.size == 1
        server._last_active = time.monotonic() - 10
        server._maybe_idle_evict()
        assert not server._stop.is_set()
        assert all(not transport.closed for transport in state["started"])
        release.set()
        results = await asyncio.gather(*calls)
        assert {result["structuredContent"]["target"] for result in results} == {"alpha", "beta"}
        await asyncio.wait_for(cleanup, 3)
        assert all(transport.closed for transport in state["started"][:2])
    finally:
        release.set()
        await asyncio.gather(*calls, return_exceptions=True)
        if cleanup is not None:
            await asyncio.gather(cleanup, return_exceptions=True)
        await pool.close_all()


async def test_pending_warm_open_retains_host_liveness(monkeypatch, tmp_path):
    import agent_mcp.serve as serve

    entered, release = asyncio.Event(), asyncio.Event()
    original = serve.OneShotSession.__aenter__

    async def blocked(session):
        entered.set()
        await release.wait()
        return await original(session)

    monkeypatch.setattr(serve.OneShotSession, "__aenter__", blocked)
    pool = WarmPool()
    server = Server(tmp_path / "serve.sock", pool=pool, idle_timeout=1, enable_lease=False)
    task = asyncio.create_task(pool.list("common", parse_config(definition())))
    try:
        await entered.wait()
        assert pool.size == 1
        server._last_active = time.monotonic() - 10
        server._maybe_idle_evict()
        assert not server._stop.is_set()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await pool.close_all()
    assert pool.size == 0

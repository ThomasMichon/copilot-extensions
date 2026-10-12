"""Offline routing regressions at the real transport payload boundary."""

from __future__ import annotations

import asyncio
import copy
import json
import shutil
import sys

import pytest

from agent_mcp.client import OneShotSession, UpstreamError
from agent_mcp.config import ConfigError, parse_config
from agent_mcp.routing import RoutedCore, isolated_config
from agent_mcp.serve import WarmPool
from agent_mcp.session import BridgeSession
from agent_mcp.transports.base import Transport


TOOLS = [
    {"name": "action", "description": "Perform an action.",
     "inputSchema": {"type": "object", "additionalProperties": False,
                     "properties": {"action": {"type": "string"}}}},
    {"name": "read", "inputSchema": {"type": "object", "properties": {}}},
]


def definition():
    return {"routing": {"parameter": "organization", "catalog": "alpha", "targets": {
        key: {"server": {"type": "http", "protocol": "legacy",
                         "url": f"https://example.test/{key}"},
              "auth": {"kind": "static", "value": key, "format": "{token}",
                       "cache": {"scope": "shared", "key": "same", "ttl": 600}}}
        for key in ("alpha", "beta")
    }}}


class MockUpstream(Transport):
    def __init__(self, cfg, injector, state):
        super().__init__(cfg, injector)
        self.key = cfg.server.url.rsplit("/", 1)[-1]
        self.state = state
        self.sent = []
        self.closed = False

    async def start(self):
        self.state["started"].append(self)

    async def send(self, msg):
        msg = copy.deepcopy(msg)
        self.sent.append(msg)
        if msg.get("id") is None:
            return
        method = msg["method"]
        if method == "initialize":
            result = {"protocolVersion": "2025-06-18", "serverInfo": {
                "name": self.key, "version": "1"}, "capabilities": {"tools": {}}}
        elif method == "tools/list":
            result = {"tools": copy.deepcopy(self.state["tools"][self.key])}
            if self.state.get("paginated"):
                if (msg.get("params") or {}).get("cursor"):
                    result["tools"] = result["tools"][1:]
                else:
                    result["tools"] = result["tools"][:1]
                    result["nextCursor"] = "next"
            if self.state.get("endless"):
                result["nextCursor"] = "forever"
        else:
            await asyncio.sleep(0)
            # Exercise the actual injector and endpoint-qualified shared cache.
            headers = await self.injector.headers()
            result = {"content": [], "structuredContent": {
                "target": self.key, "arguments": msg["params"]["arguments"],
                "credential": headers.get("Authorization")}}
        await self._emit_message({"jsonrpc": "2.0", "id": msg["id"], "result": result})

    async def aclose(self):
        self.closed = True


@pytest.fixture
def upstreams(monkeypatch, tmp_path):
    import agent_mcp.client as client

    monkeypatch.setenv("AGENT_MCP_HOME", str(tmp_path))
    state = {"started": [], "tools": {key: copy.deepcopy(TOOLS)
                                    for key in ("alpha", "beta")}}
    monkeypatch.setattr(client, "build_transport",
                        lambda cfg, injector: MockUpstream(cfg, injector, state))
    return state


def calls(state):
    return [(t.key, m) for t in state["started"] for m in t.sent
            if m["method"] == "tools/call"]


async def test_required_selector_and_no_start_on_invalid(upstreams):
    async with OneShotSession(parse_config(definition())) as session:
        for args in ({}, {"organization": "unknown"}, {"organization": None}):
            with pytest.raises(UpstreamError, match="required routing selector"):
                await session.call_tool("read", args)
        assert upstreams["started"] == []
        tools = await session.list_tools()
        for tool in tools:
            schema = tool["inputSchema"]
            assert schema["properties"]["organization"]["enum"] == ["alpha", "beta"]
            assert schema["required"] == ["organization"]
        assert [t.key for t in upstreams["started"]] == ["alpha"]


async def test_concurrent_calls_isolate_payload_auth_and_teardown(upstreams):
    original = definition()
    cfg = parse_config(original)
    async with OneShotSession(cfg) as session:
        results = await asyncio.gather(*[
            session.call_tool("action", {"organization": key, "action": f"get-{i}"})
            for i in range(12) for key in ("alpha", "beta")])
        for result in results:
            value = result["structuredContent"]
            assert value["credential"] == value["target"]
            assert "organization" not in value["arguments"]
        assert len(calls(upstreams)) == 24
        assert len(upstreams["started"]) == 2
        for t in upstreams["started"]:
            ids = [m["id"] for m in t.sent if m.get("id") is not None]
            assert len(ids) == len(set(ids))
    assert all(t.closed for t in upstreams["started"])
    assert definition() == original
    assert cfg.routing.targets["alpha"].auth.cache.key == "same"


async def test_target_authorization_and_action_gate_before_auth(upstreams):
    data = definition()
    target = data["routing"]["targets"]["beta"]
    target["tools"] = {"allow": ["action"]}
    target["decorators"] = [{"type": "input_gate", "match_tools": ["action"],
                             "deny_when": {"path": "action", "not_in": ["get", "list"]},
                             "reason": "read actions only"}]
    async with OneShotSession(parse_config(data)) as session:
        with pytest.raises(UpstreamError, match="authorization"):
            await session.call_tool("read", {"organization": "beta"})
        for action in ("write", None, ["get"]):
            with pytest.raises(UpstreamError, match="read actions only"):
                await session.call_tool("action", {"organization": "beta", "action": action})
        with pytest.raises(UpstreamError, match="read actions only"):
            await session.call_tool("action", {"organization": "beta"})
        assert upstreams["started"] == []
        assert len(await session.list_tools()) == len(TOOLS)
        result = await session.call_tool("action", {"organization": "beta", "action": "get"})
        assert result["structuredContent"]["target"] == "beta"
        result = await session.call_tool("action", {"organization": "alpha", "action": "write"})
        assert result["structuredContent"]["target"] == "alpha"
    assert len(calls(upstreams)) == 2


@pytest.mark.parametrize("difference", ["schema", "missing", "extra"])
async def test_catalog_mismatch_never_calls_backend(upstreams, difference):
    if difference == "schema":
        upstreams["tools"]["beta"][0]["inputSchema"]["required"] = ["action"]
    elif difference == "missing":
        upstreams["tools"]["beta"].pop()
    else:
        upstreams["tools"]["beta"].append(
            {"name": "extra", "inputSchema": {"type": "object"}})
    async with OneShotSession(parse_config(definition())) as session:
        with pytest.raises(UpstreamError, match="compatibility error"):
            await session.call_tool("read", {"organization": "beta"})
        assert calls(upstreams) == []
        assert len(await session.list_tools()) == len(TOOLS)
    assert all(t.closed for t in upstreams["started"])


async def test_selector_collision_is_explicit(upstreams):
    upstreams["tools"]["alpha"][0]["inputSchema"]["properties"]["organization"] = {}
    async with OneShotSession(parse_config(definition())) as session:
        with pytest.raises(UpstreamError, match="collides"):
            await session.list_tools()


async def test_complete_paginated_common_catalog(upstreams):
    upstreams["paginated"] = True
    async with OneShotSession(parse_config(definition())) as session:
        assert {t["name"] for t in await session.list_tools()} == {"action", "read"}
        await session.call_tool("read", {"organization": "beta"})
    lists = [m for t in upstreams["started"] for m in t.sent if m["method"] == "tools/list"]
    assert len(lists) == 4


async def test_pagination_bound_is_not_a_partial_contract(upstreams):
    upstreams["endless"] = True
    async with OneShotSession(parse_config(definition())) as session:
        with pytest.raises(UpstreamError, match="compatibility error.*pagination"):
            await session.list_tools()
        assert calls(upstreams) == []


async def test_bridge_and_warm_pool_share_enforcement(upstreams):
    cfg = parse_config(definition())
    responses = []
    bridge = BridgeSession(cfg, responses.append)
    await bridge.start()
    for i, args in enumerate(({}, {"organization": "beta"})):
        bridge.submit({"jsonrpc": "2.0", "id": i, "method": "tools/call",
                       "params": {"name": "read", "arguments": args}})
    await bridge.aclose()
    assert next(r for r in responses if r["id"] == 0)["error"]["code"] == -32602
    assert next(r for r in responses if r["id"] == 1)["result"][
        "structuredContent"]["target"] == "beta"
    pool = WarmPool()
    try:
        with pytest.raises(UpstreamError, match="required routing selector"):
            await pool.call("fixture", cfg, "read", {})
        tools = await pool.list("fixture", cfg)
        assert "organization" in tools[0]["inputSchema"]["required"]
        result = await pool.call("fixture", cfg, "read", {"organization": "alpha"})
        assert result["structuredContent"]["target"] == "alpha"
    finally:
        await pool.close_all()
    assert all(t.closed for t in upstreams["started"])


async def test_defer_real_subcall_requires_selector(upstreams):
    data = definition()
    data["decorators"] = [{"type": "defer"}]
    async with OneShotSession(parse_config(data)) as session:
        # Defer executes a real request below its own synthetic meta-tool.
        with pytest.raises(UpstreamError, match="required routing selector"):
            await session.call_tool("execute_tool", {"tool": "read", "arguments": {}})
        result = await session.call_tool("execute_tool", {
            "tool": "read", "arguments": {"organization": "beta"}})
        assert result["structuredContent"]["target"] == "beta"
    assert len(calls(upstreams)) == 1


@pytest.mark.skipif(shutil.which("node") is None, reason="Node runtime unavailable")
async def test_code_mode_routes_each_real_call(upstreams):
    data = definition()
    data["decorators"] = [{"type": "code-mode"}]
    async with OneShotSession(parse_config(data)) as session:
        apis = await session.call_tool("code_apis", {})
        assert "organization" in apis["content"][0]["text"]
        assert '"alpha"' in apis["content"][0]["text"]
        await session.call_tool("run_code", {
            "code": "return await tools.read({organization: 'beta'});"})
        assert calls(upstreams)[0][0] == "beta"
        assert "organization" not in calls(upstreams)[0][1]["params"]["arguments"]
        await session.call_tool("run_code", {"code": "return await tools.read({});"})
        assert len(calls(upstreams)) == 1


def test_cli_materialize_and_call_preserve_contract(upstreams, tmp_path, capsys):
    from agent_mcp.__main__ import main

    cfg = tmp_path / "alpha.json"
    cfg.write_text(json.dumps(definition()), encoding="utf-8")
    assert main(["materialize", str(cfg), "--dest", str(tmp_path / "fleet")]) == 0
    capsys.readouterr()
    sidecars = list((tmp_path / "fleet").rglob("read.md"))
    assert len(sidecars) == 1
    schema_text = sidecars[0].read_text(encoding="utf-8")
    assert '"organization"' in schema_text and '"beta"' in schema_text
    assert main(["call", str(cfg), "read", '{"organization":"beta"}']) == 0
    assert json.loads(capsys.readouterr().out)["target"] == "beta"
    assert main(["call", str(cfg), "read", "{}"]) == 1
    assert "required routing selector" in capsys.readouterr().err
    assert len(calls(upstreams)) == 1


async def test_non_tool_routes_fail_without_context(upstreams):
    core = RoutedCore(parse_config(definition()))
    try:
        result = await core.handle({"id": 1, "method": "resources/list"})
        assert result["error"]["code"] == -32601
        with pytest.raises(RuntimeError, match="cannot safely forward"):
            await core.handle({"method": "notifications/cancelled", "params": {}})
        assert upstreams["started"] == []
    finally:
        await core.aclose()


def test_config_validation_and_shared_settings():
    data = definition()
    data["timeout"] = 3
    data["headers"] = {"X-Shared": "yes"}
    data["decorators"] = [{"type": "input_gate", "match_tools": ["action"],
                           "deny_when": {"path": "action", "equals": "remove"}}]
    cfg = parse_config(data)
    assert cfg.routing.targets["alpha"].timeout == 3
    assert cfg.routing.targets["alpha"].headers == {"X-Shared": "yes"}
    assert cfg.routing.targets["alpha"].decorators == []
    assert len(cfg.decorators) == 1
    for change in ({"catalog": "unknown"}, {"parameter": ""}, {"targets": {}},
                   {"config": "external.yaml"}):
        bad = copy.deepcopy(data)
        bad["routing"].update(change)
        with pytest.raises(ConfigError):
            parse_config(bad)
    bad = copy.deepcopy(data)
    bad["routing"]["targets"]["beta"]["decorators"] = [{"type": "defer"}]
    with pytest.raises(ConfigError, match="only input_gate"):
        parse_config(bad)


def test_auth_cache_key_endpoint_tenant_identity_and_explicit_override(monkeypatch):
    target = parse_config(definition()).routing.targets["alpha"]
    key = isolated_config(target).auth.cache.key
    assert key == isolated_config(copy.deepcopy(target)).auth.cache.key
    for component in ("endpoint", "tenant", "identity", "key"):
        changed = copy.deepcopy(target)
        if component == "endpoint":
            changed.server.url += "/other"
        elif component == "tenant":
            changed.auth.tenant = "other"
        elif component == "identity":
            changed.auth.value = "other"
        else:
            changed.auth.cache.key = "other"
        assert isolated_config(changed).auth.cache.key != key
    target.auth.source_env = "SELECTED_TOKEN"
    monkeypatch.setenv("SELECTED_TOKEN", "alpha")
    key = isolated_config(target).auth.cache.key
    monkeypatch.setenv("SELECTED_TOKEN", "beta")
    assert isolated_config(target).auth.cache.key != key


async def test_actual_stdio_payload_and_single_server_parity():
    child = r'''
import json, sys
for line in sys.stdin:
    m = json.loads(line)
    if m.get("id") is None:
        continue
    if m["method"] == "initialize":
        result = {"protocolVersion":"2025-06-18","serverInfo":{"name":"fixture","version":"1"},
                  "capabilities":{}}
    elif m["method"] == "tools/list":
        result = {"tools":[{"name":"read","inputSchema":{"type":"object","properties":{}}}]}
    else:
        result = {"content":[],"structuredContent":{"target":sys.argv[1],
                  "arguments":m["params"]["arguments"]}}
    print(json.dumps({"jsonrpc":"2.0","id":m["id"],"result":result}), flush=True)
'''
    targets = {key: {"server": {"type": "stdio", "protocol": "legacy",
                                "command": [sys.executable, "-c", child, key]}}
               for key in ("alpha", "beta")}
    cfg = parse_config({"routing": {"parameter": "organization", "catalog": "alpha",
                                    "targets": targets}})
    async with OneShotSession(cfg) as session:
        result = await session.call_tool("read", {"organization": "beta"})
        assert result["structuredContent"] == {"target": "beta", "arguments": {}}
    async with OneShotSession(parse_config(targets["alpha"])) as session:
        result = await session.call_tool("read", {"organization": "unchanged"})
        assert result["structuredContent"]["arguments"] == {"organization": "unchanged"}


async def test_cli_target_env_and_auth_failure_never_falls_back(tmp_path, monkeypatch):
    import yaml
    from agent_mcp.client import result_text

    script = (
        "import json,os,sys;"
        "print(json.dumps({'target':os.environ['TARGET'],"
        "'credential':os.environ.get('ACCESS_TOKEN'),'args':sys.argv[1:]}))"
    )
    tool = {"name": "read", "inputSchema": {
        "type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
        "invoke": {"command": sys.executable, "args": ["-c", script, "{q}"]}}
    sidecar = tmp_path / "read.md"
    sidecar.write_text("---\n" + yaml.safe_dump({"mcp": tool}) + "---\n", encoding="utf-8")
    targets = {key: {"server": {"type": "cli", "tools_from": [str(sidecar)],
                                "env": {"TARGET": key}},
                     "auth": {"kind": "static", "value": key, "target_env": "ACCESS_TOKEN"}}
               for key in ("alpha", "beta")}
    cfg = parse_config({"routing": {"parameter": "organization", "catalog": "alpha",
                                    "targets": targets}})
    async with OneShotSession(cfg) as session:
        for key in ("alpha", "beta"):
            result = await session.call_tool("read", {"organization": key, "q": "hello"})
            assert json.loads(result_text(result)) == {
                "target": key, "credential": key, "args": ["hello"]}

    # A missing configured credential must not inherit a different ambient one.
    monkeypatch.setenv("ACCESS_TOKEN", "ambient-not-selected")
    monkeypatch.delenv("MISSING_SELECTED_TOKEN", raising=False)
    targets["beta"]["auth"] = {"kind": "env", "source_env": "MISSING_SELECTED_TOKEN",
                               "target_env": "ACCESS_TOKEN"}
    cfg = parse_config({"routing": {"parameter": "organization", "catalog": "alpha",
                                    "targets": targets}})
    async with OneShotSession(cfg) as session:
        with pytest.raises(UpstreamError, match="authentication failed") as error:
            await session.call_tool("read", {"organization": "beta", "q": "hello"})
        assert "ambient-not-selected" not in str(error.value)


async def test_failed_target_auth_does_not_spawn_stdio_child(tmp_path, monkeypatch):
    marker = tmp_path / "should-not-exist"
    monkeypatch.delenv("MISSING_SELECTED_TOKEN", raising=False)
    target = {"server": {"type": "stdio", "protocol": "legacy", "command": [
        sys.executable, "-c",
        "from pathlib import Path;import sys;Path(sys.argv[1]).write_text('spawned')",
        str(marker)]}, "auth": {
            "kind": "env", "source_env": "MISSING_SELECTED_TOKEN", "target_env": "ACCESS_TOKEN"}}
    cfg = parse_config({"routing": {"parameter": "organization", "catalog": "alpha",
                                    "targets": {"alpha": target}}})
    async with OneShotSession(cfg) as session:
        with pytest.raises(UpstreamError, match="authentication failed"):
            await session.call_tool("read", {"organization": "alpha"})
    assert not marker.exists()

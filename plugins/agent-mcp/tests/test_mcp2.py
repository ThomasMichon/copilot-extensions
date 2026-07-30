"""Dual-era (MCP 2026-07-28 modern vs. legacy) integration tests.

Covers the three seams the era model touches:

* the **client** one-shot negotiating an upstream's era (auto-probe with
  ``server/discover``, forced modern, forced legacy);
* the **HTTP** transport mirroring modern request metadata into headers and
  dropping the retired session id;
* the **cli** responder exposing itself as a dual-era server (``server/discover``,
  cache hints, unsupported-version rejection);
* the ``server.protocol`` config knob.
"""

from __future__ import annotations

import sys

import pytest
import yaml

from agent_mcp import protocol as proto
from agent_mcp.auth.base import NoneInjector
from agent_mcp.client import OneShotSession, result_text
from agent_mcp.config import ConfigError, parse_config
from agent_mcp.transports.cli import CliTransport
from agent_mcp.transports.http import HttpTransport

# --- fixtures: stdio MCP children of each era --------------------------------

# A MODERN upstream: answers server/discover with a real DiscoverResult, has no
# initialize handshake (rejects it), and echoes the protocolVersion it received
# in each request's _meta so a test can prove the client stamped it.
MODERN_CHILD = r"""
import sys, json
MODERN = "2026-07-28"
PV = "io.modelcontextprotocol/protocolVersion"
TOOLS = [{"name":"echo_pv","description":"echo protocol version",
          "inputSchema":{"type":"object","properties":{}}}]
def pv(m):
    return ((m.get("params") or {}).get("_meta") or {}).get(PV)
def handle(m):
    mid=m.get("id"); method=m.get("method")
    if method=="initialize":
        return {"jsonrpc":"2.0","id":mid,"error":{"code":-32601,"message":"modern: no initialize"}}
    if method=="server/discover":
        return {"jsonrpc":"2.0","id":mid,"result":{
            "resultType":"complete","supportedVersions":[MODERN],
            "capabilities":{"tools":{}},
            "_meta":{"io.modelcontextprotocol/serverInfo":{"name":"modern-fixture","version":"2"}},
            "ttlMs":60000,"cacheScope":"public"}}
    if method=="tools/list":
        return {"jsonrpc":"2.0","id":mid,"result":{"tools":TOOLS}}
    if method=="tools/call":
        return {"jsonrpc":"2.0","id":mid,"result":{"content":[{"type":"text","text":str(pv(m))}]}}
    if mid is not None:
        return {"jsonrpc":"2.0","id":mid,"result":{}}
    return None
for line in sys.stdin:
    line=line.strip()
    if not line: continue
    r=handle(json.loads(line))
    if r is not None:
        sys.stdout.write(json.dumps(r)+"\n"); sys.stdout.flush()
"""

# A LEGACY upstream: classic initialize handshake, ignores _meta, echoes the
# tool name. Answers server/discover with an empty {} result (the ambiguous case
# a real legacy server produces for the unknown method) to prove the client
# does NOT mistake it for modern.
LEGACY_CHILD = r"""
import sys, json
def handle(m):
    mid=m.get("id"); method=m.get("method")
    if method=="initialize":
        return {"jsonrpc":"2.0","id":mid,"result":{
            "protocolVersion":"2025-06-18",
            "serverInfo":{"name":"legacy-fixture","version":"1"},"capabilities":{}}}
    if method=="tools/list":
        return {"jsonrpc":"2.0","id":mid,"result":{"tools":[
            {"name":"ping_back","description":"","inputSchema":{"type":"object","properties":{}}}]}}
    if method=="tools/call":
        return {"jsonrpc":"2.0","id":mid,"result":{"content":[{"type":"text","text":"pong"}]}}
    if mid is not None:
        return {"jsonrpc":"2.0","id":mid,"result":{}}
    return None
for line in sys.stdin:
    line=line.strip()
    if not line: continue
    r=handle(json.loads(line))
    if r is not None:
        sys.stdout.write(json.dumps(r)+"\n"); sys.stdout.flush()
"""


def _cfg(child: str, protocol: str = "auto"):
    return parse_config({
        "server": {"type": "stdio", "protocol": protocol,
                   "command": [sys.executable, "-c", child]},
        "auth": {"kind": "none"},
        "timeout": 5,
    })


# --- client: era negotiation -------------------------------------------------

async def test_client_auto_detects_modern_upstream():
    async with OneShotSession(_cfg(MODERN_CHILD)) as sess:
        assert sess.is_modern
        assert sess.protocol_version == proto.MODERN
        assert sess.server_info.get("name") == "modern-fixture"
        tools = await sess.list_tools()
        assert [t["name"] for t in tools] == ["echo_pv"]
        # The echoed protocol version proves the client stamped _meta on the call.
        res = await sess.call_tool("echo_pv", {})
        assert result_text(res) == proto.MODERN


async def test_client_auto_falls_back_to_legacy_on_empty_discover():
    async with OneShotSession(_cfg(LEGACY_CHILD)) as sess:
        assert not sess.is_modern
        assert sess.protocol_version == proto.LEGACY
        assert sess.server_info.get("name") == "legacy-fixture"
        res = await sess.call_tool("ping_back", {})
        assert result_text(res) == "pong"


async def test_client_forced_modern_skips_discover_and_stamps_meta():
    # Force modern against the LEGACY child: it ignores _meta but still answers,
    # proving we skipped the discover probe and went straight to a modern call.
    async with OneShotSession(_cfg(LEGACY_CHILD, protocol="modern")) as sess:
        assert sess.is_modern
        assert sess.protocol_version == proto.MODERN
        res = await sess.call_tool("ping_back", {})
        assert result_text(res) == "pong"


async def test_client_forced_legacy_uses_handshake():
    async with OneShotSession(_cfg(LEGACY_CHILD, protocol="legacy")) as sess:
        assert not sess.is_modern
        assert sess.server_info.get("name") == "legacy-fixture"


async def test_client_quietly_falls_back_when_http_probe_rejected(caplog):
    """A legacy HTTP server rejects the modern ``server/discover`` probe (the
    transport surfaces it as a JSON-RPC error). The one-shot must fall back to
    the legacy handshake **quietly** -- no ERROR log on a routine negotiation."""
    import logging

    from agent_mcp.transports.base import Transport

    class _LegacyHttpish(Transport):
        """400s the discover probe (as HttpTransport does), accepts initialize."""

        def __init__(self) -> None:
            self._emit = None

        async def start(self) -> None:
            pass

        async def send(self, msg: dict) -> None:
            mid, method = msg.get("id"), msg.get("method")
            if method == "server/discover":
                await self._emit_message({"jsonrpc": "2.0", "id": mid,
                                          "error": {"code": -32603, "message": "HTTP 400"}})
            elif method == "initialize":
                await self._emit_message({"jsonrpc": "2.0", "id": mid, "result": {
                    "protocolVersion": "2025-06-18",
                    "serverInfo": {"name": "legacy-http", "version": "1"},
                    "capabilities": {}}})
            elif method == "tools/list":
                await self._emit_message({"jsonrpc": "2.0", "id": mid, "result": {
                    "tools": [{"name": "t", "inputSchema": {"type": "object"}}]}})
            # notifications/initialized: no reply

        async def end_input(self) -> None:
            pass

        async def aclose(self) -> None:
            pass

    cfg = parse_config({"server": {"type": "http", "url": "https://x/y"},
                        "auth": {"kind": "none"}, "timeout": 5})
    with caplog.at_level(logging.DEBUG):
        async with OneShotSession(cfg, transport=_LegacyHttpish()) as sess:
            assert not sess.is_modern
            assert sess.protocol_version == proto.LEGACY
            assert sess.server_info.get("name") == "legacy-http"
            tools = await sess.list_tools()
            assert [x["name"] for x in tools] == ["t"]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


# --- HTTP transport: modern headers vs. legacy session id --------------------

def _http_transport():
    cfg = parse_config({
        "server": {"type": "http", "url": "https://mcp.example/o"},
        "auth": {"kind": "none"},
    })
    t = HttpTransport(cfg, NoneInjector())
    received: list[dict] = []
    t.on_message(lambda m: received.append(m))
    return t, received


async def test_http_modern_request_mirrors_metadata_headers():
    t, _ = _http_transport()
    seen: list[dict] = []

    def fake_post(headers, body):
        seen.append(headers)
        return 200, {"content-type": "application/json"}, '{"jsonrpc":"2.0","id":1,"result":"ok"}'

    t._post = fake_post
    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
           "params": {"name": "search", "arguments": {},
                      "_meta": proto.client_meta(proto.MODERN, {"name": "c", "version": "1"})}}
    await t.send(msg)
    h = seen[0]
    assert h["MCP-Protocol-Version"] == proto.MODERN
    assert h["Mcp-Method"] == "tools/call"
    assert h["Mcp-Name"] == "search"
    assert "Mcp-Session-Id" not in h


async def test_http_modern_does_not_replay_session_id():
    t, _ = _http_transport()
    seen: list[dict] = []

    def fake_post(headers, body):
        seen.append(headers)
        # Server (wrongly) hands back a session id; a modern client must ignore it.
        return 200, {"mcp-session-id": "S1", "content-type": "application/json"}, '{"id":1}'

    t._post = fake_post
    modern = {"jsonrpc": "2.0", "id": 1, "method": "tools/list",
              "params": {"_meta": proto.client_meta(proto.MODERN, {"name": "c"})}}
    await t.send(modern)
    await t.send({**modern, "id": 2})
    assert "Mcp-Session-Id" not in seen[1]


async def test_http_legacy_request_still_uses_session_id():
    t, _ = _http_transport()
    seen: list[dict] = []

    def fake_post(headers, body):
        seen.append(headers)
        return 200, {"mcp-session-id": "S1", "content-type": "application/json"}, '{"id":1}'

    t._post = fake_post
    await t.send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    await t.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    assert "Mcp-Session-Id" not in seen[0]
    assert seen[1]["Mcp-Session-Id"] == "S1"


# --- cli responder: exposed as a dual-era server -----------------------------

def _sidecar(name: str) -> str:
    mcp = {"name": name, "description": f"demo {name}",
           "inputSchema": {"type": "object", "properties": {}},
           "invoke": {"command": sys.executable, "args": ["-c", "print('ok')"]}}
    return "---\n" + yaml.safe_dump({"mcp": mcp}) + "---\n"


def _cli(tmp_path, protocol: str = "auto"):
    (tmp_path / "t.md").write_text(_sidecar("do_it"), encoding="utf-8")
    cfg = parse_config(
        {"server": {"type": "cli", "protocol": protocol, "tools_from": ["t.md"]}},
        source_path=tmp_path / "bridge.yaml")
    t = CliTransport(cfg, NoneInjector())
    emitted: list[dict] = []
    t.on_message(lambda m: emitted.append(m))
    return t, emitted


async def test_cli_answers_server_discover(tmp_path):
    t, out = _cli(tmp_path)
    await t.send({"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {}})
    res = out[-1]["result"]
    assert res["resultType"] == "complete"
    assert proto.MODERN in res["supportedVersions"]
    assert proto.LEGACY in res["supportedVersions"]
    assert res["_meta"][proto.META_SERVER_INFO]["name"].startswith("agent-mcp-cli")
    assert res["ttlMs"] > 0


async def test_cli_tools_list_carries_cache_hints(tmp_path):
    t, out = _cli(tmp_path)
    await t.send({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    res = out[-1]["result"]
    assert [x["name"] for x in res["tools"]] == ["do_it"]
    assert res["ttlMs"] > 0
    assert res["cacheScope"] == "public"


async def test_cli_initialize_negotiates_requested_version(tmp_path):
    t, out = _cli(tmp_path)
    await t.send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                  "params": {"protocolVersion": proto.MODERN}})
    assert out[-1]["result"]["protocolVersion"] == proto.MODERN


async def test_cli_rejects_unsupported_modern_version(tmp_path):
    t, out = _cli(tmp_path)
    await t.send({"jsonrpc": "2.0", "id": 1, "method": "tools/list",
                  "params": {"_meta": {proto.META_PROTOCOL_VERSION: "1999-01-01"}}})
    err = out[-1]["error"]
    assert err["code"] == proto.UNSUPPORTED_PROTOCOL_VERSION
    assert err["data"]["requested"] == "1999-01-01"


async def test_cli_accepts_supported_modern_call(tmp_path):
    t, out = _cli(tmp_path)
    await t.send({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "do_it", "arguments": {},
                             "_meta": {proto.META_PROTOCOL_VERSION: proto.MODERN}}})
    assert out[-1]["result"]["isError"] is False


async def test_cli_forced_legacy_advertises_only_legacy(tmp_path):
    t, out = _cli(tmp_path, protocol="legacy")
    await t.send({"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {}})
    assert out[-1]["result"]["supportedVersions"] == [proto.LEGACY]
    # A modern request is then rejected as unsupported.
    await t.send({"jsonrpc": "2.0", "id": 2, "method": "tools/list",
                  "params": {"_meta": {proto.META_PROTOCOL_VERSION: proto.MODERN}}})
    assert out[-1]["error"]["code"] == proto.UNSUPPORTED_PROTOCOL_VERSION


# --- config: the protocol knob ----------------------------------------------

def test_config_protocol_defaults_to_auto():
    cfg = parse_config({"server": {"type": "http", "url": "https://x/y"}})
    assert cfg.server.protocol == "auto"
    assert cfg.server.protocol_is_auto
    assert cfg.server.forced_version() is None


@pytest.mark.parametrize("value,expected", [
    ("modern", proto.MODERN),
    ("legacy", proto.LEGACY),
    ("2026-07-28", "2026-07-28"),
    ("2025-06-18", "2025-06-18"),
])
def test_config_forced_version_resolves(value, expected):
    cfg = parse_config({"server": {"type": "http", "url": "https://x/y", "protocol": value}})
    assert cfg.server.forced_version() == expected
    assert not cfg.server.protocol_is_auto


def test_config_rejects_bad_protocol():
    with pytest.raises(ConfigError, match=r"server\.protocol"):
        parse_config({"server": {"type": "http", "url": "https://x/y", "protocol": "nonsense"}})

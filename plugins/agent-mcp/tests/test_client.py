"""End-to-end tests for the one-shot upstream client + the ``call`` verb.

Uses a real stdio MCP child (mirroring ``test_stdio``) so the initialize
handshake, ``tools/list`` pagination path, and ``tools/call`` passthrough are
exercised over an actual transport, not a mock.
"""

from __future__ import annotations

import sys

from agent_mcp.__main__ import main
from agent_mcp.client import (
    OneShotSession,
    result_is_error,
    result_structured,
    result_text,
)
from agent_mcp.config import parse_config

# A minimal stdio MCP server: answers initialize, tools/list, tools/call.
MCP_CHILD = r"""
import sys, json
TOOLS = [
  {"name":"greet","description":"Greet someone.",
   "inputSchema":{"type":"object","properties":{"name":{"type":"string"}},
                  "required":["name"]}},
  {"name":"boom","description":"Always errors.",
   "inputSchema":{"type":"object","properties":{}}},
  {"name":"structured","description":"Returns structured content.",
   "inputSchema":{"type":"object","properties":{}}},
]
def handle(m):
    mid = m.get("id"); method = m.get("method")
    if method == "initialize":
        return {"jsonrpc":"2.0","id":mid,"result":{
            "protocolVersion":"2025-06-18",
            "serverInfo":{"name":"fixture","version":"1"},"capabilities":{}}}
    if method == "tools/list":
        return {"jsonrpc":"2.0","id":mid,"result":{"tools":TOOLS}}
    if method == "tools/call":
        p = m.get("params") or {}; name = p.get("name"); args = p.get("arguments") or {}
        if name == "greet":
            return {"jsonrpc":"2.0","id":mid,"result":{"content":[
                {"type":"text","text":"hello "+str(args.get("name",""))}]}}
        if name == "boom":
            return {"jsonrpc":"2.0","id":mid,"result":{
                "content":[{"type":"text","text":"kaboom"}],"isError":True}}
        if name == "structured":
            return {"jsonrpc":"2.0","id":mid,"result":{
                "content":[],"structuredContent":{"ok":True}}}
        return {"jsonrpc":"2.0","id":mid,"result":{
            "content":[{"type":"text","text":"ran "+str(name)}]}}
    if mid is not None:
        return {"jsonrpc":"2.0","id":mid,"result":{}}
    return None
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    r = handle(json.loads(line))
    if r is not None:
        sys.stdout.write(json.dumps(r)+"\n"); sys.stdout.flush()
"""


def _cfg(extra: dict | None = None):
    data = {
        "server": {"type": "stdio", "command": [sys.executable, "-c", MCP_CHILD]},
        "auth": {"kind": "none"},
    }
    if extra:
        data.update(extra)
    return parse_config(data)


def _write_cfg(tmp_path, extra: dict | None = None):
    import json as _json

    data = {
        "server": {"type": "stdio", "command": [sys.executable, "-c", MCP_CHILD]},
        "auth": {"kind": "none"},
    }
    if extra:
        data.update(extra)
    p = tmp_path / "fixture.json"
    p.write_text(_json.dumps(data), encoding="utf-8")
    return p


async def test_oneshot_list_and_call():
    async with OneShotSession(_cfg()) as sess:
        tools = await sess.list_tools()
        assert {t["name"] for t in tools} == {"greet", "boom", "structured"}
        assert sess.server_info.get("name") == "fixture"

        res = await sess.call_tool("greet", {"name": "Cave"})
        assert result_text(res) == "hello Cave"
        assert not result_is_error(res)


async def test_oneshot_error_and_structured():
    async with OneShotSession(_cfg()) as sess:
        err = await sess.call_tool("boom", {})
        assert result_is_error(err)
        assert result_text(err) == "kaboom"

        st = await sess.call_tool("structured", {})
        assert result_structured(st) == {"ok": True}
        assert result_text(st) == ""


async def test_oneshot_tool_filter():
    async with OneShotSession(_cfg({"tools": {"allow": ["greet"]}})) as sess:
        tools = await sess.list_tools()
        assert [t["name"] for t in tools] == ["greet"]


def test_call_verb_success(tmp_path, capsys):
    cfg = _write_cfg(tmp_path)
    rc = main(["call", str(cfg), "greet", '{"name": "Cave"}'])
    out = capsys.readouterr().out
    assert rc == 0
    assert out.strip() == "hello Cave"


def test_call_verb_arguments_flag(tmp_path, capsys):
    cfg = _write_cfg(tmp_path)
    rc = main(["call", str(cfg), "greet", "--arguments", '{"name": "Wheatley"}'])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "hello Wheatley"


def test_call_verb_request_file(tmp_path, capsys):
    cfg = _write_cfg(tmp_path)
    req = tmp_path / "req.json"
    req.write_text('{"arguments": {"name": "GLaDOS"}}', encoding="utf-8")
    rc = main(["call", str(cfg), "greet", "--request-file", str(req)])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "hello GLaDOS"


def test_call_verb_tool_error_exit_code(tmp_path, capsys):
    cfg = _write_cfg(tmp_path)
    rc = main(["call", str(cfg), "boom", "{}"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "kaboom" in captured.err


def test_call_verb_structured_output(tmp_path, capsys):
    cfg = _write_cfg(tmp_path)
    rc = main(["call", str(cfg), "structured", "{}"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == '{"ok": true}'


def test_call_verb_requires_bridge_and_tool(tmp_path, capsys):
    rc = main(["call", "only-one-arg"])
    assert rc == 2
    assert "required" in capsys.readouterr().err


# A stdio child that reads forever but never replies -- proves the one-shot
# bounds its wait instead of hanging on a silent upstream.
SILENT_CHILD = "import sys\nfor _ in sys.stdin:\n    pass\n"


async def test_oneshot_times_out_on_silent_upstream():
    import pytest

    from agent_mcp.client import UpstreamError

    cfg = parse_config({
        "server": {"type": "stdio", "command": [sys.executable, "-c", SILENT_CHILD]},
        "auth": {"kind": "none"},
        "timeout": 0.5,
    })
    with pytest.raises(UpstreamError, match="did not respond"):
        async with OneShotSession(cfg):
            pass  # the initialize handshake itself must time out


async def test_oneshot_tears_down_transport_when_init_fails():
    """A handshake failure must still close the transport (no leaked child)."""
    import pytest

    from agent_mcp.client import UpstreamError
    from agent_mcp.transports.base import Transport

    class _StuckTransport(Transport):
        def __init__(self) -> None:
            self._emit = None
            self.started = self.ended = self.closed = False

        async def start(self) -> None:
            self.started = True

        async def send(self, msg: dict) -> None:
            pass  # never replies -> initialize times out

        async def end_input(self) -> None:
            self.ended = True

        async def aclose(self) -> None:
            self.closed = True

    cfg = parse_config({
        "server": {"type": "stdio", "command": [sys.executable, "-c", SILENT_CHILD]},
        "auth": {"kind": "none"},
        "timeout": 0.2,
    })
    stuck = _StuckTransport()
    with pytest.raises(UpstreamError):
        async with OneShotSession(cfg, transport=stuck):
            pass
    assert stuck.started
    assert stuck.closed  # __aexit__ is skipped on __aenter__ failure; we clean up anyway



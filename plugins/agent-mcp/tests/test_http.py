from __future__ import annotations

import copy

import pytest

from agent_mcp import protocol as proto
from agent_mcp.auth.base import NoneInjector
from agent_mcp.client import CLIENT_INFO, OneShotSession
from agent_mcp.config import parse_config
from agent_mcp.transports.http import HttpTransport


def _transport(injector=None):
    cfg = parse_config({
        "server": {"type": "http", "url": "https://mcp.example/o"},
        "auth": {"kind": "none"},
    })
    t = HttpTransport(cfg, injector or NoneInjector())
    received: list[dict] = []
    t.on_message(lambda m: received.append(m))
    return t, received


@pytest.mark.parametrize("modern", [False, True])
@pytest.mark.parametrize("metadata", ["absent", None, {}, {
    **proto.client_meta("2027-01-01", {"name": "downstream", "version": "1"}, {"roots": {}}),
    "progressToken": "progress-1", "example/trace": {"id": "request-1"},
}])
async def test_prepared_request_headers_follow_upstream_without_mutating_caller(modern, metadata):
    cfg = parse_config({
        "server": {"type": "http", "url": "https://example.invalid/mcp",
                   "protocol": "modern" if modern else "legacy"},
        "auth": {"kind": "none"}, "headers": {"X-Request-Context": "preserved"},
    })
    session = OneShotSession(cfg)
    if modern:
        session._enter_modern(proto.MODERN)
    request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": "read", "arguments": {"value": 7}}}
    if metadata != "absent":
        request["params"]["_meta"] = copy.deepcopy(metadata)
    original = copy.deepcopy(request)
    prepared = session._prepare(request)
    transport = HttpTransport(cfg, NoneInjector())
    transport._session_id = "legacy-session"
    headers = await transport._base_headers(prepared)
    assert request == original
    assert prepared["params"]["arguments"] == {"value": 7}
    assert headers["X-Request-Context"] == "preserved"
    meta = prepared["params"].get("_meta")
    if modern:
        assert headers["MCP-Protocol-Version"] == proto.MODERN
        assert headers["Mcp-Method"] == "tools/call"
        assert headers["Mcp-Name"] == "read"
        assert "Mcp-Session-Id" not in headers
        assert meta[proto.META_CLIENT_INFO] == CLIENT_INFO
        assert meta[proto.META_CLIENT_CAPABILITIES] == {}
    else:
        assert "MCP-Protocol-Version" not in headers
        assert "Mcp-Method" not in headers
        assert "Mcp-Name" not in headers
        assert headers["Mcp-Session-Id"] == "legacy-session"
        assert not isinstance(meta, dict) or not any(
            key in meta for key in (
                proto.META_PROTOCOL_VERSION, proto.META_CLIENT_INFO,
                proto.META_CLIENT_CAPABILITIES,
            )
        )
    if isinstance(metadata, dict) and "progressToken" in metadata:
        assert meta["progressToken"] == metadata["progressToken"]
        assert meta["example/trace"] == metadata["example/trace"]


async def test_sse_response_emits_parsed_objects():
    t, received = _transport()
    sse = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n'
    t._post = lambda headers, body: (200, {"content-type": "text/event-stream"}, sse)
    await t.send({"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert received == [{"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}]


async def test_plain_json_response_emitted():
    t, received = _transport()
    t._post = lambda h, b: (200, {"content-type": "application/json"},
                            '{"jsonrpc":"2.0","id":2,"result":1}')
    await t.send({"jsonrpc": "2.0", "id": 2, "method": "x"})
    assert received[0]["result"] == 1


async def test_202_emits_nothing():
    t, received = _transport()
    t._post = lambda h, b: (202, {}, "")
    await t.send({"jsonrpc": "2.0", "method": "notify"})
    assert received == []


async def test_session_id_captured_and_replayed():
    t, _ = _transport()
    seen_headers: list[dict] = []

    def fake_post(headers, body):
        seen_headers.append(headers)
        return 200, {"mcp-session-id": "S1", "content-type": "application/json"}, '{"id":1}'

    t._post = fake_post
    await t.send({"id": 1})
    await t.send({"id": 2})
    assert "Mcp-Session-Id" not in seen_headers[0]
    assert seen_headers[1]["Mcp-Session-Id"] == "S1"


async def test_401_triggers_invalidate_and_retry():
    class CountingInjector(NoneInjector):
        def __init__(self):
            self.invalidated = 0

        async def invalidate(self):
            self.invalidated += 1

    inj = CountingInjector()
    t, received = _transport(inj)
    responses = iter([
        (401, {}, ""),
        (200, {"content-type": "application/json"}, '{"jsonrpc":"2.0","id":1,"result":"ok"}'),
    ])
    t._post = lambda h, b: next(responses)
    await t.send({"jsonrpc": "2.0", "id": 1, "method": "x"})
    assert inj.invalidated == 1
    assert received[0]["result"] == "ok"


async def test_http_error_status_emits_jsonrpc_error():
    t, received = _transport()
    t._post = lambda h, b: (500, {}, "boom")
    await t.send({"jsonrpc": "2.0", "id": 7, "method": "x"})
    assert received[0]["error"]["code"] == -32603
    assert received[0]["id"] == 7


async def test_http_400_on_discover_probe_is_quiet(caplog):
    """A legacy server's 400 on the ``server/discover`` probe is the expected
    "upstream is legacy" signal -- logged at debug, never error."""
    import logging

    t, received = _transport()
    t._post = lambda h, b: (
        400, {}, '{"error":{"message":"MCP-Protocol-Version 2026-07-28 not supported"}}')
    with caplog.at_level(logging.DEBUG, logger="agent-mcp.http"):
        await t.send({"jsonrpc": "2.0", "id": 1, "method": "server/discover",
                      "params": {}})
    # The client still receives a JSON-RPC error to drive its fallback...
    assert received[0]["error"]["code"] == -32603
    # ...but the transport logged it quietly: no ERROR, a DEBUG probe note.
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("probe rejected" in r.getMessage() for r in caplog.records)


async def test_http_400_on_real_request_is_error(caplog):
    """A 4xx on a real request is still a loud ERROR (a genuine failure)."""
    import logging

    t, _ = _transport()
    t._post = lambda h, b: (400, {}, "bad request")
    with caplog.at_level(logging.DEBUG, logger="agent-mcp.http"):
        await t.send({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                      "params": {"name": "x"}})
    assert any(r.levelno >= logging.ERROR for r in caplog.records)

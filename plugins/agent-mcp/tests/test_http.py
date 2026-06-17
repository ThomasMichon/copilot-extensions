from __future__ import annotations

from agent_mcp.auth.base import NoneInjector
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

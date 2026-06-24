from __future__ import annotations

import asyncio

from agent_mcp.pipeline import Pipeline, UpstreamClient, is_notification, is_request
from agent_mcp.transports.base import Transport


class ScriptedTransport(Transport):
    """A Transport that emits scripted upstream messages in response to sends."""

    def __init__(self, responder) -> None:
        self._emit = None
        self.responder = responder
        self.sent: list[dict] = []

    def on_message(self, handler) -> None:
        self._emit = handler

    async def send(self, msg: dict) -> None:
        self.sent.append(msg)
        for out in self.responder(msg):
            await self._emit(out)


def test_is_request_and_notification():
    assert is_request({"method": "x", "id": 1})
    assert not is_request({"method": "x"})
    assert is_notification({"method": "x"})
    assert not is_notification({"method": "x", "id": 1})


async def test_request_correlates_by_id():
    def responder(msg):
        return [{"jsonrpc": "2.0", "id": msg["id"], "result": {"ok": True}}]

    client = UpstreamClient(ScriptedTransport(responder))
    resp = await client.request({"jsonrpc": "2.0", "id": 7, "method": "ping"})
    assert resp == {"jsonrpc": "2.0", "id": 7, "result": {"ok": True}}


async def test_notification_returns_none_and_is_sent():
    transport = ScriptedTransport(lambda msg: [])
    client = UpstreamClient(transport)
    resp = await client.request({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert resp is None
    assert transport.sent[-1]["method"] == "notifications/initialized"


async def test_unsolicited_message_routed():
    seen: list[dict] = []
    # Upstream emits an extra server notification alongside the response.
    def responder(msg):
        return [
            {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"},
            {"jsonrpc": "2.0", "id": msg["id"], "result": {}},
        ]

    client = UpstreamClient(ScriptedTransport(responder))
    client.on_unsolicited(lambda m: seen.append(m))
    await client.request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert seen and seen[0]["method"] == "notifications/tools/list_changed"


async def test_new_id_is_unique():
    client = UpstreamClient(ScriptedTransport(lambda m: []))
    ids = {client.new_id() for _ in range(5)}
    assert len(ids) == 5


async def test_pipeline_passthrough_order():
    # A decorator that records the order it sees the request vs. response.
    order: list[str] = []

    class Recorder:
        def __init__(self, tag):
            self.tag = tag

        async def handle(self, request, nxt):
            order.append(f"{self.tag}-req")
            resp = await nxt(request)
            order.append(f"{self.tag}-resp")
            return resp

    async def core(request):
        order.append("core")
        return {"jsonrpc": "2.0", "id": request["id"], "result": {}}

    pipe = Pipeline([Recorder("a"), Recorder("b")], core)
    await pipe.handle({"jsonrpc": "2.0", "id": 1, "method": "x"})
    assert order == ["a-req", "b-req", "core", "b-resp", "a-resp"]


async def test_fail_pending_resolves_inflight():
    # A transport that never responds; fail_pending should unstick the request.
    transport = ScriptedTransport(lambda m: [])
    client = UpstreamClient(transport)
    task = asyncio.create_task(
        client.request({"jsonrpc": "2.0", "id": 9, "method": "tools/call"}))
    await asyncio.sleep(0)
    client.fail_pending("shutdown")
    resp = await task
    assert resp["error"]["message"] == "shutdown"

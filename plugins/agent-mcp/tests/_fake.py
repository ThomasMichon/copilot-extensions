"""Shared test helpers: a fake upstream core + bridge context."""

from __future__ import annotations

from agent_mcp.decorators.base import BridgeContext
from agent_mcp.pipeline import Pipeline


def text_result(text: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


class FakeUpstream:
    """A scripted ``core`` Next: answers tools/list + tools/call from a catalog."""

    def __init__(self, tools: list[dict], handlers: dict | None = None,
                 page_size: int | None = None) -> None:
        self.tools = tools
        self.handlers = handlers or {}
        self.calls: list[tuple[str, dict]] = []
        self.list_requests = 0
        self.page_size = page_size

    async def core(self, request: dict):
        method = request.get("method")
        rid = request.get("id")
        if method == "tools/list":
            self.list_requests += 1
            if self.page_size:
                return {"jsonrpc": "2.0", "id": rid, "result": self._page(request)}
            return {"jsonrpc": "2.0", "id": rid, "result": {"tools": self.tools}}
        if method == "tools/call":
            params = request.get("params") or {}
            name = params.get("name")
            args = params.get("arguments") or {}
            self.calls.append((name, args))
            handler = self.handlers.get(name)
            if handler is not None:
                return {"jsonrpc": "2.0", "id": rid, "result": handler(args)}
            return {"jsonrpc": "2.0", "id": rid, "result": text_result(f"ran {name}")}
        if rid is not None:
            return {"jsonrpc": "2.0", "id": rid, "result": {}}
        return None

    def _page(self, request: dict) -> dict:
        cursor = int((request.get("params") or {}).get("cursor", 0) or 0)
        chunk = self.tools[cursor: cursor + self.page_size]
        result = {"tools": chunk}
        nxt = cursor + self.page_size
        if nxt < len(self.tools):
            result["nextCursor"] = str(nxt)
        return result


def make_ctx() -> tuple[BridgeContext, list[dict]]:
    counter = {"n": 0}
    emitted: list[dict] = []

    def new_id() -> str:
        counter["n"] += 1
        return f"int-{counter['n']}"

    def emit(msg: dict) -> None:
        emitted.append(msg)

    return BridgeContext(new_id, emit), emitted


def list_req(rid=1) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "method": "tools/list", "params": {}}


def call_req(name: str, args: dict | None = None, rid=2) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "method": "tools/call",
            "params": {"name": name, "arguments": args or {}}}


def tool(name: str, description: str = "", schema: dict | None = None) -> dict:
    t = {"name": name, "description": description}
    if schema is not None:
        t["inputSchema"] = schema
    return t


def names_in(resp: dict) -> list[str]:
    return [t["name"] for t in resp["result"]["tools"]]


def run(decorator, upstream: FakeUpstream, request: dict):
    """Run one request through a single-decorator pipeline (returns coroutine)."""
    return Pipeline([decorator], upstream.core).handle(request)

"""Decorator pipeline + upstream JSON-RPC client.

The bridge is a chain of **decorators** wrapping a single **upstream core**:

    client  <->  decorator[0]  <->  ...  <->  decorator[n-1]  <->  core(upstream)

A client request flows *down* the stack (``decorator[0]`` first) and the response
bubbles back *up*. Each decorator calls ``nxt(request)`` to forward toward the
upstream and receives the response, which it may transform; or it may
short-circuit by returning a synthesized response (echoing the request ``id``)
without calling ``nxt`` at all -- this is how synthetic tools work.

:class:`UpstreamClient` adapts a streaming :class:`~agent_mcp.transports.base.Transport`
(send + ``on_message`` callback) into a request/response ``await``-able call by
correlating JSON-RPC ``id``s. Server-initiated messages that match no pending
request (e.g. ``notifications/tools/list_changed``) are routed to an
*unsolicited* handler, which the bridge forwards straight to the client.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from .transports.base import Transport

log = logging.getLogger("agent-mcp.pipeline")

# A pipeline link: forward a client->server message and return the response
# (for a request with an ``id``) or ``None`` (for a notification).
Next = Callable[[dict], Awaitable[dict | None]]


def is_request(msg: dict) -> bool:
    """True if ``msg`` is a JSON-RPC request (has both ``method`` and ``id``)."""
    return "method" in msg and msg.get("id") is not None


def is_notification(msg: dict) -> bool:
    """True if ``msg`` is a JSON-RPC notification (``method``, no ``id``)."""
    return "method" in msg and msg.get("id") is None


def error_response(request: dict, message: str, code: int = -32603) -> dict:
    """Build a JSON-RPC error response echoing ``request``'s id."""
    return {"jsonrpc": "2.0", "id": request.get("id"),
            "error": {"code": code, "message": message}}


def result_response(request: dict, result: Any) -> dict:
    """Build a JSON-RPC success response echoing ``request``'s id."""
    return {"jsonrpc": "2.0", "id": request.get("id"), "result": result}


class UpstreamClient:
    """Request/response client over a streaming transport (correlates by id)."""

    def __init__(self, transport: Transport) -> None:
        self.transport = transport
        self._pending: dict[Any, asyncio.Future] = {}
        self._counter = 0
        self._unsolicited: Callable[[dict], Awaitable[None] | None] | None = None
        transport.on_message(self._on_upstream)

    def on_unsolicited(self, handler: Callable[[dict], Awaitable[None] | None]) -> None:
        """Register the sink for server-initiated / uncorrelated messages."""
        self._unsolicited = handler

    def new_id(self) -> str:
        """A fresh internal request id that will not collide with client ids."""
        self._counter += 1
        return f"amcp-{self._counter}"

    async def _on_upstream(self, msg: dict) -> None:
        mid = msg.get("id")
        # A response (id present, no method) for a request we are awaiting.
        if mid is not None and "method" not in msg and mid in self._pending:
            fut = self._pending.pop(mid)
            if not fut.done():
                fut.set_result(msg)
            return
        # Otherwise it is unsolicited (server notification/request, or a late
        # response we no longer track) -- hand it to the bridge for passthrough.
        if self._unsolicited is not None:
            res = self._unsolicited(msg)
            if res is not None:
                await res

    async def request(self, msg: dict) -> dict | None:
        """Forward a client->server message; await + return the response if any."""
        if is_notification(msg) or "method" not in msg:
            # Notification, or a client->server response to a server request:
            # fire-and-forget, no reply is expected.
            await self.transport.send(msg)
            return None

        mid = msg["id"]
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[mid] = fut
        try:
            await self.transport.send(msg)
        except Exception as exc:  # surface as a JSON-RPC error
            self._pending.pop(mid, None)
            log.error("upstream send failed: %s", exc)
            return error_response(msg, f"upstream send failed: {exc}")
        return await fut

    def fail_pending(self, message: str = "bridge shutting down") -> None:
        """Resolve every in-flight request with an error (on shutdown)."""
        for mid, fut in list(self._pending.items()):
            if not fut.done():
                fut.set_result({"jsonrpc": "2.0", "id": mid,
                                "error": {"code": -32603, "message": message}})
        self._pending.clear()


class Pipeline:
    """Composes decorators around an upstream ``core`` call."""

    def __init__(self, decorators: list, core: Next) -> None:
        self.decorators = decorators
        self.core = core

    async def handle(self, request: dict) -> dict | None:
        """Run ``request`` through the full decorator stack to the upstream core."""
        nxt = self.core
        for dec in reversed(self.decorators):
            nxt = _bind(dec, nxt)
        return await nxt(request)

    async def aclose(self) -> None:
        for dec in self.decorators:
            close = getattr(dec, "aclose", None)
            if close is not None:
                await close()


def _bind(decorator, nxt: Next) -> Next:
    async def call(request: dict) -> dict | None:
        return await decorator.handle(request, nxt)

    return call

"""Decorator base class and the per-bridge context handed to decorators."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from ..pipeline import Next, error_response, result_response

__all__ = ["BridgeContext", "Decorator", "Next", "error_response", "result_response"]


class BridgeContext:
    """Shared capabilities a decorator may need beyond the ``nxt`` chain.

    * :meth:`new_id` -- a fresh upstream request id (never collides with client ids).
    * :meth:`emit_to_client` -- push a server->client message (e.g. a
      ``notifications/tools/list_changed``) to the client out-of-band.
    """

    def __init__(self, new_id: Callable[[], str],
                 emit_to_client: Callable[[dict], Awaitable[None] | None]) -> None:
        self._new_id = new_id
        self._emit = emit_to_client

    def new_id(self) -> str:
        return self._new_id()

    async def emit_to_client(self, msg: dict) -> None:
        res = self._emit(msg)
        if res is not None:
            await res


class Decorator:
    """A single MCP middleware. Override :meth:`handle`.

    The default implementation is a pass-through. Subclasses inspect or rewrite
    the request, call ``await nxt(request)`` to reach the upstream (through the
    decorators below it), and may transform the response. To implement a
    synthetic tool, return a response built with :func:`result_response` /
    :func:`error_response` *without* calling ``nxt``.
    """

    type: str = "decorator"

    def __init__(self, options: dict, ctx: BridgeContext) -> None:
        self.options = options
        self.ctx = ctx

    async def handle(self, request: dict, nxt: Next) -> dict | None:
        return await nxt(request)

    async def aclose(self) -> None:
        """Release any decorator-owned resources (child processes, files)."""
        return None

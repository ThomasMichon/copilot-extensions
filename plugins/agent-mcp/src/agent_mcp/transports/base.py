"""Transport abstraction (upstream side of the bridge).

A transport owns the connection to one upstream MCP server and moves JSON-RPC
messages in both directions:

* the bridge calls :meth:`send` to forward a client->server message;
* the transport calls the registered ``on_message`` callback for each
  server->client message it receives.

This bidirectional, streaming model fits both a request/response HTTP server
(events arrive in the POST response) and a long-lived stdio child (messages
arrive asynchronously on the child's stdout).
"""

from __future__ import annotations

import abc
from collections.abc import Awaitable, Callable

from ..auth.base import AuthInjector
from ..config import BridgeConfig

MessageHandler = Callable[[dict], Awaitable[None] | None]


class Transport(abc.ABC):
    """Owns the upstream MCP connection for one bridge."""

    def __init__(self, cfg: BridgeConfig, injector: AuthInjector) -> None:
        self.cfg = cfg
        self.injector = injector
        self._emit: MessageHandler | None = None

    def on_message(self, handler: MessageHandler) -> None:
        """Register the callback for server->client messages."""
        self._emit = handler

    async def _emit_message(self, msg: dict) -> None:
        if self._emit is None:
            return
        result = self._emit(msg)
        if result is not None:
            await result

    async def start(self) -> None:
        """Establish the upstream connection (no-op for stateless transports)."""
        return None

    @abc.abstractmethod
    async def send(self, msg: dict) -> None:
        """Forward a client->server JSON-RPC message upstream."""

    async def end_input(self) -> None:
        """Signal that no more client messages will be sent (client stdin EOF).

        Transports that wrap a child should propagate EOF and let in-flight
        server output drain before :meth:`aclose`. No-op for stateless transports.
        """
        return None

    async def aclose(self) -> None:
        """Tear down the upstream connection."""
        return None

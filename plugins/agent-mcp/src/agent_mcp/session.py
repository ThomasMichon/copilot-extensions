"""Shared MCP bridge session: the upstream connection + decorator pipeline for
one client, independent of how that client is attached.

Both the stdio bridge (``agent-mcp bridge``) and the resident ``serve``
session-host run a client's MCP session the same way -- its own upstream
connection, its own decorator :class:`~agent_mcp.pipeline.Pipeline`, and its own
server->client notification stream -- differing only in the client transport
(process stdio vs. the serve socket). Extracting it here lets the resident
session-host host **many** client sessions in one interpreter (the work-coalescing
multiplexer, #744) while the stdio bridge keeps running one per process.

The session writes every client-bound message (a response, a decorator-emitted
push, or an unsolicited upstream notification) through the injected
``write_to_client`` sink; the caller owns how that reaches the actual client.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from .auth import build_injector
from .config import BridgeConfig
from .decorators import BridgeContext, build_decorators
from .pipeline import Pipeline, UpstreamClient, error_response, is_request
from .transports import build_transport

log = logging.getLogger("agent-mcp.session")

# A client-bound sink: called with a fully-formed JSON-RPC message object to
# deliver to the client (response, decorator push, or upstream notification).
ClientSink = Callable[[dict], None]


class BridgeSession:
    """One client's upstream + decorator pipeline, driven message-by-message.

    Lifecycle: :meth:`start` builds and connects the upstream; :meth:`submit`
    dispatches a client message concurrently (its response is written to the
    sink); :meth:`aclose` drains in-flight work and tears the upstream down.
    """

    def __init__(self, cfg: BridgeConfig, write_to_client: ClientSink) -> None:
        self.cfg = cfg
        self._write = write_to_client
        self._transport = None
        self._client: UpstreamClient | None = None
        self._pipeline: Pipeline | None = None
        self._tasks: set[asyncio.Task] = set()

    @property
    def decorator_count(self) -> int:
        return len(self._pipeline.decorators) if self._pipeline is not None else 0

    async def start(self) -> None:
        """Build the injector + transport + upstream client + pipeline, connect."""
        injector = build_injector(self.cfg)
        self._transport = build_transport(self.cfg, injector)
        self._client = UpstreamClient(self._transport)
        # Server-initiated / uncorrelated upstream messages pass straight to the
        # client; a decorator's emit_to_client does too.
        self._client.on_unsolicited(self._write)
        ctx = BridgeContext(new_id=self._client.new_id, emit_to_client=self._write)
        self._pipeline = Pipeline(build_decorators(self.cfg, ctx), self._client.request)
        await self._transport.start()

    async def _dispatch(self, msg: dict) -> None:
        assert self._pipeline is not None
        try:
            resp = await self._pipeline.handle(msg)
        except Exception as exc:  # never let one request kill the session
            log.error("pipeline error: %s", exc)
            resp = error_response(msg, f"bridge error: {exc}") if is_request(msg) else None
        if resp is not None:
            self._write(resp)

    def submit(self, msg: dict) -> None:
        """Dispatch a client->server message concurrently; reply goes to the sink."""
        task = asyncio.create_task(self._dispatch(msg))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def aclose(self) -> None:
        """Drain in-flight dispatches and tear the upstream down."""
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._pipeline is not None:
            await self._pipeline.aclose()
        if self._client is not None:
            self._client.fail_pending()
        if self._transport is not None:
            await self._transport.end_input()
            await self._transport.aclose()

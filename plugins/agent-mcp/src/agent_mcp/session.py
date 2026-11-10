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
from collections.abc import Awaitable, Callable

from .auth import build_injector
from .config import BridgeConfig
from .decorators import BridgeContext, build_decorators
from .pipeline import Pipeline, UpstreamClient, error_response, is_request
from .transports import build_transport

log = logging.getLogger("agent-mcp.session")

# A client-bound sink: called with a fully-formed JSON-RPC message object to
# deliver to the client (response, decorator push, or upstream notification).
# May be sync or async -- an async sink (returning an awaitable) is awaited, the
# same convention BridgeContext.emit_to_client and UpstreamClient already use, so
# a caller can apply write flow control (e.g. awaiting a socket drain).
ClientSink = Callable[[dict], Awaitable[None] | None]


class BridgeSession:
    """One client's upstream + decorator pipeline, driven message-by-message.

    Lifecycle: :meth:`start` builds and connects the upstream; :meth:`submit`
    dispatches a client message concurrently (its response is written to the
    sink); :meth:`aclose` drains in-flight work and tears the upstream down.
    """

    def __init__(
        self,
        cfg: BridgeConfig,
        write_to_client: ClientSink,
        *,
        transport=None,
        checkpoint_binding=None,
        resume: dict | None = None,
    ) -> None:
        self.cfg = cfg
        self._write = write_to_client
        self._transport = transport
        self._checkpoint_binding = checkpoint_binding
        self._checkpoint_identity = None
        self._resume = resume
        self._client: UpstreamClient | None = None
        self._pipeline: Pipeline | None = None
        self._tasks: set[asyncio.Task] = set()
        self._router = None

    @property
    def decorator_count(self) -> int:
        return len(self._pipeline.decorators) if self._pipeline is not None else 0

    @property
    def has_pending(self) -> bool:
        """True while at least one client message is mid-dispatch.

        The authoritative liveness signal for an idle self-reap (#3876): a
        bridge must never be reaped while a request is still awaiting its
        upstream response, however long that takes, even past an otherwise
        elapsed idle window.
        """
        return bool(self._tasks)

    async def start(self) -> None:
        """Build the injector + transport + upstream client + pipeline, connect."""
        restored = None
        if self._checkpoint_binding is not None:
            self._checkpoint_identity = self._checkpoint_binding.identity(self.cfg)
        if self._resume is not None:
            from . import checkpoint as cp

            if self._transport is None or self._checkpoint_binding is None:
                raise cp.CheckpointRefused("resume requires a custody-owned transport and binding")
            restored = cp.unpack(self.cfg, self._resume, self._checkpoint_binding)
            if set(restored) != {"pipeline", "client"}:
                raise cp.CheckpointRefused("session checkpoint schema differs")
        if self.cfg.routing is not None:
            if self._checkpoint_binding is not None:
                from .checkpoint import CheckpointRefused

                raise CheckpointRefused("routed custody has not been qualified")
            from .routing import RoutedCore

            self._router = RoutedCore(self.cfg)
            ctx = BridgeContext(new_id=self._router.new_id, emit_to_client=self._write)
            self._pipeline = Pipeline(build_decorators(self.cfg, ctx), self._router.handle)
            return
        if self._transport is None:
            injector = build_injector(self.cfg)
            self._transport = build_transport(self.cfg, injector)
        self._client = UpstreamClient(self._transport)
        # Server-initiated / uncorrelated upstream messages pass straight to the
        # client; a decorator's emit_to_client does too.
        self._client.on_unsolicited(self._write)
        ctx = BridgeContext(new_id=self._client.new_id, emit_to_client=self._write)
        self._pipeline = Pipeline(build_decorators(self.cfg, ctx), self._client.request)
        if restored is not None:
            cp.restore_client(self._client, restored["client"])
            cp.restore_pipeline(self._pipeline, restored["pipeline"])
        # Bound the upstream spawn/connect the same way OneShotSession does
        # (see the downstream tracker): an unbounded ``transport.start()`` here
        # doesn't wedge a shared lock the way a WarmPool entry-open would, but
        # it still leaves one caller (the ``agent-mcp bridge`` stdio process,
        # or one ``serve`` attach connection) hanging forever with no
        # diagnostic, holding an fd/subprocess slot open indefinitely.
        try:
            await asyncio.wait_for(self._transport.start(), timeout=self.cfg.timeout)
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise RuntimeError(
                f"upstream transport did not start within {self.cfg.timeout}s"
            ) from exc

    async def _dispatch(self, msg: dict) -> None:
        assert self._pipeline is not None
        try:
            resp = await self._pipeline.handle(msg)
        except Exception as exc:  # never let one request kill the session
            log.error("pipeline error: %s", exc)
            resp = error_response(msg, f"bridge error: {exc}") if is_request(msg) else None
        if resp is not None:
            # The sink may be sync or async; await it when async (matching the
            # emit_to_client / unsolicited convention) so a socket sink can apply
            # write backpressure via drain().
            res = self._write(resp)
            if res is not None:
                await res

    def submit(self, msg: dict) -> asyncio.Task:
        """Dispatch a client->server message concurrently; reply goes to the sink."""
        task = asyncio.create_task(self._dispatch(msg))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def quiesce(self) -> None:
        """Join complete dispatches, including subcalls and response transformation."""
        while self._tasks:
            tasks = tuple(self._tasks)
            await asyncio.shield(asyncio.gather(*tasks, return_exceptions=True))
            self._tasks.difference_update(task for task in tasks if task.done())

    def checkpoint(self) -> dict:
        """Export quiescent single-upstream data for this custody session only."""
        from . import checkpoint as cp

        if (
            self._tasks
            or self._pipeline is None
            or self._client is None
            or self._checkpoint_binding is None
            or self._router is not None
        ):
            raise cp.CheckpointRefused("session has not quiesced with qualified custody")
        if self._checkpoint_binding.identity(self.cfg) != self._checkpoint_identity:
            raise cp.CheckpointRefused("execution context changed after upstream admission")
        return cp.envelope(
            self.cfg,
            {
                "pipeline": cp.pipeline_state(self._pipeline),
                "client": cp.client_state(self._client),
            },
            self._checkpoint_binding,
        )

    async def aclose(self) -> None:
        """Drain in-flight dispatches and tear the upstream down."""
        await self.quiesce()
        if self._pipeline is not None:
            await self._pipeline.aclose()
        if self._router is not None:
            await self._router.aclose()
        if self._client is not None:
            self._client.fail_pending()
        if self._transport is not None:
            await self._transport.end_input()
            await self._transport.aclose()

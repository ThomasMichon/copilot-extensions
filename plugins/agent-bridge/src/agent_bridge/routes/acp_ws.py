"""Remote ACP transport -- expose ``BridgeAgent`` over a WebSocket.

acp-ui (https://acp-ui.github.io) and other ACP clients connect here to drive a
downstream agent through the bridge, so a human can inspect and steer remote
agents from a chat UI. Two entry points map a connection to a target:

* ``WS /acp/{agent}``            -- spawn a fresh session for a registered agent
* ``WS /acp/session/{session_id}`` -- adopt an already-running bridge session
  (e.g. one started by an inter-agent ``send``) for observe/steer

Auth: the browser WebSocket API cannot attach arbitrary HTTP headers, so the
bridge token is carried as a ``bearer.<token>`` WebSocket subprotocol (acp-ui's
convention). A plain ``Authorization: Bearer <token>`` header is also honored
for non-browser clients. The ``BearerAuthMiddleware`` only covers the ``http``
scope, so auth is enforced here for the ``websocket`` scope.

The ACP SDK's :class:`AgentSideConnection` speaks newline-delimited JSON-RPC
over an asyncio ``StreamReader``/``StreamWriter`` pair. We back those streams
with the WebSocket entirely in memory -- mirroring ``acp.stdio``'s Windows
stream shim -- so no sockets or subprocesses are involved.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from asyncio import transports as aio_transports
from typing import Any, cast

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from ..acp_agent import BridgeAgent

log = logging.getLogger("agent-bridge")

router = APIRouter()

# Canonical ACP-over-WebSocket subprotocol advertised by acp-ui. We negotiate it
# back when offered so the browser's subprotocol selection succeeds.
_ACP_SUBPROTOCOL = "acp.v1"
# acp-ui folds ``Authorization: Bearer <token>`` into this subprotocol entry.
_BEARER_PREFIX = "bearer."
# Generous frame buffer for multimodal prompts (matches acp.core's default).
_READER_LIMIT = 50 * 1024 * 1024


class _WsOutTransport(asyncio.BaseTransport):
    """asyncio write transport that forwards NDJSON frames onto an out queue.

    The ACP ``Connection`` writes ``json + "\\n"`` per message. We split on
    newlines and enqueue each complete JSON line; a single drain task delivers
    them to the WebSocket in order (one WS text frame per JSON-RPC frame).
    """

    def __init__(self, out_queue: asyncio.Queue[str | None]) -> None:
        self._queue = out_queue
        self._closing = False
        self._buf = bytearray()

    def write(self, data: bytes) -> None:  # type: ignore[override]
        if self._closing:
            return
        self._buf.extend(data)
        while True:
            nl = self._buf.find(b"\n")
            if nl < 0:
                break
            line = bytes(self._buf[:nl]).strip()
            del self._buf[: nl + 1]
            if line:
                self._queue.put_nowait(line.decode("utf-8", "replace"))

    def is_closing(self) -> bool:  # type: ignore[override]
        return self._closing

    def close(self) -> None:  # type: ignore[override]
        if self._closing:
            return
        self._closing = True
        tail = bytes(self._buf).strip()
        self._buf.clear()
        if tail:
            self._queue.put_nowait(tail.decode("utf-8", "replace"))
        # Sentinel: tell the drain task there is nothing more to send.
        self._queue.put_nowait(None)

    def abort(self) -> None:  # type: ignore[override]
        self.close()

    def get_extra_info(self, name: str, default: Any = None) -> Any:  # type: ignore[override]
        return default


class _DrainProtocol(asyncio.BaseProtocol):
    """Minimal protocol supplying ``_drain_helper`` for ``StreamWriter.drain``.

    The out queue is unbounded, so writes never block and drain is a no-op.
    """

    async def _drain_helper(self) -> None:
        return


def _extract_bearer(subprotocols: list[str]) -> str | None:
    """Return the token from a ``bearer.<token>`` subprotocol, if present."""
    for proto in subprotocols:
        if proto.startswith(_BEARER_PREFIX):
            return proto[len(_BEARER_PREFIX):]
    return None


def _provided_token(ws: WebSocket, subprotocols: list[str]) -> str | None:
    """Resolve the caller's token from subprotocol or Authorization header."""
    token = _extract_bearer(subprotocols)
    if token:
        return token
    header = ws.headers.get("authorization", "")
    if header.startswith("Bearer "):
        return header[7:]
    return None


async def _run_bridge_ws(
    ws: WebSocket,
    *,
    agent_name: str | None,
    adopt_session_id: str | None,
) -> None:
    """Authenticate, accept, and bridge a WebSocket to an ACP ``BridgeAgent``."""
    mgr = ws.app.state.session_manager
    resolver = getattr(ws.app.state, "resolver", None)
    expected = getattr(ws.app.state, "auth_token", None)
    offered = list(ws.scope.get("subprotocols", []))

    # --- Auth (websocket scope bypasses BearerAuthMiddleware) ---------------
    if expected:
        provided = _provided_token(ws, offered)
        if provided != expected:
            log.warning("ACP WS auth rejected (path=%s)", ws.url.path)
            await ws.close(code=1008)  # policy violation
            return

    # --- Validate target before accepting for a clean rejection -------------
    if agent_name is not None and resolver and agent_name not in resolver.agents:
        log.warning("ACP WS unknown agent '%s'", agent_name)
        await ws.close(code=1011)
        return
    if adopt_session_id is not None and mgr.get_session(adopt_session_id) is None:
        log.warning("ACP WS unknown session '%s'", adopt_session_id)
        await ws.close(code=1011)
        return

    subprotocol = _ACP_SUBPROTOCOL if _ACP_SUBPROTOCOL in offered else None
    await ws.accept(subprotocol=subprotocol)
    log.info(
        "ACP WS connected (agent=%s, adopt=%s)",
        agent_name or "-", adopt_session_id or "-",
    )

    loop = asyncio.get_running_loop()
    out_queue: asyncio.Queue[str | None] = asyncio.Queue()
    reader = asyncio.StreamReader(limit=_READER_LIMIT)
    out_transport = _WsOutTransport(out_queue)
    writer = asyncio.StreamWriter(
        cast(aio_transports.WriteTransport, out_transport),
        _DrainProtocol(),
        None,
        loop,
    )

    agent = BridgeAgent(
        mgr,
        resolver=resolver,
        default_agent=agent_name,
        adopt_session_id=adopt_session_id,
    )

    # Imported from the canonical location (matching acp_agent.py) to keep
    # explicit control over the connection lifecycle.
    from acp.agent.connection import AgentSideConnection

    conn = AgentSideConnection(agent, writer, reader, listening=False)

    async def _pump_in() -> None:
        """WebSocket text frames -> ACP reader (newline-framed)."""
        try:
            while True:
                text = await ws.receive_text()
                reader.feed_data(text.encode("utf-8") + b"\n")
        except WebSocketDisconnect:
            pass
        except Exception:
            log.debug("ACP WS inbound pump stopped", exc_info=True)
        finally:
            reader.feed_eof()

    async def _pump_out() -> None:
        """ACP writer queue -> WebSocket text frames (ordered)."""
        try:
            while True:
                line = await out_queue.get()
                if line is None:
                    break
                if ws.application_state != WebSocketState.CONNECTED:
                    break
                await ws.send_text(line)
        except Exception:
            log.debug("ACP WS outbound pump stopped", exc_info=True)

    in_task = asyncio.create_task(_pump_in())
    out_task = asyncio.create_task(_pump_out())
    try:
        # Runs the JSON-RPC receive loop; returns when the reader hits EOF
        # (client disconnect).
        await conn.listen()
    finally:
        out_transport.close()  # enqueue sentinel to stop _pump_out
        in_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await out_task
        with contextlib.suppress(Exception):
            await conn.close()
        with contextlib.suppress(Exception):
            await agent.cleanup()
        if ws.application_state == WebSocketState.CONNECTED:
            with contextlib.suppress(Exception):
                await ws.close()
        log.info("ACP WS closed (agent=%s)", agent_name or "-")


@router.websocket("/acp/session/{session_id}")
async def acp_ws_session(ws: WebSocket, session_id: str) -> None:
    """Adopt an existing bridge session over ACP (observe/steer)."""
    await _run_bridge_ws(ws, agent_name=None, adopt_session_id=session_id)


@router.websocket("/acp/{agent}")
async def acp_ws_agent(ws: WebSocket, agent: str) -> None:
    """Spawn a fresh session for a registered agent over ACP."""
    await _run_bridge_ws(ws, agent_name=agent, adopt_session_id=None)

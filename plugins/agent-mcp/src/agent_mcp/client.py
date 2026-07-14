"""One-shot upstream MCP client: connect, initialize, list/call, exit.

The bridge (:mod:`agent_mcp.bridge`) keeps a long-lived stdio session that
proxies a *client's* JSON-RPC traffic to one upstream. The **materialize** and
**call** verbs need the opposite shape: drive the upstream *ourselves* for a
single, stateless interaction -- fetch the tool catalog, or invoke one tool --
then tear down.

:class:`OneShotSession` reuses the same transport + auth-injector stack as the
bridge (so http/sse/stdio and credential injection all behave identically), adds
the MCP ``initialize`` handshake the bridge normally relays from its client, and
exposes two calls: :meth:`list_tools` and :meth:`call_tool`. It is the execution
substrate under ``agent-mcp call`` and the introspection step of
``agent-mcp materialize``.

There is deliberately **no per-call daemon** here: a one-shot connect avoids the
per-session ``uv run``/``npx`` cold-start that a resident MCP server pays, while
still being a live stdio/http session (a future ``agent-mcp serve`` can hold the
connection warm; the stubs fall back to this path when it is absent).
"""

from __future__ import annotations

import asyncio
import logging

from . import __version__
from .auth import build_injector
from .config import BridgeConfig, ToolFilter
from .decorators._catalog import fetch_all_tools
from .decorators.base import BridgeContext
from .pipeline import UpstreamClient
from .transports import Transport, build_transport

log = logging.getLogger("agent-mcp.client")

# The MCP protocol revision we advertise in ``initialize``. Servers negotiate
# down if they speak an older one; this matches the revision the bridge's own
# clients use in practice.
PROTOCOL_VERSION = "2025-06-18"


class UpstreamError(RuntimeError):
    """An upstream returned a JSON-RPC error (carries the error mapping)."""

    def __init__(self, message: str, *, code: int | None = None,
                 data: object | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


def _filter_tools(tools: list[dict], flt: ToolFilter) -> list[dict]:
    """Apply the bridge config's allow/deny filter to a raw tool list."""
    if not flt.active:
        return list(tools)
    import fnmatch

    kept = []
    for t in tools:
        name = t.get("name", "") if isinstance(t, dict) else ""
        if flt.deny and any(fnmatch.fnmatchcase(name, p) for p in flt.deny):
            continue
        if flt.allow and not any(fnmatch.fnmatchcase(name, p) for p in flt.allow):
            continue
        kept.append(t)
    return kept


class OneShotSession:
    """Drive one upstream MCP for a single stateless interaction.

    Usage::

        async with OneShotSession(cfg) as sess:
            tools = await sess.list_tools()
            result = await sess.call_tool("create_issue", {"title": "x"})

    A ``transport`` may be injected (tests, or a pre-built connection); otherwise
    it is constructed from ``cfg`` exactly as the bridge would.
    """

    def __init__(self, cfg: BridgeConfig, *, transport: Transport | None = None) -> None:
        self.cfg = cfg
        self._transport = transport
        self._client: UpstreamClient | None = None
        self._ctx: BridgeContext | None = None
        self._server_info: dict = {}

    async def __aenter__(self) -> OneShotSession:
        injector = build_injector(self.cfg)
        transport = self._transport or build_transport(self.cfg, injector)
        self._transport = transport
        client = UpstreamClient(transport)
        # We drive the upstream ourselves; server-initiated notifications during a
        # one-shot (e.g. list_changed) have no client to reach -- drop them.
        client.on_unsolicited(lambda _msg: None)
        self._client = client
        self._ctx = BridgeContext(new_id=client.new_id, emit_to_client=lambda _m: None)

        await transport.start()
        try:
            await self._initialize()
        except BaseException:
            # __aexit__ is not called when __aenter__ raises, so tear the
            # transport down here or a spawned upstream child would leak.
            await self._teardown()
            raise
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self._teardown()

    async def _teardown(self) -> None:
        client, transport = self._client, self._transport
        if client is not None:
            client.fail_pending("one-shot session closing")
        if transport is not None:
            try:
                await transport.end_input()
            finally:
                await transport.aclose()

    async def _initialize(self) -> None:
        client = self._need_client()
        init_req = {
            "jsonrpc": "2.0",
            "id": client.new_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "agent-mcp", "version": __version__},
            },
        }
        resp = await self._request(init_req)
        if isinstance(resp, dict):
            if "error" in resp:
                _raise_error(resp["error"], context="initialize")
            result = resp.get("result")
            if isinstance(result, dict):
                self._server_info = result.get("serverInfo") or {}
        # The initialized notification has no id and expects no reply.
        await self._request({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _need_client(self) -> UpstreamClient:
        if self._client is None:
            raise RuntimeError("OneShotSession used outside its async context")
        return self._client

    async def _request(self, msg: dict) -> dict | None:
        """Send one upstream request under a bounded timeout.

        Unlike the long-lived bridge, a one-shot must never block forever on a
        dead or silent upstream (a crashed stdio child, a hung endpoint). A
        request that carries an ``id`` is bounded by ``cfg.timeout``; a
        notification (no reply expected) is sent without waiting.
        """
        client = self._need_client()
        if msg.get("id") is None:
            return await client.request(msg)
        try:
            return await asyncio.wait_for(client.request(msg), timeout=self.cfg.timeout)
        except (TimeoutError, asyncio.TimeoutError) as exc:
            method = msg.get("method", "request")
            raise UpstreamError(
                f"{method}: upstream did not respond within {self.cfg.timeout}s"
            ) from exc

    async def _paginated_request(self, msg: dict) -> dict | None:
        """The ``Next``-shaped callable handed to :func:`fetch_all_tools`."""
        return await self._request(msg)

    @property
    def server_info(self) -> dict:
        """The upstream's ``serverInfo`` from the initialize result (may be empty)."""
        return self._server_info

    async def list_tools(self) -> list[dict]:
        """Fetch the full (paginated) upstream catalog, honoring the tool filter."""
        self._need_client()
        if self._ctx is None:
            raise RuntimeError("OneShotSession used outside its async context")
        tools = await fetch_all_tools(self._paginated_request, self._ctx)
        return _filter_tools(tools, self.cfg.tools)

    async def call_tool(self, name: str, arguments: dict) -> dict:
        """Invoke one tool; return the ``tools/call`` result mapping.

        Raises :class:`UpstreamError` on a JSON-RPC error. A tool that reports a
        semantic failure via ``isError`` is returned normally (the caller decides
        the exit code) -- only a protocol-level error raises.
        """
        client = self._need_client()
        req = {
            "jsonrpc": "2.0",
            "id": client.new_id(),
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        }
        resp = await self._request(req)
        if not isinstance(resp, dict):
            raise UpstreamError(f"no response for tools/call '{name}'")
        if "error" in resp:
            _raise_error(resp["error"], context=f"tools/call '{name}'")
        result = resp.get("result")
        if not isinstance(result, dict):
            raise UpstreamError(f"malformed tools/call result for '{name}'")
        return result


def _raise_error(error: object, *, context: str) -> None:
    if isinstance(error, dict):
        msg = str(error.get("message") or "upstream error")
        raise UpstreamError(f"{context}: {msg}", code=error.get("code"),
                            data=error.get("data"))
    raise UpstreamError(f"{context}: {error}")


def result_text(result: dict) -> str:
    """Concatenate the text ``content`` blocks of a ``tools/call`` result.

    Raw passthrough: the upstream's text content verbatim, joined by newlines.
    Non-text blocks (images, resources) are skipped here -- see
    :func:`result_structured` for the structured channel.
    """
    parts: list[str] = []
    for block in result.get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "\n".join(parts)


def result_structured(result: dict) -> object | None:
    """The upstream-advertised structured output, if any (never synthesized).

    Returns ``structuredContent`` when the tool provides it; otherwise ``None``.
    We never guess a JSON shape -- structure appears only when the upstream
    itself emits it.
    """
    sc = result.get("structuredContent")
    return sc if sc is not None else None


def result_is_error(result: dict) -> bool:
    """Whether the tool reported a semantic failure (``isError: true``)."""
    return bool(result.get("isError"))

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
from . import protocol as proto
from .auth import build_injector
from .config import BridgeConfig, ToolFilter
from .decorators._catalog import fetch_all_tools
from .decorators.base import BridgeContext
from .pipeline import UpstreamClient
from .transports import Transport, build_transport

log = logging.getLogger("agent-mcp.client")

# The client identity stamped on the legacy ``initialize`` and on every modern
# request's ``_meta`` (``io.modelcontextprotocol/clientInfo``).
CLIENT_INFO = {"name": "agent-mcp", "version": __version__}

# Legacy handshake revision, retained as a module constant for backward
# compatibility. Era selection now lives in :mod:`agent_mcp.protocol`: a
# one-shot negotiates modern (per-request ``_meta``) vs. legacy (``initialize``)
# based on the bridge's ``server.protocol`` and a ``server/discover`` probe.
PROTOCOL_VERSION = proto.LEGACY


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
        # Negotiated era: ``_modern`` True once we've settled on a per-request
        # metadata revision; ``_protocol_version`` is the concrete revision we
        # then stamp on / speak.
        self._modern: bool = False
        self._protocol_version: str = proto.LEGACY

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
            await self._negotiate()
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

    async def _negotiate(self) -> None:
        """Settle the upstream's protocol era before any list/call.

        ``server.protocol`` forces an era when set (``modern``/``legacy`` or an
        explicit revision); otherwise (``auto``) we probe with
        ``server/discover`` and fall back to the legacy ``initialize`` handshake
        on any non-modern outcome, per the spec's stdio/HTTP fallback rules.
        """
        forced = self.cfg.server.forced_version()
        if forced is not None:
            if proto.is_modern(forced):
                self._enter_modern(forced)
            else:
                await self._legacy_initialize(forced)
            return
        await self._auto_negotiate()

    async def _auto_negotiate(self) -> None:
        """Probe with ``server/discover``; fall back to ``initialize`` on legacy.

        Three outcomes (spec): a ``DiscoverResult`` -> modern; a recognized
        ``UnsupportedProtocolVersionError`` -> modern (pick a version it lists);
        any other error **or a timeout** -> legacy, fall back to the handshake.
        The fallback must not be keyed to one error code.
        """
        req = {
            "jsonrpc": "2.0",
            "id": self._need_client().new_id(),
            "method": "server/discover",
            "params": {"_meta": proto.client_meta(proto.MODERN, CLIENT_INFO)},
        }
        try:
            resp = await self._request(req)
        except UpstreamError:
            # No response within the timeout -> treat as a legacy server.
            await self._legacy_initialize(proto.LEGACY)
            return

        if isinstance(resp, dict) and self._is_discover_result(resp.get("result")):
            result = resp["result"]
            version = self._pick_version(result.get("supportedVersions"))
            meta = result.get("_meta") or {}
            info = meta.get(proto.META_SERVER_INFO)
            if isinstance(info, dict):
                self._server_info = info
            self._enter_modern(version)
            return

        if isinstance(resp, dict) and proto.is_unsupported_version_error(resp):
            data = (resp.get("error") or {}).get("data") or {}
            version = self._pick_version(data.get("supported"))
            self._enter_modern(version)
            return

        # Any other outcome -- a JSON-RPC error (e.g. a legacy HTTP server's 400
        # rejecting the modern MCP-Protocol-Version header, or a 404/parse error),
        # or an empty/ill-formed result a legacy stdio server returns for the
        # unknown ``server/discover`` method -- means the server is legacy. Fall
        # back to the ``initialize`` handshake. This is an expected negotiation
        # outcome, not an error (the transport logs the probe rejection at debug).
        log.debug("server/discover did not yield a DiscoverResult -- "
                  "falling back to the legacy initialize handshake")
        await self._legacy_initialize(proto.LEGACY)

    @staticmethod
    def _is_discover_result(result: object) -> bool:
        """Whether a ``server/discover`` result is a well-formed ``DiscoverResult``.

        Requires the ``supportedVersions`` list: a legacy server that answers the
        unknown method with an empty ``{}`` result must **not** be mistaken for a
        modern one (that would skip the handshake and lose ``serverInfo``).
        """
        return isinstance(result, dict) and isinstance(
            result.get("supportedVersions"), list)

    def _pick_version(self, supported: object) -> str:
        """Choose a mutually supported version from a server's advertised list.

        Prefers our own newest-first :data:`SUPPORTED_VERSIONS` order; falls back
        to the server's first entry, then to :data:`MODERN` when nothing overlaps
        (we probed modern, so proceed optimistically).
        """
        server_versions = [v for v in supported if isinstance(v, str)] \
            if isinstance(supported, list) else []
        for ours in proto.SUPPORTED_VERSIONS:
            if ours in server_versions:
                return ours
        return server_versions[0] if server_versions else proto.MODERN

    def _enter_modern(self, version: str) -> None:
        """Record that the upstream speaks modern ``version`` (no handshake)."""
        self._modern = True
        self._protocol_version = version
        log.debug("upstream negotiated modern protocol %s", version)

    async def _legacy_initialize(self, version: str) -> None:
        """Run the legacy ``initialize`` / ``notifications/initialized`` handshake."""
        self._modern = False
        self._protocol_version = version
        client = self._need_client()
        init_req = {
            "jsonrpc": "2.0",
            "id": client.new_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": version,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
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

    def _prepare(self, msg: dict) -> dict:
        """Stamp modern ``_meta`` on an outgoing request when the era is modern.

        A no-op in legacy mode, so the same list/call code path serves both eras.
        """
        if self._modern:
            proto.inject_client_meta(msg, self._protocol_version, CLIENT_INFO)
        return msg

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
        return await self._request(self._prepare(msg))

    @property
    def server_info(self) -> dict:
        """The upstream's ``serverInfo`` from initialize/discover (may be empty)."""
        return self._server_info

    @property
    def protocol_version(self) -> str:
        """The negotiated upstream protocol revision."""
        return self._protocol_version

    @property
    def is_modern(self) -> bool:
        """Whether the upstream negotiated a modern (per-request metadata) era."""
        return self._modern

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
        resp = await self._request(self._prepare(req))
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

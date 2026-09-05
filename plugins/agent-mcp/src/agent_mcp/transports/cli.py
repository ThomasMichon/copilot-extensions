"""The ``cli`` transport: a local CLI->MCP responder (no upstream MCP).

Where :class:`~agent_mcp.transports.http.HttpTransport` and
:class:`~agent_mcp.transports.stdio.StdioTransport` forward JSON-RPC to a real
upstream MCP server, this transport **answers the protocol itself** from a set of
tool sidecars (:mod:`agent_mcp.cli_tools`). ``tools/list`` is synthesized from the
sidecars' ``inputSchema``; ``tools/call`` binds the arguments to an argv and
spawns the native CLI as a subprocess, shaping stdout/stderr/exit-code into an MCP
result. There is no network and no dependency-resolving server launch -- the whole
point is to give an MCP-only consumer a native CLI without a per-tool MCP server.

It plugs into the same :class:`~agent_mcp.transports.base.Transport` seam as the
proxying transports, so the bridge's stdio framing, decorator pipeline, and legacy
``tools:`` allow/deny filter all apply unchanged.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from .. import __version__
from .. import protocol as proto
from .._exec import no_window_creationflags, resolve_spawn
from ..auth.base import AuthInjector
from ..cli_tools import CliTool, CliToolError, build_argv, load_cli_tools, tool_in_scope
from ..config import BridgeConfig
from .base import Transport

log = logging.getLogger("agent-mcp.transport.cli")

# The tool capability this responder advertises (both eras). ``listChanged`` is
# False: the sidecar set is fixed for the life of the bridge.
_CAPABILITIES = {"tools": {"listChanged": False}}

# JSON-RPC error codes we return for local failures.
_METHOD_NOT_FOUND = proto.METHOD_NOT_FOUND
_INTERNAL_ERROR = proto.INTERNAL_ERROR


def _resolve_sidecar_command(tool: CliTool, argv: list[str]) -> list[str]:
    """Resolve path-qualified commands relative to the declaring sidecar."""
    if not argv or tool.source is None:
        return argv
    command = Path(argv[0])
    if command.is_absolute() or not command.parent.parts:
        return argv
    resolved = list(argv)
    resolved[0] = str((tool.source.parent / command).resolve())
    return resolved


def _result(request: dict, result: object) -> dict:
    """A JSON-RPC success response echoing ``request``'s id (inlined to avoid a
    circular import with :mod:`agent_mcp.pipeline`, which imports this package)."""
    return {"jsonrpc": "2.0", "id": request.get("id"), "result": result}


def _error(request: dict, message: str, code: int = _INTERNAL_ERROR) -> dict:
    """A JSON-RPC error response echoing ``request``'s id."""
    return {"jsonrpc": "2.0", "id": request.get("id"),
            "error": {"code": code, "message": message}}


class CliTransport(Transport):
    """Answer MCP requests locally from tool sidecars + subprocess execution."""

    def __init__(self, cfg: BridgeConfig, injector: AuthInjector) -> None:
        super().__init__(cfg, injector)
        base_dir = cfg.source_path.parent if cfg.source_path else None
        all_tools = load_cli_tools(cfg.server.tools_from, base_dir=base_dir)
        # Scope gate: an out-of-scope tool is neither advertised nor runnable.
        self._tools: dict[str, CliTool] = {
            t.name: t for t in all_tools if tool_in_scope(t, cfg.server.scopes)
        }
        dropped = [t.name for t in all_tools if t.name not in self._tools]
        if dropped:
            log.info("cli transport: %d tool(s) out of scope %s: %s",
                     len(dropped), cfg.server.scopes, ", ".join(dropped))
        self._base_env: dict[str, str] | None = None

    async def _child_env(self) -> dict[str, str]:
        """Environment for spawned tools: ``os.environ`` + ``server.env``
        (static, computed once and cached) plus a freshly-applied auth
        injection on **every** call.

        This lets a ``cli`` bridge **self-source** a credential in the bridge's
        own process -- where ``vault``/``gh``/``az`` helpers run in a clean
        context -- and hand it to the tool via its environment, instead of
        depending on the credential already being present in the session env.
        The injector owns its own token caching/TTL/``invalidate`` semantics
        (see :class:`~agent_mcp.auth.base.TokenInjector`), so re-invoking
        ``child_env()`` per call is cheap on the happy path and self-heals a
        transient failure. Caching the *injection result* here instead (as a
        prior version did) would let one transient auth failure -- e.g. a cold
        vault -- permanently poison every subsequent spawn for the life of a
        long-lived transport (notably ``agent-mcp serve``'s warm-pooled
        sessions, which can outlive a single failure by hours). A failure to
        acquire is still non-fatal: fall back to the ambient environment so a
        tool that can source its own credential still runs.
        """
        if self._base_env is None:
            env = dict(os.environ)
            env.update(self.cfg.server.env)
            self._base_env = env
        env = dict(self._base_env)
        try:
            env.update(await self.injector.child_env())
        except Exception as exc:
            log.warning("cli transport: auth injection failed (%s); "
                        "spawning with ambient environment", exc)
        return env

    async def send(self, msg: dict) -> None:
        method = msg.get("method")
        # Notifications (no id) need no reply; ``notifications/initialized`` etc.
        if msg.get("id") is None or "method" not in msg:
            return
        try:
            resp = await self._respond(method, msg)
        except Exception as exc:  # never let one call wedge the pipe
            log.error("cli transport error on %s: %s", method, exc)
            resp = _error(msg, f"cli transport error: {exc}", _INTERNAL_ERROR)
        await self._emit_message(resp)

    def _supported_versions(self) -> tuple[str, ...]:
        """The protocol revisions this responder advertises.

        ``auto`` exposes both eras (dual-era); a forced ``server.protocol``
        narrows the responder to just that era/revision so the adapter can be
        pinned (e.g. ``modern`` to advertise only ``2026-07-28``).
        """
        if self.cfg.server.protocol_is_auto:
            return proto.SUPPORTED_VERSIONS
        forced = self.cfg.server.forced_version()
        return (forced,) if forced else proto.SUPPORTED_VERSIONS

    def _server_info(self) -> dict:
        return {"name": f"agent-mcp-cli:{self.cfg.name or 'bridge'}",
                "version": __version__}

    async def _respond(self, method: str, msg: dict) -> dict:
        supported = self._supported_versions()

        # ``server/discover`` is the modern probe: always answer it (even for an
        # unsupported requested version) so the client can learn what we speak.
        if method == "server/discover":
            return _result(msg, proto.discover_result(
                self._server_info(), _CAPABILITIES, supported=supported))

        # Modern requests self-describe their version in ``_meta``. Reject one we
        # don't support with the standard error carrying our supported list.
        requested = proto.request_protocol_version(msg)
        if requested is not None and requested not in supported:
            return proto.unsupported_version_error(msg, requested, supported)

        if method == "initialize":
            # Legacy handshake: echo the negotiated version, or fall back to our
            # newest supported one when the client's request isn't representable.
            params = msg.get("params") or {}
            asked = params.get("protocolVersion") if isinstance(params, dict) else None
            negotiated = proto.negotiate(asked, supported) or supported[0]
            return _result(msg, {
                "protocolVersion": negotiated,
                "capabilities": _CAPABILITIES,
                "serverInfo": self._server_info(),
            })
        if method == "ping":
            return _result(msg, {})
        if method == "tools/list":
            tools = [t.mcp_dict() for t in self._tools.values()]
            # Cacheable list result: the sidecar catalog is host-local and fixed
            # for the bridge's lifetime, so advertise a short public cache hint.
            return _result(msg, {
                "tools": tools,
                "ttlMs": proto.DEFAULT_TTL_MS,
                "cacheScope": proto.DEFAULT_CACHE_SCOPE,
            })
        if method == "tools/call":
            return await self._call(msg)
        return _error(msg, f"method not found: {method}", _METHOD_NOT_FOUND)

    async def _call(self, msg: dict) -> dict:
        params = msg.get("params") or {}
        name = params.get("name")
        arguments = params.get("arguments") or {}
        tool = self._tools.get(name)
        if tool is None:
            # Unknown, or gated out of scope -> a tool-level error, not protocol.
            return _result(msg, _error_content(
                f"unknown tool: {name!r} (not advertised on this host)"))
        try:
            argv = build_argv(tool, arguments)
            argv = resolve_spawn(_resolve_sidecar_command(tool, argv))
        except CliToolError as exc:
            return _result(msg, _error_content(f"invalid arguments: {exc}"))

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=await self._child_env(),
                creationflags=no_window_creationflags(),  # Windows: no console window
            )
            out_b, err_b = await proc.communicate()
            rc = proc.returncode
        except FileNotFoundError:
            return _result(msg, _error_content(
                f"command not found: {argv[0]!r}"))
        except Exception as exc:
            return _result(msg, _error_content(f"spawn failed: {exc}"))

        stdout = out_b.decode("utf-8", "replace")
        stderr = err_b.decode("utf-8", "replace")
        if rc == 0:
            return _result(msg, {
                "content": [{"type": "text", "text": stdout}],
                "isError": False,
            })
        tail = stderr.strip() or stdout.strip() or f"exited with code {rc}"
        return _result(msg, _error_content(
            f"`{tool.command}` exited {rc}: {tail[-2000:]}"))


def _error_content(message: str) -> dict:
    """An MCP tool result marked as a semantic error (``isError: true``)."""
    return {"content": [{"type": "text", "text": message}], "isError": True}

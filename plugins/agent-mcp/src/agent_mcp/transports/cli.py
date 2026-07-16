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

from ..auth.base import AuthInjector
from ..cli_tools import CliTool, CliToolError, build_argv, load_cli_tools, tool_in_scope
from ..config import BridgeConfig
from .._exec import resolve_spawn
from .base import Transport

log = logging.getLogger("agent-mcp.transport.cli")

PROTOCOL_VERSION = "2025-06-18"

# JSON-RPC error codes we return for local failures.
_METHOD_NOT_FOUND = -32601
_INTERNAL_ERROR = -32603


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

    async def _respond(self, method: str, msg: dict) -> dict:
        if method == "initialize":
            return _result(msg, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": f"agent-mcp-cli:{self.cfg.name or 'bridge'}",
                               "version": PROTOCOL_VERSION},
            })
        if method == "ping":
            return _result(msg, {})
        if method == "tools/list":
            tools = [t.mcp_dict() for t in self._tools.values()]
            return _result(msg, {"tools": tools})
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
            argv = resolve_spawn(build_argv(tool, arguments))
        except CliToolError as exc:
            return _result(msg, _error_content(f"invalid arguments: {exc}"))

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
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

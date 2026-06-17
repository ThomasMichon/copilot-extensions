"""Bridge core: a local stdio MCP server that proxies to one upstream.

Reads line-delimited JSON-RPC on stdin, forwards each message to the configured
upstream transport, and writes upstream messages back to stdout. Optionally
filters the upstream ``tools/list`` result by an allow/deny list. Transport and
auth specifics live in :mod:`agent_mcp.transports` and :mod:`agent_mcp.auth`.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import sys
import threading

from .auth import build_injector
from .config import BridgeConfig, ToolFilter
from .transports import build_transport

log = logging.getLogger("agent-mcp.bridge")


def _matches(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


def apply_tool_filter(msg: dict, tools: ToolFilter) -> dict:
    """Filter a ``tools/list`` result in place-ish by allow/deny patterns.

    Non-``tools/list`` messages pass through untouched. Patterns are
    shell-style (``repo_*``). ``deny`` wins over ``allow``.
    """
    if not tools.active:
        return msg
    result = msg.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
        return msg

    kept = []
    for tool in result["tools"]:
        name = tool.get("name", "") if isinstance(tool, dict) else ""
        if tools.deny and _matches(name, tools.deny):
            continue
        if tools.allow and not _matches(name, tools.allow):
            continue
        kept.append(tool)
    result["tools"] = kept
    return msg


class Bridge:
    """Runs one configured bridge over the process stdio."""

    def __init__(self, cfg: BridgeConfig) -> None:
        self.cfg = cfg
        self._out_lock = threading.Lock()

    def _write(self, obj: dict) -> None:
        line = json.dumps(obj)
        with self._out_lock:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()

    async def _on_upstream(self, msg: dict) -> None:
        self._write(apply_tool_filter(msg, self.cfg.tools))

    async def run(self) -> int:
        injector = build_injector(self.cfg)
        transport = build_transport(self.cfg, injector)
        transport.on_message(self._on_upstream)
        await transport.start()

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        def _reader() -> None:
            for line in sys.stdin:
                loop.call_soon_threadsafe(queue.put_nowait, line)
            loop.call_soon_threadsafe(queue.put_nowait, None)

        threading.Thread(target=_reader, name="agent-mcp-stdin", daemon=True).start()
        log.info("bridge '%s' started (%s -> %s)", self.cfg.name, self.cfg.server.type,
                 self.cfg.server.url or " ".join(self.cfg.server.command))

        while True:
            line = await queue.get()
            if line is None:
                break
            text = line.strip()
            if not text:
                continue
            try:
                msg = json.loads(text)
            except json.JSONDecodeError:
                log.warning("invalid JSON on stdin: %s", text[:200])
                continue
            try:
                await transport.send(msg)
            except Exception as exc:
                log.error("send failed: %s", exc)

        await transport.end_input()
        await transport.aclose()
        return 0

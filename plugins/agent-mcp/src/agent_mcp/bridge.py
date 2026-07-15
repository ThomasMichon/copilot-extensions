"""Bridge core: a local stdio MCP server that proxies to one upstream.

Reads line-delimited JSON-RPC on stdin and runs each client message through a
:class:`~agent_mcp.pipeline.Pipeline` of decorators wrapping the upstream
transport. Decorators may filter, rename, defer, code-mode, or storage-relay the
traffic (see :mod:`agent_mcp.decorators`); the legacy top-level ``tools:`` filter
is applied as an implicit decorator. Transport and auth specifics live in
:mod:`agent_mcp.transports` and :mod:`agent_mcp.auth`.
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
from .decorators import BridgeContext, build_decorators
from .pipeline import Pipeline, UpstreamClient, error_response, is_request
from .transports import build_transport

log = logging.getLogger("agent-mcp.bridge")


def _matches(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


def apply_tool_filter(msg: dict, tools: ToolFilter) -> dict:
    """Filter a ``tools/list`` result in place-ish by allow/deny patterns.

    Retained for backward compatibility; the filtering logic now also lives in
    :class:`agent_mcp.decorators.filter.FilterDecorator`. Non-``tools/list``
    messages pass through untouched. Patterns are shell-style (``repo_*``).
    ``deny`` wins over ``allow``.
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

    def _emit_to_client(self, msg: dict) -> None:
        """Server->client push from a decorator (e.g. list_changed)."""
        self._write(msg)

    def _on_unsolicited(self, msg: dict) -> None:
        """Uncorrelated upstream message (server notification / late reply)."""
        self._write(msg)

    async def _dispatch(self, pipeline: Pipeline, msg: dict) -> None:
        try:
            resp = await pipeline.handle(msg)
        except Exception as exc:  # never let one request kill the loop
            log.error("pipeline error: %s", exc)
            resp = error_response(msg, f"bridge error: {exc}") if is_request(msg) else None
        if resp is not None:
            self._write(resp)

    async def run(self) -> int:
        injector = build_injector(self.cfg)
        transport = build_transport(self.cfg, injector)
        client = UpstreamClient(transport)
        client.on_unsolicited(self._on_unsolicited)
        ctx = BridgeContext(new_id=client.new_id, emit_to_client=self._emit_to_client)
        pipeline = Pipeline(build_decorators(self.cfg, ctx), client.request)

        await transport.start()

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        def _reader() -> None:
            for line in sys.stdin:
                loop.call_soon_threadsafe(queue.put_nowait, line)
            loop.call_soon_threadsafe(queue.put_nowait, None)

        threading.Thread(target=_reader, name="agent-mcp-stdin", daemon=True).start()
        log.info("bridge '%s' started (%s -> %s); %d decorator(s)", self.cfg.name,
                 self.cfg.server.type,
                 self.cfg.server.launch_desc,
                 len(pipeline.decorators))

        tasks: set[asyncio.Task] = set()
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
            task = asyncio.create_task(self._dispatch(pipeline, msg))
            tasks.add(task)
            task.add_done_callback(tasks.discard)

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await pipeline.aclose()
        client.fail_pending()
        await transport.end_input()
        await transport.aclose()
        return 0

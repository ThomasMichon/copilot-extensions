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

from .config import BridgeConfig, ToolFilter
from .session import BridgeSession
from .watchdog import install_parent_death_watchdog, reap_descendants_on_exit

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

    async def run(self) -> int:
        session = BridgeSession(self.cfg, self._write)
        await session.start()

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        def _signal_shutdown() -> None:
            # The terminal signal used by both stdin EOF and the parent-death
            # watchdog: unblock run() into the graceful teardown path below.
            try:
                loop.call_soon_threadsafe(queue.put_nowait, None)
            except RuntimeError:
                pass  # loop already closed -- nothing to unblock

        # Guard against the leaked-tree failure mode where an interposed launcher
        # (e.g. the Windows cmd shim) is terminated but this process's inherited
        # stdin never sees EOF: reap descendants on exit and shut down when the
        # launch parent goes away. See agent_mcp.watchdog.
        reap_descendants_on_exit()
        install_parent_death_watchdog(_signal_shutdown)

        def _reader() -> None:
            # The watchdog can end run() (and close the loop) while this daemon
            # thread is still blocked on stdin; guard the wakeups so a late read
            # doesn't raise "Event loop is closed" during interpreter shutdown.
            try:
                for line in sys.stdin:
                    loop.call_soon_threadsafe(queue.put_nowait, line)
                loop.call_soon_threadsafe(queue.put_nowait, None)
            except RuntimeError:
                pass  # loop already closed -- shutdown is already under way

        threading.Thread(target=_reader, name="agent-mcp-stdin", daemon=True).start()
        log.info("bridge '%s' started (%s -> %s); %d decorator(s)", self.cfg.name,
                 self.cfg.server.type,
                 self.cfg.server.launch_desc,
                 session.decorator_count)

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
            session.submit(msg)

        await session.aclose()
        return 0

"""stdio upstream transport -- wrap a child-process MCP server.

Spawns the configured ``server.command`` as a child MCP server, injects auth via
the child environment (``server.env`` plus the :class:`AuthInjector`'s
``child_env``), and pumps JSON-RPC line-delimited messages in both directions.

This is the "bridge wrapper around another MCP" case: e.g. wrap a third-party
``npx`` MCP and feed it a host-acquired token by env var.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

from .._exec import resolve_argv
from .base import Transport

log = logging.getLogger("agent-mcp.stdio")


class StdioTransport(Transport):
    """Wrap a child-process MCP server over its stdio."""

    def __init__(self, cfg, injector) -> None:
        super().__init__(cfg, injector)
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None

    async def start(self) -> None:
        env = dict(os.environ)
        env.update(self.cfg.server.env)
        env.update(await self.injector.child_env())

        argv = self.cfg.server.command
        if not argv:
            raise ValueError("stdio transport requires server.command")
        # Resolve argv[0] so .cmd/.bat shims (e.g. npx.cmd) spawn on Windows --
        # create_subprocess_exec only auto-appends .exe, not PATHEXT.
        argv = resolve_argv(argv)
        log.info("spawning upstream MCP: %s", " ".join(argv))
        self._proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=None,  # inherit -- child diagnostics go to our stderr
            env=env,
        )
        self._reader_task = asyncio.create_task(self._pump_stdout())

    async def _pump_stdout(self) -> None:
        if self._proc is None or self._proc.stdout is None:
            return
        while True:
            line = await self._proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                obj = json.loads(text)
            except json.JSONDecodeError:
                log.warning("non-JSON line from upstream child: %s", text[:200])
                continue
            await self._emit_message(obj)

    async def send(self, msg: dict) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise RuntimeError("stdio transport not started")
        data = (json.dumps(msg) + "\n").encode("utf-8")
        self._proc.stdin.write(data)
        await self._proc.stdin.drain()

    async def end_input(self) -> None:
        # Propagate client stdin EOF to the child so it can finish, then let any
        # buffered server output drain via the reader task before we close.
        if self._proc and self._proc.stdin and not self._proc.stdin.is_closing():
            self._proc.stdin.close()
        if self._reader_task:
            try:
                await asyncio.wait_for(asyncio.shield(self._reader_task), timeout=5.0)
            except (TimeoutError, asyncio.TimeoutError):
                pass

    async def aclose(self) -> None:
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
        if self._proc and self._proc.returncode is None:
            try:
                self._proc.terminate()
            except ProcessLookupError:
                pass

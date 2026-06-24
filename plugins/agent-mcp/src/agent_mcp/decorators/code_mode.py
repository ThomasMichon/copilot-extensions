"""``code-mode`` decorator -- expose a typed ``run_code`` tool over the catalog.

Instead of N tool definitions, the client sees a single ``run_code`` tool whose
description carries a generated TypeScript ``Tools`` interface for the whole
catalog. The model writes a short JS/TS snippet that calls tools as async methods
(``await tools.someTool({...})``) and chains/aggregates results in one round-trip
-- the "code mode" pattern. The snippet runs in a Node child; each ``tools.X``
call is relayed back to the upstream MCP through the decorator chain.

A companion ``code_apis`` tool returns the full interface text on demand (for
clients that truncate long tool descriptions).
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import shutil
from pathlib import Path

from .._exec import resolve_argv
from ._catalog import fetch_all_tools, render_tools_interface, tool_call_args, tool_call_name
from .base import BridgeContext, Decorator, Next, error_response, result_response

log = logging.getLogger("agent-mcp.code-mode")

_HARNESS = Path(__file__).with_name("harness.js")


class CodeModeDecorator(Decorator):
    type = "code-mode"

    def __init__(self, options: dict, ctx: BridgeContext) -> None:
        super().__init__(options, ctx)
        self.tool_name = str(options.get("tool", "run_code"))
        self.apis_name = str(options.get("apis_tool", "code_apis"))
        self.find_name = str(options.get("find_tool", "find_tool"))
        self.runtime = str(options.get("runtime", "node"))
        self.timeout = float(options.get("timeout", 30.0))
        self.interface = str(options.get("interface", "Tools"))
        self.expose = [str(p) for p in (options.get("expose") or [])]
        # Above this catalog size, run_code's description points at find_tool
        # instead of embedding the (huge) full interface -- "find feeds code-mode".
        self.interface_limit = int(options.get("interface_limit", 40))
        self.max_results = int(options.get("max_results", 20))
        self._catalog: list[dict] = []

    # -- middleware --------------------------------------------------------

    async def handle(self, request: dict, nxt: Next) -> dict | None:
        method = request.get("method")
        name = tool_call_name(request)

        if name == self.tool_name:
            return await self._handle_run(request, nxt)
        if name == self.find_name:
            return await self._handle_find(request, nxt)
        if name == self.apis_name:
            await self._ensure_catalog(nxt)
            return result_response(request, {
                "content": [{"type": "text", "text": self._interface_text()}],
                "isError": False,
            })

        resp = await nxt(request)
        if method == "tools/list":
            await self._capture(resp, nxt)
            self._rewrite_list(resp)
        return resp

    # -- catalog -----------------------------------------------------------

    async def _capture(self, resp: dict | None, nxt: Next) -> None:
        if not isinstance(resp, dict):
            return
        result = resp.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
            return
        if result.get("nextCursor"):
            # Paginated upstream: the pass-through response is only page 1.
            # Fetch the complete catalog so find/execute/code-mode see every tool.
            self._catalog = await fetch_all_tools(nxt, ctx=self.ctx)
        else:
            self._catalog = [t for t in result["tools"] if isinstance(t, dict)]

    async def _ensure_catalog(self, nxt: Next) -> None:
        if not self._catalog:
            self._catalog = await fetch_all_tools(nxt, ctx=self.ctx)

    def _is_exposed(self, name: str) -> bool:
        return any(fnmatch.fnmatchcase(name, p) for p in self.expose)

    def _interface_text(self, tools: list[dict] | None = None) -> str:
        return render_tools_interface(tools if tools is not None else self._catalog,
                                      interface=self.interface)

    def _search(self, query: str) -> list[dict]:
        q = query.strip().lower()
        if not q:
            return list(self._catalog)
        return [t for t in self._catalog
                if q in f"{t.get('name', '')}\n{t.get('description', '')}".lower()]

    def _rewrite_list(self, resp: dict | None) -> None:
        if not isinstance(resp, dict):
            return
        result = resp.get("result")
        if not isinstance(result, dict):
            return
        exposed = [t for t in self._catalog if self._is_exposed(t.get("name", ""))]
        result["tools"] = [*exposed, self._run_tool(), self._find_tool(), self._apis_tool()]
        result.pop("nextCursor", None)

    def _run_tool(self) -> dict:
        desc = (
            "Execute a JavaScript/TypeScript snippet that calls upstream tools as "
            "async methods and returns a value. The snippet is an async function "
            "body; `return` your result. Each tool is available as "
            "`await tools.<name>(args)` (a lone JSON text result is auto-parsed). "
            "Chain/aggregate multiple calls in one snippet instead of many "
            "round-trips."
        )
        if len(self._catalog) <= self.interface_limit:
            desc += ("\n\nAvailable API:\n```typescript\n" + self._interface_text()
                     + "\n\ndeclare const tools: " + self.interface + ";\n```")
        else:
            desc += (f"\n\nThis server has {len(self._catalog)} tools — too many to "
                     f"list here. Call `{self.find_name}(query)` first to get the "
                     f"typed TypeScript signatures for the tools you need, then use "
                     f"them in your snippet.")
        return {
            "name": self.tool_name,
            "description": desc,
            "inputSchema": {
                "type": "object",
                "properties": {
                    "code": {"type": "string",
                             "description": "Async function body; use "
                                            "`await tools.X(args)`; `return` a value."},
                },
                "required": ["code"],
            },
        }

    def _find_tool(self) -> dict:
        return {
            "name": self.find_name,
            "description": (
                "Search the tool catalog and return TypeScript signatures for the "
                f"matching tools, ready to call from {self.tool_name} as "
                "`await tools.<name>(args)`."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string",
                              "description": "Free-text search over names + descriptions."},
                    "limit": {"type": "integer",
                              "description": f"Max matches (default {self.max_results})."},
                },
            },
        }

    async def _handle_find(self, request: dict, nxt: Next) -> dict:
        await self._ensure_catalog(nxt)
        args = tool_call_args(request)
        limit = int(args.get("limit", self.max_results) or self.max_results)
        matches = self._search(str(args.get("query", "")))[:limit]
        if not matches:
            return result_response(request, {
                "content": [{"type": "text", "text": "no tools match"}], "isError": False})
        text = (f"{len(matches)} tool(s) — call via {self.tool_name} as "
                f"await tools.<name>(args):\n```typescript\n"
                + self._interface_text(matches) + "\n```")
        return result_response(request, {
            "content": [{"type": "text", "text": text}], "isError": False})

    def _apis_tool(self) -> dict:
        return {
            "name": self.apis_name,
            "description": "Return the full TypeScript Tools interface for use with "
                           + self.tool_name + ".",
            "inputSchema": {"type": "object", "properties": {}},
        }

    # -- run_code ----------------------------------------------------------

    async def _handle_run(self, request: dict, nxt: Next) -> dict:
        if shutil.which(self.runtime) is None and not Path(self.runtime).exists():
            return error_response(
                request, f"code-mode runtime '{self.runtime}' not found on PATH")
        await self._ensure_catalog(nxt)
        code = tool_call_args(request).get("code")
        if not isinstance(code, str) or not code.strip():
            return error_response(request, "'code' (a JS/TS snippet) is required",
                                  code=-32602)
        try:
            outcome = await asyncio.wait_for(self._run_node(code, nxt), timeout=self.timeout)
        except (TimeoutError, asyncio.TimeoutError):
            return result_response(request, {
                "content": [{"type": "text", "text": f"code timed out after {self.timeout}s"}],
                "isError": True,
            })
        return result_response(request, outcome)

    async def _run_node(self, code: str, nxt: Next) -> dict:
        argv = resolve_argv([self.runtime, str(_HARNESS)])
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            names = [t.get("name") for t in self._catalog if t.get("name")]
            await self._write(proc, {"t": "start", "code": code, "toolNames": names})

            done: dict | None = None
            if proc.stdout is None:
                return self._format_outcome(None)
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode("utf-8", errors="replace").strip())
                except json.JSONDecodeError:
                    continue
                kind = msg.get("t")
                if kind == "call":
                    await self._service_call(proc, msg, nxt)
                elif kind == "done":
                    done = msg
                    break
            return self._format_outcome(done)
        finally:
            # Always reap the child -- including the wait_for timeout / cancel
            # path, where this runs during CancelledError propagation.
            await self._terminate(proc)

    @staticmethod
    async def _terminate(proc) -> None:
        if proc.stdin is not None:
            try:
                proc.stdin.close()
            except (OSError, ValueError) as exc:
                log.debug("closing node stdin: %s", exc)
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        try:
            await proc.wait()
        except ProcessLookupError:
            pass

    async def _service_call(self, proc, msg: dict, nxt: Next) -> None:
        sub = {"jsonrpc": "2.0", "id": self.ctx.new_id(), "method": "tools/call",
               "params": {"name": msg.get("tool"), "arguments": msg.get("args") or {}}}
        resp = await nxt(sub)
        reply = {"t": "call_result", "id": msg.get("id")}
        if isinstance(resp, dict) and "result" in resp:
            reply["ok"] = True
            reply["result"] = resp["result"]
        else:
            reply["ok"] = False
            err = (resp or {}).get("error") if isinstance(resp, dict) else None
            reply["error"] = (err or {}).get("message", "tool call failed") \
                if isinstance(err, dict) else "tool call failed"
        await self._write(proc, reply)

    @staticmethod
    async def _write(proc, obj: dict) -> None:
        if proc.stdin is None:
            return
        proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
        await proc.stdin.drain()

    @staticmethod
    def _format_outcome(done: dict | None) -> dict:
        if not done:
            return {"content": [{"type": "text", "text": "code produced no result"}],
                    "isError": True}
        logs = done.get("logs") or []
        log_text = ("\n".join(str(line_) for line_ in logs)).strip()
        if done.get("ok"):
            value = done.get("result")
            value_text = value if isinstance(value, str) else json.dumps(value, indent=2)
            text = value_text if not log_text else f"{log_text}\n---\n{value_text}"
            return {"content": [{"type": "text", "text": text}], "isError": False}
        err = done.get("error", "code failed")
        text = err if not log_text else f"{log_text}\n---\n{err}"
        return {"content": [{"type": "text", "text": text}], "isError": True}

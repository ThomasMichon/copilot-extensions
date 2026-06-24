"""``defer`` decorator -- hide a large tool catalog behind meta-tools.

Modelled on the UniFi MCP meta-tool pattern. Instead of exposing 100+ tools in
``tools/list`` (which floods a model's context), the bridge exposes a small set
of meta-tools and keeps the real catalog searchable:

* ``find_tool``    -- search the full catalog by query/category; returns compact
  name + description entries (optionally input schemas).
* ``execute_tool`` -- invoke any catalog tool by name with arguments.
* ``load_tools``   -- (lazy mode) promote named tools into ``tools/list`` and emit
  ``notifications/tools/list_changed`` so capable clients can call them directly.

Modes (``mode:``):
* ``lazy`` (default) -- ``tools/list`` shows exposed tools + loaded tools + the
  meta-tools (incl. ``load_tools``).
* ``eager`` -- the full catalog *and* the meta-tools are listed.
* ``meta_only`` -- only exposed tools + ``find_tool``/``execute_tool`` are listed.
"""

from __future__ import annotations

import fnmatch
import json

from ._catalog import fetch_all_tools, tool_call_args, tool_call_name
from .base import BridgeContext, Decorator, Next, error_response, result_response

_MODES = ("lazy", "eager", "meta_only")


class DeferDecorator(Decorator):
    type = "defer"

    def __init__(self, options: dict, ctx: BridgeContext) -> None:
        super().__init__(options, ctx)
        self.mode = str(options.get("mode", "lazy"))
        if self.mode not in _MODES:
            self.mode = "lazy"
        self.expose = [str(p) for p in (options.get("expose") or [])]
        self.find_name = str(options.get("find_tool", "find_tool"))
        self.execute_name = str(options.get("execute_tool", "execute_tool"))
        self.load_name = str(options.get("load_tool", "load_tools"))
        self.max_results = int(options.get("max_results", 20))
        self._catalog: list[dict] = []
        self._loaded: set[str] = set()

    # -- middleware --------------------------------------------------------

    async def handle(self, request: dict, nxt: Next) -> dict | None:
        method = request.get("method")
        name = tool_call_name(request)

        if name == self.find_name:
            return await self._handle_find(request, nxt)
        if name == self.execute_name:
            return await self._handle_execute(request, nxt)
        if name == self.load_name and self.mode == "lazy":
            return await self._handle_load(request, nxt)

        resp = await nxt(request)
        if method == "tools/list":
            await self._capture(resp, nxt)
            self._rewrite_list(resp)
        return resp

    # -- catalog management ------------------------------------------------

    async def _capture(self, resp: dict | None, nxt: Next) -> None:
        if not isinstance(resp, dict):
            return
        result = resp.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
            return
        if result.get("nextCursor"):
            # Paginated upstream: the pass-through response is only page 1.
            # Fetch the complete catalog so find_tool/execute_tool cover every tool.
            self._catalog = await fetch_all_tools(nxt, ctx=self.ctx)
        else:
            self._catalog = [t for t in result["tools"] if isinstance(t, dict)]

    async def _ensure_catalog(self, nxt: Next) -> None:
        if not self._catalog:
            self._catalog = await fetch_all_tools(nxt, ctx=self.ctx)

    def _is_exposed(self, name: str) -> bool:
        return any(fnmatch.fnmatchcase(name, p) for p in self.expose)

    def _rewrite_list(self, resp: dict | None) -> None:
        if not isinstance(resp, dict):
            return
        result = resp.get("result")
        if not isinstance(result, dict):
            return
        if self.mode == "eager":
            visible = list(self._catalog)
        else:
            visible = [t for t in self._catalog
                       if self._is_exposed(t.get("name", ""))
                       or t.get("name") in self._loaded]
        result["tools"] = visible + self._meta_tools()
        result.pop("nextCursor", None)

    def _meta_tools(self) -> list[dict]:
        tools = [
            {
                "name": self.find_name,
                "description": (
                    "Search the full tool catalog by free-text query and/or "
                    "category. Returns compact {name, description} entries; set "
                    "include_schemas=true to include input schemas. Use this to "
                    "discover a tool, then call it with " + self.execute_name + "."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string",
                                  "description": "Free-text search over names + descriptions."},
                        "category": {"type": "string",
                                     "description": "Restrict to a name prefix / category."},
                        "limit": {"type": "integer",
                                  "description": f"Max results (default {self.max_results})."},
                        "include_schemas": {"type": "boolean",
                                            "description": "Include each tool's inputSchema."},
                    },
                },
            },
            {
                "name": self.execute_name,
                "description": (
                    "Invoke any catalog tool by name. Provide 'tool' (the name "
                    "from " + self.find_name + ") and 'arguments'."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "tool": {"type": "string", "description": "Catalog tool name."},
                        "arguments": {"type": "object", "description": "Tool arguments."},
                    },
                    "required": ["tool"],
                },
            },
        ]
        if self.mode == "lazy":
            tools.append({
                "name": self.load_name,
                "description": (
                    "Promote named catalog tools into tools/list so they can be "
                    "called directly; emits notifications/tools/list_changed."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "tools": {"type": "array", "items": {"type": "string"},
                                  "description": "Tool names to load."},
                    },
                    "required": ["tools"],
                },
            })
        return tools

    # -- meta-tool handlers ------------------------------------------------

    async def _handle_find(self, request: dict, nxt: Next) -> dict:
        await self._ensure_catalog(nxt)
        args = tool_call_args(request)
        query = str(args.get("query", "")).strip().lower()
        category = str(args.get("category", "")).strip().lower()
        limit = int(args.get("limit", self.max_results) or self.max_results)
        include_schemas = bool(args.get("include_schemas", False))

        matches = []
        for tool in self._catalog:
            name = tool.get("name", "")
            desc = tool.get("description", "") or ""
            hay = f"{name}\n{desc}".lower()
            if query and query not in hay:
                continue
            if category and category not in name.lower():
                continue
            entry = {"name": name, "description": desc}
            if include_schemas and isinstance(tool.get("inputSchema"), dict):
                entry["inputSchema"] = tool["inputSchema"]
            matches.append(entry)
            if len(matches) >= limit:
                break

        header = f"{len(matches)} tool(s) match" + (f" '{query}'" if query else "")
        body = "\n".join(f"- {m['name']}: {m['description']}" for m in matches)
        payload = {"content": [{"type": "text", "text": f"{header}\n{body}".strip()},
                               {"type": "text", "text": json.dumps({"tools": matches})}],
                   "isError": False}
        return result_response(request, payload)

    async def _handle_execute(self, request: dict, nxt: Next) -> dict | None:
        args = tool_call_args(request)
        tool = args.get("tool")
        if not isinstance(tool, str) or not tool:
            return error_response(request, "'tool' (a catalog tool name) is required",
                                  code=-32602)
        sub = {"jsonrpc": "2.0", "id": self.ctx.new_id(), "method": "tools/call",
               "params": {"name": tool, "arguments": args.get("arguments") or {}}}
        resp = await nxt(sub)
        if not isinstance(resp, dict):
            return error_response(request, f"no response executing '{tool}'")
        if "error" in resp:
            return {"jsonrpc": "2.0", "id": request.get("id"), "error": resp["error"]}
        return result_response(request, resp.get("result"))

    async def _handle_load(self, request: dict, nxt: Next) -> dict:
        await self._ensure_catalog(nxt)
        args = tool_call_args(request)
        names = [str(n) for n in (args.get("tools") or []) if isinstance(n, str)]
        known = {t.get("name") for t in self._catalog}
        loaded, unknown = [], []
        for n in names:
            (loaded if n in known else unknown).append(n)
            if n in known:
                self._loaded.add(n)
        if loaded:
            await self.ctx.emit_to_client(
                {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
        text = f"loaded {len(loaded)} tool(s): {', '.join(loaded) or '(none)'}"
        if unknown:
            text += f"; unknown: {', '.join(unknown)}"
        return result_response(request, {"content": [{"type": "text", "text": text}],
                                         "isError": bool(unknown and not loaded)})

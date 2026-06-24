"""``transform`` decorator -- reshape tool results per tool.

Large, deeply-nested tool results (e.g. an ADO ``repo_list_pull_requests`` page
wrapped in ``{count, value:[...]}`` where each PR has 40 fields) waste the
model's context. ``transform`` rewrites a matching tool's result with a small set
of ops applied to its JSON document (``structuredContent`` and/or a JSON text
content block):

* ``extract: <path>`` -- replace the whole result with the value at a path
  (``value`` to unwrap an envelope).
* ``pick: [paths]``   -- keep only these dotted paths (nested shape preserved).
* ``drop: [paths]``   -- remove these dotted paths.
* ``command: [...]``  -- pipe the result JSON to a filter's stdin; its stdout
  (parsed as JSON) replaces the result. The jq-style escape hatch.

Ops apply in order extract -> pick -> drop (or ``command`` alone). Multiple rules
matching the same tool apply in sequence.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import subprocess
from typing import Any

from ._catalog import tool_call_name
from ._jsonutil import (
    MISSING,
    drop_path,
    get_path,
    json_documents,
    pick_paths,
    split_path,
)
from .base import BridgeContext, Decorator, Next

log = logging.getLogger("agent-mcp.transform")


class _TransformRule:
    __slots__ = ("command", "drop", "extract", "pick", "tool")

    def __init__(self, spec: dict) -> None:
        self.tool = str(spec.get("tool", "*"))
        self.command = [str(c) for c in spec["command"]] if spec.get("command") else None
        self.extract = split_path(spec["extract"]) if spec.get("extract") else None
        self.pick = [str(p) for p in (spec.get("pick") or [])]
        self.drop = [split_path(p) for p in (spec.get("drop") or [])]


class TransformDecorator(Decorator):
    type = "transform"

    def __init__(self, options: dict, ctx: BridgeContext) -> None:
        super().__init__(options, ctx)
        self.command_timeout = float(options.get("command_timeout", 30.0))
        # Accept either a top-level ``rules:`` list or a single inline rule.
        raw = options.get("rules")
        if raw is None and any(k in options for k in ("tool", "extract", "pick",
                                                      "drop", "command")):
            raw = [options]
        self.rules = [_TransformRule(r) for r in (raw or []) if isinstance(r, dict)]

    async def handle(self, request: dict, nxt: Next) -> dict | None:
        method = request.get("method")
        name = tool_call_name(request)
        resp = await nxt(request)
        if method == "tools/call":
            await self._transform(resp, name)
        return resp

    def _rules_for(self, tool: str | None) -> list[_TransformRule]:
        if tool is None:
            return []
        return [r for r in self.rules if fnmatch.fnmatchcase(tool, r.tool)]

    async def _transform(self, resp: dict | None, tool: str | None) -> None:
        if not isinstance(resp, dict):
            return
        result = resp.get("result")
        if not isinstance(result, dict):
            return
        rules = self._rules_for(tool)
        if not rules:
            return
        for kind, block, doc in json_documents(result):
            new_doc = doc
            for rule in rules:
                new_doc = await self._apply_rule(new_doc, rule)
            if kind == "structured":
                result["structuredContent"] = new_doc
            elif block is not None:
                block["text"] = json.dumps(new_doc)

    async def _apply_rule(self, doc: Any, rule: _TransformRule) -> Any:
        if rule.command:
            return await self._run_command(doc, rule.command)
        if rule.extract is not None:
            val = get_path(doc, rule.extract)
            if val is not MISSING:
                doc = val
        if rule.pick:
            doc = self._pick(doc, rule.pick)
        if rule.drop:
            self._drop(doc, rule.drop)
        return doc

    @staticmethod
    def _pick(doc: Any, paths: list[str]) -> Any:
        # Map element-wise over a list (e.g. a list-PRs array), else pick the object.
        if isinstance(doc, list):
            return [pick_paths(e, paths) if isinstance(e, dict) else e for e in doc]
        if isinstance(doc, dict):
            return pick_paths(doc, paths)
        return doc

    @staticmethod
    def _drop(doc: Any, drops: list[list[str]]) -> None:
        targets = doc if isinstance(doc, list) else [doc]
        for item in targets:
            if isinstance(item, dict):
                for segs in drops:
                    drop_path(item, segs)

    async def _run_command(self, doc: Any, command: list[str]) -> Any:
        try:
            proc = await asyncio.to_thread(
                subprocess.run, command, input=json.dumps(doc),
                capture_output=True, text=True, timeout=self.command_timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            log.error("transform command failed: %s", exc)
            return doc
        out = (proc.stdout or "").strip()
        if not out:
            return doc
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return out

"""``rename`` decorator -- rewrite tool names and descriptions.

Supports namespacing, prefix/suffix, and regex substitutions on tool *names*,
plus prefix/suffix/regex edits to *descriptions*. The client sees the rewritten
names; a ``tools/call`` for a rewritten name is mapped back to the real upstream
name before forwarding. Name rewrites are learned from ``tools/list`` responses;
structural rewrites (namespace/prefix/suffix) are also reversed by construction,
so a call can be routed even before the first ``tools/list``.
"""

from __future__ import annotations

import re

from ._catalog import tool_call_name
from .base import BridgeContext, Decorator, Next


class RenameDecorator(Decorator):
    type = "rename"

    def __init__(self, options: dict, ctx: BridgeContext) -> None:
        super().__init__(options, ctx)
        self.namespace = options.get("namespace")
        self.separator = str(options.get("separator", "__"))
        self.prefix = str(options.get("prefix", ""))
        self.suffix = str(options.get("suffix", ""))
        self.name_patterns = _compile_patterns(options.get("patterns"))
        desc = options.get("description") or {}
        self.desc_prefix = str(desc.get("prefix", ""))
        self.desc_suffix = str(desc.get("suffix", ""))
        self.desc_patterns = _compile_patterns(desc.get("patterns"))
        # learned rewritten-name -> original-name (populated from tools/list)
        self._reverse: dict[str, str] = {}

    # -- name transforms ---------------------------------------------------

    def _rename(self, name: str) -> str:
        out = name
        for rx, repl in self.name_patterns:
            out = rx.sub(repl, out)
        if self.prefix:
            out = self.prefix + out
        if self.suffix:
            out = out + self.suffix
        if self.namespace:
            out = f"{self.namespace}{self.separator}{out}"
        return out

    def _structural_reverse(self, name: str) -> str | None:
        """Best-effort inverse for namespace/prefix/suffix (not regex)."""
        out = name
        if self.namespace:
            head = f"{self.namespace}{self.separator}"
            if not out.startswith(head):
                return None
            out = out[len(head):]
        if self.suffix:
            if not out.endswith(self.suffix):
                return None
            out = out[: -len(self.suffix)]
        if self.prefix:
            if not out.startswith(self.prefix):
                return None
            out = out[len(self.prefix):]
        # If regex patterns are configured we cannot invert them structurally.
        return None if self.name_patterns else out

    def _rewrite_description(self, desc: str) -> str:
        out = desc
        for rx, repl in self.desc_patterns:
            out = rx.sub(repl, out)
        return f"{self.desc_prefix}{out}{self.desc_suffix}"

    # -- middleware --------------------------------------------------------

    async def handle(self, request: dict, nxt: Next) -> dict | None:
        name = tool_call_name(request)
        if name is not None:
            original = self._reverse.get(name) or self._structural_reverse(name)
            if original is None and name in self._reverse.values():
                original = name  # already an upstream name (e.g. internal sub-call)
            if original is None:
                # Unknown rewritten name and not reversible -- forward as-is and
                # let upstream reject, rather than guessing.
                original = name
            request = _with_tool_name(request, original)

        resp = await nxt(request)
        if request.get("method") == "tools/list":
            self._rewrite_list(resp)
        return resp

    def _rewrite_list(self, resp: dict | None) -> None:
        if not isinstance(resp, dict):
            return
        result = resp.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
            return
        for tool in result["tools"]:
            if not isinstance(tool, dict):
                continue
            old = tool.get("name", "")
            new = self._rename(old)
            if new != old:
                self._reverse[new] = old
                tool["name"] = new
            if "description" in tool or self.desc_prefix or self.desc_suffix \
                    or self.desc_patterns:
                tool["description"] = self._rewrite_description(tool.get("description", ""))


def _with_tool_name(request: dict, name: str) -> dict:
    params = dict(request.get("params") or {})
    params["name"] = name
    return {**request, "params": params}


def _compile_patterns(raw) -> list[tuple[re.Pattern, str]]:
    out: list[tuple[re.Pattern, str]] = []
    for item in raw or []:
        if isinstance(item, dict) and "match" in item:
            out.append((re.compile(str(item["match"])), str(item.get("replace", ""))))
    return out

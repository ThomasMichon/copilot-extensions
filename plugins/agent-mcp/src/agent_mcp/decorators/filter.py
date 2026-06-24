"""``filter`` decorator -- allow/deny which upstream tools are exposed.

Generalizes the legacy top-level ``tools:`` filter into a stack decorator. It
prunes ``tools/list`` results *and* rejects ``tools/call`` for tools that the
filter hides, so a hidden tool cannot be invoked by name even if its name leaks.
"""

from __future__ import annotations

import fnmatch

from ._catalog import tool_call_name
from .base import BridgeContext, Decorator, Next, error_response


def matches(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, p) for p in patterns)


def visible(name: str, allow: list[str], deny: list[str]) -> bool:
    """Whether ``name`` survives the allow/deny filter (deny wins)."""
    if deny and matches(name, deny):
        return False
    if allow and not matches(name, allow):
        return False
    return True


class FilterDecorator(Decorator):
    type = "filter"

    def __init__(self, options: dict, ctx: BridgeContext) -> None:
        super().__init__(options, ctx)
        self.allow = [str(p) for p in (options.get("allow") or [])]
        self.deny = [str(p) for p in (options.get("deny") or [])]

    @property
    def active(self) -> bool:
        return bool(self.allow or self.deny)

    async def handle(self, request: dict, nxt: Next) -> dict | None:
        if not self.active:
            return await nxt(request)

        name = tool_call_name(request)
        if name is not None and not visible(name, self.allow, self.deny):
            return error_response(request, f"tool '{name}' is not available", code=-32601)

        resp = await nxt(request)
        if request.get("method") == "tools/list":
            self._filter_list(resp)
        return resp

    def _filter_list(self, resp: dict | None) -> None:
        if not isinstance(resp, dict):
            return
        result = resp.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
            return
        result["tools"] = [
            t for t in result["tools"]
            if isinstance(t, dict) and visible(t.get("name", ""), self.allow, self.deny)
        ]

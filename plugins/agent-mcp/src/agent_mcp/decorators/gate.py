"""``gate`` decorator -- conditionally allow/deny a tool based on a preflight call.

Some tools can only be judged *safe to run* by a fact that lives in a **different**
upstream response, not in the gated tool's own request or result. A static
``filter``/``transform`` can't see that out-of-band signal, so it must either allow
the tool for everyone or deny it for everyone.

``gate`` closes that gap generically. When a client calls one of ``match_tools``,
the decorator first issues a **preflight** upstream call (a lookup keyed off the
gated call's own arguments), evaluates a boolean **predicate** over the preflight
result, and then:

* **allows** the real call through untouched (predicate true), or
* **denies** it -- returning a policy ``stub``, an empty ``drop`` result, or a
  JSON-RPC ``error`` (predicate false, ``on_deny``).

Nothing domain-specific lives here: ``match_tools``, the ``preflight`` lookup, the
``allow_when`` predicate, and the deny action are all config. A preflight result is
cached per resolved-key (``cache: per-key``) so gating several tools off the same
lookup costs one round-trip. Preflight failure is **fail-closed** by default
(``on_error: deny``): a policy gate denies when it cannot prove the allow condition.

```yaml
- type: gate
  match_tools: [get_details, get_discussion]   # globs; which tools/call to gate
  preflight:
    tool: get_record_by_id                     # the out-of-band lookup
    args_from: { id: "$args.recordId" }        # map preflight args from the gated call
    cache: per-key                             # cache the lookup per resolved args
  allow_when:                                  # boolean predicate over the lookup result
    all:
      - any:
          - { path: "tags[*]", in: ["public", "internal"] }
          - { path: "title", matches: "\\[OK\\]" }
      - { path: "isSensitive", equals: false }
  on_deny: stub                                # stub | drop | error
  stub: { blocked: true, reason: "withheld by policy" }
```
"""

from __future__ import annotations

import fnmatch
import json
import logging
import re
from typing import Any

from ._catalog import tool_call_args, tool_call_name
from ._jsonutil import json_documents
from .base import BridgeContext, Decorator, Next, error_response, result_response

log = logging.getLogger("agent-mcp.gate")

_MISSING = object()

# A path step: a dotted key, an ``[*]`` array wildcard, or a ``[n]`` array index.
_STEP_RE = re.compile(r"([^.\[\]]+)|\[(\*)\]|\[(-?\d+)\]")


def _parse_path(path: str) -> list[tuple[str, Any]]:
    """Tokenize a path (``a.b[*].c`` / ``tags[*]`` / ``x[0]``) into steps."""
    steps: list[tuple[str, Any]] = []
    for key, wild, idx in _STEP_RE.findall(str(path)):
        if key:
            steps.append(("key", key))
        elif wild:
            steps.append(("wild", None))
        elif idx:
            steps.append(("idx", int(idx)))
    return steps


def _resolve_path(doc: Any, path: str) -> list[Any]:
    """Resolve ``path`` in ``doc`` to the (0..n) values it addresses.

    ``[*]`` fans out over a list; a bare key descends an object; ``[n]`` indexes a
    list. A key that misses, or a type mismatch, simply contributes no values --
    so an absent path yields ``[]`` (which reads as "condition not satisfied").
    """
    nodes: list[Any] = [doc]
    for kind, val in _parse_path(path):
        nxt: list[Any] = []
        for node in nodes:
            if kind == "key":
                if isinstance(node, dict) and val in node:
                    nxt.append(node[val])
            elif kind == "wild":
                if isinstance(node, list):
                    nxt.extend(node)
            elif kind == "idx" and isinstance(node, list) and -len(node) <= val < len(node):
                nxt.append(node[val])
        nodes = nxt
    return nodes


# ---------------------------------------------------------------------------
# Predicate engine
# ---------------------------------------------------------------------------

# Leaf comparison ops. "Positive" ops are satisfied when ANY resolved value
# matches; their negative twins are satisfied when NO resolved value matches
# (vacuously true when the path resolves to nothing).
_POSITIVE_OPS = ("in", "equals", "matches", "contains", "exists")
_NEGATIVE_OPS = {"not_in": "in", "not_matches": "matches", "not_equals": "equals"}
_ALL_OPS = (*_POSITIVE_OPS, *_NEGATIVE_OPS)


def _leaf_positive(op: str, value: Any, resolved: list[Any]) -> bool:
    """Evaluate a positive leaf op: true if ANY resolved value satisfies it."""
    if op == "exists":
        present = len(resolved) > 0
        return present if bool(value) else not present
    for v in resolved:
        if op == "in" and isinstance(value, list) and v in value:
            return True
        if op == "equals" and v == value:
            return True
        if op == "matches" and isinstance(v, str) and re.search(str(value), v):
            return True
        if op == "contains":
            if isinstance(v, (list, str)) and value in v:
                return True
    return False


def _eval_leaf(node: dict, doc: Any) -> bool:
    path = node.get("path")
    resolved = _resolve_path(doc, path) if path is not None else []
    for op, value in node.items():
        if op == "path":
            continue
        if op in _NEGATIVE_OPS:
            # No resolved value may satisfy the positive twin (vacuously true).
            if _leaf_positive(_NEGATIVE_OPS[op], value, resolved):
                return False
        elif op in _POSITIVE_OPS:
            if not _leaf_positive(op, value, resolved):
                return False
        else:
            log.warning("gate: unknown predicate op '%s' (ignored)", op)
    return True


def _eval_predicate(node: Any, doc: Any) -> bool:
    """Evaluate a predicate node (``all``/``any``/``not`` combinator or a leaf)."""
    if not isinstance(node, dict):
        return False
    if "all" in node:
        return all(_eval_predicate(c, doc) for c in (node["all"] or []))
    if "any" in node:
        return any(_eval_predicate(c, doc) for c in (node["any"] or []))
    if "not" in node:
        return not _eval_predicate(node["not"], doc)
    return _eval_leaf(node, doc)


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------

class GateDecorator(Decorator):
    type = "gate"

    def __init__(self, options: dict, ctx: BridgeContext) -> None:
        super().__init__(options, ctx)
        self.match_tools = [str(t) for t in (options.get("match_tools") or [])]
        preflight = options.get("preflight") or {}
        self.preflight_tool = str(preflight.get("tool", ""))
        self.args_from = dict(preflight.get("args_from") or {})
        self.cache_mode = str(preflight.get("cache") or "none")
        self.allow_when = options.get("allow_when")
        self.on_deny = str(options.get("on_deny", "stub"))
        self.on_error = str(options.get("on_error", "deny"))
        self.stub = options.get("stub")
        if self.stub is None:
            self.stub = {"blocked": True, "reason": "withheld by policy"}
        self._cache: dict[str, Any] = {}

    def _gates(self, name: str | None) -> bool:
        return name is not None and any(
            fnmatch.fnmatchcase(name, p) for p in self.match_tools)

    async def handle(self, request: dict, nxt: Next) -> dict | None:
        if request.get("method") != "tools/call":
            return await nxt(request)
        name = tool_call_name(request)
        if not self._gates(name):
            return await nxt(request)

        preflight_args = self._resolve_preflight_args(tool_call_args(request))
        doc = await self._preflight(preflight_args, nxt)
        if doc is _MISSING:
            log.warning("gate: preflight for '%s' failed; on_error=%s",
                        name, self.on_error)
            if self.on_error == "allow":
                return await nxt(request)
            return self._deny(request)

        if _eval_predicate(self.allow_when, doc):
            return await nxt(request)
        return self._deny(request)

    def _resolve_preflight_args(self, call_args: dict) -> dict:
        """Build the preflight tool's arguments from the gated call's args.

        Each ``args_from`` value is a ``$args.<path>`` reference into the gated
        call's arguments (``$args`` alone = the whole args object), or any other
        value passed through as a literal.
        """
        out: dict = {}
        for key, spec in self.args_from.items():
            if isinstance(spec, str) and spec.startswith("$args"):
                rest = spec[len("$args"):].lstrip(".")
                if not rest:
                    out[key] = call_args
                else:
                    vals = _resolve_path(call_args, rest)
                    if vals:
                        out[key] = vals[0]
            else:
                out[key] = spec
        return out

    async def _preflight(self, args: dict, nxt: Next) -> Any:
        """Issue the preflight lookup (cached per-key) and return its JSON doc."""
        cache_key = json.dumps(args, sort_keys=True, default=str)
        if self.cache_mode == "per-key" and cache_key in self._cache:
            return self._cache[cache_key]
        req = {"jsonrpc": "2.0", "id": self.ctx.new_id(), "method": "tools/call",
               "params": {"name": self.preflight_tool, "arguments": args}}
        resp = await nxt(req)
        doc = self._result_doc(resp)
        if doc is not _MISSING and self.cache_mode == "per-key":
            self._cache[cache_key] = doc
        return doc

    @staticmethod
    def _result_doc(resp: Any) -> Any:
        """The first JSON document (structured or JSON text) in a tool result."""
        if not isinstance(resp, dict) or "error" in resp:
            return _MISSING
        result = resp.get("result")
        if not isinstance(result, dict) or result.get("isError"):
            return _MISSING
        docs = json_documents(result)
        return docs[0][2] if docs else _MISSING

    def _deny(self, request: dict) -> dict:
        if self.on_deny == "error":
            return error_response(
                request, "tool result withheld by gate policy", code=-32603)
        if self.on_deny == "drop":
            return result_response(request, {"content": [], "isError": False})
        # stub (default): return the configured policy payload as the result.
        result: dict = {
            "content": [{"type": "text", "text": json.dumps(self.stub)}],
            "isError": False,
        }
        if isinstance(self.stub, dict):
            result["structuredContent"] = self.stub
        return result_response(request, result)

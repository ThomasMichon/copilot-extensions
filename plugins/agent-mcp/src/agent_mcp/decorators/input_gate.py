"""``input_gate`` decorator -- deny a tool call whose OWN arguments match a predicate.

``gate`` (see ``gate.py``) judges a call by an out-of-band **preflight** fact
(a different tool's response). Some authorization invariants instead need to
judge the call by its **own request arguments** -- e.g. "never let an
``update_incident`` call itself introduce the string ``ai-safe`` into a
``tags``/``title`` argument", so that a marker meant to be human-set can't be
self-granted by the same agent session that also reads gated content once the
marker is present.

Neither existing decorator can express that: ``filter`` is a static per-tool
allow/deny (no argument inspection at all), and ``transform`` only reshapes a
tool's **output**, never its input. ``input_gate`` closes that specific gap:
when a client calls one of ``match_tools``, it evaluates a boolean
``deny_when`` predicate over the call's **own arguments** (using the same
path/op mini-language as ``gate``'s ``allow_when`` -- see ``_predicate.py``)
and denies the call if it matches, before it ever reaches the upstream.

This is a narrow, single-purpose complement to ``gate``, not a replacement:
``gate`` decides "is this incident's OWN state safe to read"; ``input_gate``
decides "does this WRITE attempt introduce a marker/value it must never
introduce", independent of any preflight lookup.

```yaml
- type: input_gate
  match_tools: [update_incident]                # globs; which tools/call to gate
  deny_when:                                     # boolean predicate over the call's OWN args
    any:
      - { path: "tags[*]", matches: "(?i)^ai-safe$" }
      - { path: "title", matches: "(?i)(^|[^A-Za-z0-9_-])ai-safe([^A-Za-z0-9_-]|$)" }
  on_deny: error                                  # error | stub | drop (default: error)
  reason: "the ai-safe tag/keyword is human-only; an agent must never self-grant it"
```

Unlike ``gate`` (fail-closed on preflight error, since it protects a READ), a
predicate evaluation error here can only reasonably fail closed too --
``deny_when`` is evaluated purely locally (no network call), so there is no
transient-failure case to distinguish; a malformed predicate is a config bug,
not a runtime condition, and is caught by config validation instead
(:func:`agent_mcp.config._validate_input_gate` recursively validates the
``deny_when`` tree's shape and regexes at load time).

> **Placement -- put ``input_gate`` innermost (last in the ``decorators:``
> list, closest to the upstream).** ``code-mode``/``defer`` synthesize their
> own ``tools/call`` sub-requests and forward them via the SAME ``nxt`` they
> themselves were invoked with -- i.e. only to decorators BELOW their own
> position, never back through decorators above them. Recommended ordering
> already places ``code-mode``/``defer`` first/outermost (see
> ``decorators/__init__.py``'s module docstring) specifically so their
> synthesized calls still pass through everything below -- but that only
> protects ``input_gate`` if ``input_gate`` is ALSO below them, not above.
> Likewise, ``storage`` may rehydrate a ``$stream`` argument handle into its
> real value on the way to upstream; an ``input_gate`` placed OUTER (earlier)
> than ``storage`` would evaluate ``deny_when`` against the un-rehydrated
> handle instead of the real value it references, silently missing a match.
> Placing ``input_gate`` last -- after ``code-mode``/``defer``/``storage`` --
> guarantees it always evaluates the fully-resolved arguments the upstream is
> actually about to receive, regardless of what synthesized or rehydrated the
> call.
"""

from __future__ import annotations

import fnmatch
import json
import logging

from .._predicate import eval_predicate
from ..pipeline import is_notification
from ._catalog import tool_call_args, tool_call_name
from .base import BridgeContext, Decorator, Next, error_response, result_response

log = logging.getLogger("agent-mcp.input_gate")


class InputGateDecorator(Decorator):
    type = "input_gate"

    def __init__(self, options: dict, ctx: BridgeContext) -> None:
        super().__init__(options, ctx)
        self.match_tools = [str(t) for t in (options.get("match_tools") or [])]
        self.deny_when = options.get("deny_when")
        self.on_deny = str(options.get("on_deny", "error"))
        self.reason = str(options.get("reason") or "denied by input policy")
        self.stub = options.get("stub")
        if self.stub is None:
            self.stub = {"blocked": True, "reason": self.reason}

    def _gates(self, name: str | None) -> bool:
        return name is not None and any(
            fnmatch.fnmatchcase(name, p) for p in self.match_tools)

    async def handle(self, request: dict, nxt: Next) -> dict | None:
        if request.get("method") != "tools/call":
            return await nxt(request)
        name = tool_call_name(request)
        if not self._gates(name):
            return await nxt(request)

        args = tool_call_args(request)
        if eval_predicate(self.deny_when, args, log=log):
            log.warning("input_gate: denied '%s' -- %s", name, self.reason)
            if is_notification(request):
                # A JSON-RPC notification (no 'id') never gets a response --
                # per the pipeline contract (pipeline.py), fire-and-forget.
                # Denying it means simply not forwarding it; there is nothing
                # to reply with (an id:null response would be an invalid
                # message on a notification-only stream).
                return None
            return self._deny(request)
        return await nxt(request)

    def _deny(self, request: dict) -> dict:
        if self.on_deny == "error":
            return error_response(request, self.reason, code=-32603)
        if self.on_deny == "drop":
            return result_response(request, {"content": [], "isError": False})
        # stub (default for parity with `gate`'s naming, though this decorator
        # defaults on_deny to "error" since it protects a WRITE, not a READ).
        result: dict = {
            "content": [{"type": "text", "text": json.dumps(self.stub)}],
            "isError": False,
        }
        if isinstance(self.stub, dict):
            result["structuredContent"] = self.stub
        return result_response(request, result)

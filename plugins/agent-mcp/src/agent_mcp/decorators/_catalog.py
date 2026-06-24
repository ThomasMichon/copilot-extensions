"""Shared helpers for tool catalogs and JSON-Schema -> TypeScript rendering.

Several decorators (``defer``, ``code-mode``) need the *full* upstream tool
catalog. :func:`fetch_all_tools` issues ``tools/list`` through the ``nxt`` chain,
following ``nextCursor`` pagination, so a 100+ tool catalog is retrieved whole.
"""

from __future__ import annotations

import keyword
from typing import Any

from .base import BridgeContext, Next

_PAGE_LIMIT = 100  # safety bound on pagination loops


def tool_call_name(request: dict) -> str | None:
    """The tool name from a ``tools/call`` request, or ``None``."""
    if request.get("method") != "tools/call":
        return None
    params = request.get("params") or {}
    name = params.get("name")
    return name if isinstance(name, str) else None


def tool_call_args(request: dict) -> dict:
    """The ``arguments`` mapping from a ``tools/call`` request."""
    params = request.get("params") or {}
    args = params.get("arguments")
    return args if isinstance(args, dict) else {}


def text_result(text: str, *, is_error: bool = False) -> dict:
    """A ``tools/call`` result with a single text content block."""
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


async def fetch_all_tools(nxt: Next, ctx: BridgeContext) -> list[dict]:
    """Issue ``tools/list`` (following pagination) and return every tool."""
    tools: list[dict] = []
    cursor: str | None = None
    for _ in range(_PAGE_LIMIT):
        params: dict[str, Any] = {}
        if cursor:
            params["cursor"] = cursor
        req = {"jsonrpc": "2.0", "id": ctx.new_id(), "method": "tools/list",
               "params": params}
        resp = await nxt(req)
        if not isinstance(resp, dict) or "result" not in resp:
            break
        result = resp.get("result") or {}
        page = result.get("tools")
        if isinstance(page, list):
            tools.extend(t for t in page if isinstance(t, dict))
        cursor = result.get("nextCursor")
        if not cursor:
            break
    return tools


# ---------------------------------------------------------------------------
# JSON-Schema -> TypeScript (best-effort, for code-mode interfaces)
# ---------------------------------------------------------------------------

def _ts_type(schema: Any) -> str:
    """Render a JSON-Schema fragment as a TypeScript type (fallback ``any``)."""
    if not isinstance(schema, dict):
        return "any"
    if "enum" in schema and isinstance(schema["enum"], list):
        return " | ".join(_ts_literal(v) for v in schema["enum"]) or "any"
    if "anyOf" in schema or "oneOf" in schema:
        variants = schema.get("anyOf") or schema.get("oneOf") or []
        rendered = sorted({_ts_type(v) for v in variants})
        return " | ".join(rendered) or "any"
    t = schema.get("type")
    if isinstance(t, list):
        return " | ".join(sorted({_ts_type({**schema, "type": one}) for one in t})) or "any"
    if t == "string":
        return "string"
    if t in ("number", "integer"):
        return "number"
    if t == "boolean":
        return "boolean"
    if t == "null":
        return "null"
    if t == "array":
        return f"{_ts_type(schema.get('items'))}[]"
    if t == "object" or "properties" in schema:
        return _ts_object(schema)
    return "any"


def _ts_literal(value: Any) -> str:
    if isinstance(value, str):
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    return "any"


def _ts_object(schema: dict) -> str:
    props = schema.get("properties")
    if not isinstance(props, dict) or not props:
        return "Record<string, any>"
    required = set(schema.get("required") or [])
    fields = []
    for key, sub in props.items():
        opt = "" if key in required else "?"
        fields.append(f"{_ts_key(key)}{opt}: {_ts_type(sub)}")
    return "{ " + "; ".join(fields) + " }"


def _ts_key(key: str) -> str:
    if key.isidentifier() and not keyword.iskeyword(key):
        return key
    return '"' + key.replace('"', '\\"') + '"'


def _safe_method_name(name: str) -> str:
    """Whether ``name`` can be a dotted JS method (``tools.name``)."""
    return bool(name) and name.isidentifier() and not keyword.iskeyword(name)


def render_tools_interface(tools: list[dict], *, interface: str = "Tools") -> str:
    """Render a ``Tools`` TypeScript interface declaring every tool as a method."""
    lines = [f"interface {interface} {{"]
    for tool in tools:
        name = tool.get("name", "")
        if not name:
            continue
        desc = (tool.get("description") or "").strip()
        schema = tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {}
        arg_type = _ts_type(schema) if schema else "Record<string, any>"
        if desc:
            for line in desc.splitlines():
                lines.append(f"  /** {line.strip()} */")
        if _safe_method_name(name):
            lines.append(f"  {name}(args: {arg_type}): Promise<any>;")
        else:
            lines.append(f"  [\"{name}\"](args: {arg_type}): Promise<any>;")
    lines.append("}")
    return "\n".join(lines)

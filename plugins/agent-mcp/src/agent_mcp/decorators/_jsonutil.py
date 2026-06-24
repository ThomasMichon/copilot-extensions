"""Small JSON helpers for the storage decorator: dotted-path get/set, schema
inference, and input-schema 'streamify' rewriting."""

from __future__ import annotations

import json
from typing import Any

MISSING = object()


def split_path(path: str) -> list[str]:
    """Split a dotted path (``data.items.0``) into segments."""
    return [seg for seg in str(path).split(".") if seg != ""]


def _match_key(node: dict, segs: list[str], i: int) -> tuple[str | None, int]:
    """Longest dotted key in ``node`` matching ``segs`` from index ``i``.

    Lets a path address a literal dotted key (e.g. ADO ``fields.System.Title``
    where ``fields`` is ``{"System.Title": ...}``) as well as genuine nesting.
    """
    for j in range(len(segs), i, -1):
        key = ".".join(segs[i:j])
        if key in node:
            return key, j
    return None, i


def get_path(doc: Any, segs: list[str]) -> Any:
    """Return the value at ``segs`` in ``doc``, or ``MISSING`` if absent."""
    cur = doc
    i = 0
    while i < len(segs):
        if isinstance(cur, dict):
            key, ni = _match_key(cur, segs, i)
            if key is None:
                return MISSING
            cur = cur[key]
            i = ni
        elif isinstance(cur, list):
            try:
                idx = int(segs[i])
            except ValueError:
                return MISSING
            if not -len(cur) <= idx < len(cur):
                return MISSING
            cur = cur[idx]
            i += 1
        else:
            return MISSING
    return cur


def has_path(doc: Any, segs: list[str]) -> bool:
    return get_path(doc, segs) is not MISSING


def set_path(doc: Any, segs: list[str], value: Any) -> bool:
    """Replace the existing value at ``segs`` in ``doc`` in place. Returns success."""
    if not segs:
        return False
    cur = doc
    i = 0
    while i < len(segs):
        if isinstance(cur, dict):
            key, ni = _match_key(cur, segs, i)
            if key is None:
                return False
            if ni == len(segs):
                cur[key] = value
                return True
            cur = cur[key]
            i = ni
        elif isinstance(cur, list):
            try:
                idx = int(segs[i])
            except ValueError:
                return False
            if not -len(cur) <= idx < len(cur):
                return False
            if i == len(segs) - 1:
                cur[idx] = value
                return True
            cur = cur[idx]
            i += 1
        else:
            return False
    return False


def infer_schema(value: Any, *, max_props: int = 50) -> dict:
    """Infer a minimal JSON Schema from an example value."""
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if isinstance(value, str):
        return {"type": "string"}
    if value is None:
        return {"type": "null"}
    if isinstance(value, list):
        schema: dict = {"type": "array"}
        if value:
            schema["items"] = infer_schema(value[0], max_props=max_props)
        return schema
    if isinstance(value, dict):
        props = {k: infer_schema(v, max_props=max_props)
                 for k, v in list(value.items())[:max_props]}
        return {"type": "object", "properties": props}
    return {}


def streamify_schema(schema: dict, segs: list[str], note: str) -> bool:
    """Rewrite the property at ``segs`` in a JSON Schema into a stream-URL string.

    The original type/description are preserved in the new description so the
    model knows the URL must point at a JSON-serialized instance of the original
    value. Returns True if the property was found and rewritten.
    """
    if not segs or not isinstance(schema, dict):
        return False
    cur = schema
    for seg in segs[:-1]:
        props = cur.get("properties")
        if not isinstance(props, dict) or seg not in props:
            return False
        cur = props[seg]
    props = cur.get("properties")
    if not isinstance(props, dict):
        return False
    leaf = segs[-1]
    original = props.get(leaf) if isinstance(props.get(leaf), dict) else {}
    orig_type = original.get("type", "value")
    orig_desc = (original.get("description") or "").strip()
    desc = (f"URL to a stream containing a JSON-serialized {orig_type}"
            f"{(' — ' + note) if note else ''}.")
    if orig_desc:
        desc += f" Original: {orig_desc}"
    props[leaf] = {"type": "string", "format": "uri", "description": desc}
    return True


def json_documents(result: dict):
    """Yield ``(kind, block, doc)`` JSON documents in a tool result.

    ``structuredContent`` (mutated in place) and any text content block whose
    text parses as a JSON object/array (re-serialized by the caller after edits).
    Shared by the ``storage`` and ``transform`` decorators.
    """
    docs: list[tuple[str, dict | None, Any]] = []
    structured = result.get("structuredContent")
    if isinstance(structured, (dict, list)):
        docs.append(("structured", None, structured))
    content = result.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    try:
                        parsed = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(parsed, (dict, list)):
                        docs.append(("text", block, parsed))
    return docs


def set_doc_in_block(kind: str, block: dict | None, doc: Any) -> None:
    """Write a (possibly replaced) document back into its text content block."""
    if kind == "text" and block is not None:
        block["text"] = json.dumps(doc)


def pick_paths(doc: Any, paths: list[str]) -> dict:
    """Build a new object keeping only ``paths`` (matched key shape preserved)."""
    out: dict = {}
    for path in paths:
        _copy_into(doc, out, split_path(path))
    return out


def _copy_into(src: Any, dst: dict, segs: list[str]) -> None:
    cur_src = src
    cur_dst = dst
    i = 0
    while i < len(segs):
        if not isinstance(cur_src, dict):
            return  # pick only descends through objects
        key, ni = _match_key(cur_src, segs, i)
        if key is None:
            return
        if ni == len(segs):  # leaf -- copy under the same key it matched
            cur_dst[key] = cur_src[key]
            return
        nxt_dst = cur_dst.get(key)
        if not isinstance(nxt_dst, dict):
            nxt_dst = {}
            cur_dst[key] = nxt_dst
        cur_src = cur_src[key]
        cur_dst = nxt_dst
        i = ni


def drop_path(doc: Any, segs: list[str]) -> None:
    """Remove the value at ``segs`` from ``doc`` in place."""
    if not segs:
        return
    cur = doc
    i = 0
    while i < len(segs):
        if isinstance(cur, dict):
            key, ni = _match_key(cur, segs, i)
            if key is None:
                return
            if ni == len(segs):
                cur.pop(key, None)
                return
            cur = cur[key]
            i = ni
        elif isinstance(cur, list):
            try:
                idx = int(segs[i])
            except ValueError:
                return
            if not -len(cur) <= idx < len(cur):
                return
            if i == len(segs) - 1:
                del cur[idx]
                return
            cur = cur[idx]
            i += 1
        else:
            return

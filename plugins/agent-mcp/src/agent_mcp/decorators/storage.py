"""``storage`` decorator -- relay large tool I/O through a stream buffer.

Big tool outputs flood the model's context and big inputs must be re-typed by the
model. This decorator externalizes them:

* **Outputs** -- a ``tools/call`` result text block larger than ``threshold`` is
  written to a backing store; the client receives a short preview plus a *handle*
  and can fetch the full value on demand via the ``read_stream`` meta-tool.
* **Inputs** -- a handle anywhere in a tool's ``arguments`` (a bare handle string
  or ``{"$stream": "<handle>"}``) is rehydrated to the stored value before the
  call is forwarded upstream -- so one tool's output can be piped into another's
  input without the payload passing through the model.

Backends: ``file`` (local filesystem, default) and ``http`` (POST to store / GET
to read).
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import os
import subprocess
import urllib.request
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ._catalog import tool_call_args, tool_call_name
from ._jsonutil import (
    MISSING,
    get_path,
    infer_schema,
    json_documents,
    set_path,
    split_path,
    streamify_schema,
)
from .base import BridgeContext, Decorator, Next, error_response, result_response

log = logging.getLogger("agent-mcp.storage")

_DEFAULT_DIR = Path(os.environ.get("AGENT_MCP_HOME", Path.home() / ".agent-mcp")) / "storage"


class StreamBackend:
    """Stores/loads opaque text payloads addressed by a handle string."""

    def store(self, text: str) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    def owns(self, handle: str) -> bool:  # pragma: no cover - interface
        raise NotImplementedError

    def load(self, handle: str) -> str:  # pragma: no cover - interface
        raise NotImplementedError


class FileBackend(StreamBackend):
    """Filesystem backend. Handles look like ``mcpstream://<uuid>``."""

    scheme = "mcpstream://"

    def __init__(self, directory: Path) -> None:
        self.dir = Path(directory).expanduser()
        self.dir.mkdir(parents=True, exist_ok=True)

    def store(self, text: str) -> str:
        uid = uuid.uuid4().hex
        (self.dir / uid).write_text(text, encoding="utf-8")
        return f"{self.scheme}{uid}"

    def owns(self, handle: str) -> bool:
        return isinstance(handle, str) and handle.startswith(self.scheme)

    def load(self, handle: str) -> str:
        uid = handle[len(self.scheme):]
        if not uid:
            raise ValueError(f"invalid stream handle: {handle}")
        # Resolve and require the target to sit *directly* inside the store, so a
        # model-supplied handle cannot escape it (subdirs, ``..``, or Windows
        # drive-relative paths like ``C:Windows`` all resolve elsewhere).
        base = self.dir.resolve()
        try:
            path = (self.dir / uid).resolve()
        except (OSError, ValueError) as exc:
            raise ValueError(f"invalid stream handle: {handle}") from exc
        if path.parent != base:
            raise ValueError(f"invalid stream handle: {handle}")
        if not path.exists():
            raise FileNotFoundError(f"stream not found: {handle}")
        return path.read_text(encoding="utf-8")


class HttpBackend(StreamBackend):
    """HTTP backend: POST ``<url>`` to store, GET the returned handle to read."""

    def __init__(self, url: str, timeout: float = 30.0) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout
        self._base = urlparse(self.url)

    def store(self, text: str) -> str:
        req = urllib.request.Request(self.url, data=text.encode("utf-8"),
                                     headers={"Content-Type": "text/plain"}, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            location = resp.headers.get("Location")
            body = resp.read().decode("utf-8", errors="replace")
        if location:
            if location.startswith("http"):
                return location
            return f"{self.url}/{location.lstrip('/')}"
        try:
            return f"{self.url}/{json.loads(body)['id']}"
        except (json.JSONDecodeError, KeyError, TypeError):
            return body.strip()

    def owns(self, handle: str) -> bool:
        # Exact scheme+host match and a path under the configured base, so a
        # look-alike host (``store.internal.evil.com``) is not accepted (SSRF).
        if not isinstance(handle, str):
            return False
        p = urlparse(handle)
        if p.scheme != self._base.scheme or p.netloc != self._base.netloc:
            return False
        base_path = self._base.path.rstrip("/")
        return p.path == base_path or p.path.startswith(base_path + "/")

    def load(self, handle: str) -> str:
        req = urllib.request.Request(handle, method="GET")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")


def build_backend(options: dict) -> StreamBackend:
    backend = str(options.get("backend", "file"))
    if backend == "http":
        url = options.get("url")
        if not url:
            raise ValueError("storage backend 'http' requires 'url'")
        return HttpBackend(str(url), float(options.get("timeout", 30.0)))
    return FileBackend(Path(options.get("dir") or _DEFAULT_DIR))


class _OutputRule:
    __slots__ = ("segs", "summary")

    def __init__(self, path: str, summary: Any) -> None:
        self.segs = split_path(path)
        self.summary = summary  # None | True | dict ({count,schema,head} or {command})


class _InputRule:
    __slots__ = ("note", "segs")

    def __init__(self, path: str, note: str) -> None:
        self.segs = split_path(path)
        self.note = note


class _Rule:
    __slots__ = ("inputs", "outputs", "tool")

    def __init__(self, spec: dict) -> None:
        self.tool = str(spec.get("tool", "*"))
        self.outputs = [_OutputRule(o.get("path", ""), o.get("summary"))
                        for o in (spec.get("outputs") or []) if isinstance(o, dict)]
        self.inputs = [_InputRule(i.get("path", ""), str(i.get("note", "")))
                       for i in (spec.get("inputs") or []) if isinstance(i, dict)]


class StorageDecorator(Decorator):
    type = "storage"

    def __init__(self, options: dict, ctx: BridgeContext) -> None:
        super().__init__(options, ctx)
        self.backend = build_backend(options)
        self.threshold = int(options.get("threshold", 8192))
        self.max_preview = int(options.get("max_preview", 200))
        self.read_name = str(options.get("read_tool", "read_stream"))
        self.command_timeout = float(options.get("command_timeout", 30.0))
        self.rules = [_Rule(r) for r in (options.get("rules") or []) if isinstance(r, dict)]

    async def handle(self, request: dict, nxt: Next) -> dict | None:
        method = request.get("method")
        name = tool_call_name(request)

        if name == self.read_name:
            return self._handle_read(request)

        if method == "tools/call":
            request = self._rehydrate_request(request)

        resp = await nxt(request)
        if method == "tools/call":
            await self._externalize_outputs(resp, name)
        elif method == "tools/list":
            self._rewrite_input_schemas(resp)
            self._add_read_tool(resp)
        return resp

    # -- rule lookup -------------------------------------------------------

    def _output_rules_for(self, tool: str | None) -> list[_OutputRule]:
        rules: list[_OutputRule] = []
        for rule in self.rules:
            if tool is not None and rule.outputs and fnmatch.fnmatchcase(tool, rule.tool):
                rules.extend(rule.outputs)
        return rules

    def _input_rules_for(self, tool: str | None) -> list[_InputRule]:
        rules: list[_InputRule] = []
        for rule in self.rules:
            if tool is not None and rule.inputs and fnmatch.fnmatchcase(tool, rule.tool):
                rules.extend(rule.inputs)
        return rules

    # -- inputs ------------------------------------------------------------

    def _rehydrate_request(self, request: dict) -> dict:
        args = tool_call_args(request)
        if not args:
            return request
        new_args = self._rehydrate(args)
        params = dict(request.get("params") or {})
        params["arguments"] = new_args
        return {**request, "params": params}

    def _rehydrate(self, value):
        if isinstance(value, dict):
            # An externalized output {"$stream": h, "summary": ..} round-trips back
            # into a tool input here -- restore the original value.
            handle = value.get("$stream")
            if isinstance(handle, str):
                return self._load(handle)
            return {k: self._rehydrate(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self._rehydrate(v) for v in value]
        if isinstance(value, str) and self.backend.owns(value):
            return self._load(value)
        return value

    def _load(self, handle: str):
        text = self.backend.load(handle)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text

    # -- outputs -----------------------------------------------------------

    async def _externalize_outputs(self, resp: dict | None, tool: str | None) -> None:
        if not isinstance(resp, dict):
            return
        result = resp.get("result")
        if not isinstance(result, dict):
            return
        rules = self._output_rules_for(tool)
        if rules:
            await self._apply_output_rules(result, rules)
        else:
            # No field-level rule for this tool: fall back to blanket
            # externalization of any oversized text block.
            self._externalize_blanket(result)

    async def _apply_output_rules(self, result: dict, rules: list[_OutputRule]) -> None:
        for kind, block, doc in json_documents(result):
            changed = False
            for rule in rules:
                value = get_path(doc, rule.segs)
                if value is MISSING:
                    continue
                payload = json.dumps(value)
                ref: dict[str, Any] = {"$stream": self.backend.store(payload),
                                       "bytes": len(payload.encode("utf-8"))}
                summary = await self._summarize(value, rule.summary)
                if summary is not None:
                    ref["summary"] = summary
                if set_path(doc, rule.segs, ref):
                    changed = True
            if changed and kind == "text" and block is not None:
                block["text"] = json.dumps(doc)

    async def _summarize(self, value, spec) -> Any:
        if spec is False:
            return None
        if isinstance(spec, dict) and spec.get("command"):
            return await self._summarize_command(value, spec["command"])
        # Summary is on by default (count + inferred schema + first 3 items);
        # set ``summary: false`` to disable, or pass a mapping to customize.
        opts = spec if isinstance(spec, dict) else {}
        out: dict[str, Any] = {}
        if opts.get("count", True):
            out["count"] = len(value) if isinstance(value, (list, dict, str)) else None
        if opts.get("schema", True):
            out["schema"] = infer_schema(value)
        head = opts.get("head", 3)
        if head:
            if isinstance(value, list):
                out["head"] = value[: int(head)]
            elif isinstance(value, str):
                out["preview"] = value[: int(head)]
        return out

    async def _summarize_command(self, value, command) -> Any:
        argv = [str(c) for c in command]
        try:
            proc = await asyncio.to_thread(
                subprocess.run, argv, input=json.dumps(value),
                capture_output=True, text=True, timeout=self.command_timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            return {"error": f"summary command failed: {exc}"}
        out = (proc.stdout or "").strip()
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return out

    def _externalize_blanket(self, result: dict) -> None:
        content = result.get("content")
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "text":
                continue
            text = block.get("text", "")
            if not isinstance(text, str) or len(text.encode("utf-8")) < self.threshold:
                continue
            handle = self.backend.store(text)
            preview = text[: self.max_preview]
            block["text"] = (
                f"{preview}\u2026\n[stored {len(text)} chars at {handle} \u2014 "
                f"fetch the full value with {self.read_name}, or pass the handle "
                f"as a tool input via {{\"$stream\": \"{handle}\"}}]"
            )
            annotations = block.setdefault("annotations", {})
            if isinstance(annotations, dict):
                annotations["stream"] = handle

    # -- input-schema rewrite ----------------------------------------------

    def _rewrite_input_schemas(self, resp: dict | None) -> None:
        if not isinstance(resp, dict):
            return
        result = resp.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
            return
        for tool in result["tools"]:
            if not isinstance(tool, dict):
                continue
            rules = self._input_rules_for(tool.get("name"))
            schema = tool.get("inputSchema")
            if not rules or not isinstance(schema, dict):
                continue
            for inp in rules:
                streamify_schema(schema, inp.segs, inp.note)

    # -- read_stream meta-tool ---------------------------------------------

    def _add_read_tool(self, resp: dict | None) -> None:
        if not isinstance(resp, dict):
            return
        result = resp.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
            return
        result["tools"].append({
            "name": self.read_name,
            "description": "Fetch a stored stream payload by handle (optionally a "
                           "byte range via offset/length).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "handle": {"type": "string", "description": "Stream handle."},
                    "offset": {"type": "integer", "description": "Start character offset."},
                    "length": {"type": "integer", "description": "Max characters to return."},
                },
                "required": ["handle"],
            },
        })

    def _handle_read(self, request: dict) -> dict:
        args = tool_call_args(request)
        handle = args.get("handle")
        if not isinstance(handle, str) or not self.backend.owns(handle):
            return error_response(request, "a valid stream 'handle' is required",
                                  code=-32602)
        try:
            text = self.backend.load(handle)
        except (FileNotFoundError, ValueError) as exc:
            return error_response(request, str(exc))
        offset = int(args.get("offset", 0) or 0)
        length = args.get("length")
        sliced = text[offset: offset + int(length)] if length is not None else text[offset:]
        return result_response(request, {
            "content": [{"type": "text", "text": sliced}], "isError": False})

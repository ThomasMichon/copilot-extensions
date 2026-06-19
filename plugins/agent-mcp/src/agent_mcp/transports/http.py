"""HTTP (Streamable HTTP + SSE) upstream transport.

A Streamable HTTP + SSE upstream transport for the MCP bridge:
the bearer/header is supplied by the configured :class:`AuthInjector` rather than
hardcoded to a single auth command. Requests are serialized (one in flight at a time), the
``Mcp-Session-Id`` header is captured and replayed, and a ``401`` triggers one
auth refresh + retry.

Uses the standard library (``urllib``) on a worker thread to avoid adding an
HTTP dependency; the bridge is single-flight so blocking I/O off the event loop
via :func:`asyncio.to_thread` is sufficient.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request

from .base import Transport
from .sse import parse_sse_events

log = logging.getLogger("agent-mcp.http")


class HttpTransport(Transport):
    """Wrap a remote Streamable HTTP MCP endpoint."""

    def __init__(self, cfg, injector) -> None:
        super().__init__(cfg, injector)
        self._url = cfg.server.url or ""
        self._session_id: str | None = None
        self._lock = asyncio.Lock()

    async def _base_headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        headers.update(self.cfg.headers)
        headers.update(await self.injector.headers())
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _post(self, headers: dict[str, str], body: bytes) -> tuple[int, dict[str, str], str]:
        req = urllib.request.Request(self._url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.cfg.timeout) as resp:
                text = resp.read().decode("utf-8", errors="replace")
                return resp.status, {k.lower(): v for k, v in resp.headers.items()}, text
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            return exc.code, {k.lower(): v for k, v in (exc.headers or {}).items()}, text

    async def send(self, msg: dict) -> None:
        async with self._lock:
            await self._send_locked(msg)

    async def _send_locked(self, msg: dict, *, retried: bool = False) -> None:
        headers = await self._base_headers()
        body = json.dumps(msg).encode("utf-8")
        try:
            status, resp_headers, text = await asyncio.to_thread(self._post, headers, body)
        except (TimeoutError, urllib.error.URLError, OSError) as exc:
            log.error("upstream POST failed: %s", exc)
            await self._error(msg, f"HTTP error: {exc}")
            return

        sid = resp_headers.get("mcp-session-id")
        if sid:
            self._session_id = sid

        if status == 401 and not retried:
            log.info("401 from upstream -- refreshing credential and retrying")
            await self.injector.invalidate()
            await self._send_locked(msg, retried=True)
            return

        if status == 202:  # notification accepted, no body
            return

        if status >= 400:
            log.error("upstream HTTP %s: %s", status, text[:200])
            await self._error(msg, f"HTTP {status}")
            return

        await self._dispatch_body(resp_headers.get("content-type", ""), text)

    async def _dispatch_body(self, content_type: str, text: str) -> None:
        if "text/event-stream" in content_type:
            for evt in parse_sse_events(text):
                obj = _safe_json(evt.data)
                if obj is not None:
                    await self._emit_message(obj)
        elif text.strip():
            obj = _safe_json(text)
            if obj is not None:
                await self._emit_message(obj)

    async def _error(self, msg: dict, message: str) -> None:
        mid = msg.get("id")
        if mid is None:
            return
        err = {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": message}}
        await self._emit_message(err)


def _safe_json(text: str) -> dict | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        log.warning("non-JSON upstream payload: %s", text[:200])
        return None

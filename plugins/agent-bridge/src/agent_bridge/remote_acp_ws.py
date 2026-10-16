"""Bounded, redirects-off ACP WebSocket connection using public WS primitives."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import ssl as ssl_module
from collections.abc import Callable
from http.cookiejar import CookieJar
from urllib.request import Request
from urllib.parse import urlsplit
from typing import Any

from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus
from credential_relay.http_auth import HttpAuthInjector

from .remote_acp_transport import RemoteTransport, RemoteTransportError
from .remote_acp_config import RemoteAcpTarget


class _NoRedirectConnect(connect):
    def process_redirect(self, exc):
        # Public websockets override hook. Never carry auth to a redirected URI,
        # even when the destination happens to have the same origin.
        return exc


class WsAcpTransport(RemoteTransport):
    def __init__(
        self, target: RemoteAcpTarget, auth: HttpAuthInjector | None = None, *,
        cookies: CookieJar | None = None, ssl: ssl_module.SSLContext | None = None,
        on_lost: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(target, auth, on_lost=on_lost)
        self._ws = None
        self._ssl = ssl
        self._cookies = cookies if cookies is not None else CookieJar()
        self.cookies = self._cookies
        self._initialized = False

    async def open(self) -> WsAcpTransport:
        if self.closed or self._ended.is_set():
            raise RemoteTransportError("Remote ACP connection closed")
        url = self.target.endpoint
        parsed = urlsplit(url)
        uri = parsed._replace(scheme="wss" if parsed.scheme == "https" else "ws").geturl()
        for attempt in range(2):
            request = Request(parsed.geturl())
            self._cookies.add_cookie_header(request)
            headers = await self.headers()
            if request.has_header("Cookie"):
                headers["Cookie"] = request.get_header("Cookie")
            try:
                options = {}
                if self._ssl is not None:
                    options["ssl"] = self._ssl
                self._ws = await _NoRedirectConnect(
                    uri, additional_headers=headers,
                    open_timeout=self.target.timeout_seconds,
                    close_timeout=self.target.timeout_seconds,
                    max_size=self.target.max_frame_bytes,
                    max_queue=self.target.max_queue_messages,
                    ping_interval=20, ping_timeout=self.target.timeout_seconds,
                    compression=None, proxy=None, **options,
                    logger=logging.Logger("agent-bridge.remote-acp.wire", logging.WARNING),
                )
                if self.closed or self._ended.is_set():
                    raise RemoteTransportError("Remote WS handshake completed after close")
                connection_id = self._ws.response.headers.get("Acp-Connection-Id")
                if not connection_id or len(connection_id) > 1024:
                    raise RemoteTransportError("Missing remote connection identity")
                self._capture_cookies(self._ws.response.headers, request)
                self.task(self._consume())
                return self
            except InvalidStatus as exc:
                if exc.response.status_code == 401 and attempt == 0 and self.auth:
                    self._capture_cookies(exc.response.headers, request)
                    await self.refresh_auth()
                    continue
                raise RemoteTransportError("Remote WS handshake refused") from None
            except Exception:
                if self._ws is not None:
                    await self._ws.close()
                raise RemoteTransportError("Remote WS handshake failed") from None
        raise RemoteTransportError("Remote WS authentication refused")

    def _capture_cookies(self, headers, request) -> None:
        from email.message import Message
        from types import SimpleNamespace

        try:
            values = Message()
            for value in headers.get_all("Set-Cookie"):
                values.add_header("Set-Cookie", value)
            self._cookies.extract_cookies(SimpleNamespace(info=lambda: values), request)
        except Exception:
            raise RemoteTransportError("Remote affinity cookie refused") from None

    async def send(self, message: dict[str, Any]) -> None:
        try:
            if not self._initialized:
                if message.get("method") != "initialize":
                    raise RemoteTransportError("Remote WS requires initialize first")
                self._initialized = True
            self.outgoing_session(message)
            raw = self.encode(message)
            await asyncio.wait_for(
                self._ws.send(raw.decode()), self.target.timeout_seconds,
            )
        except Exception:
            self.fail("Remote WS send failed; outcome unknown")
            raise RemoteTransportError("Remote WS send failed; outcome unknown") from None

    async def _consume(self) -> None:
        try:
            while True:
                frame = await self._ws.recv()
                if isinstance(frame, bytes):
                    continue
                await self.incoming(self.decode(frame))
        except asyncio.CancelledError:
            raise
        except Exception:
            self.fail("Remote WS connection lost; outcome unknown")

    async def _close_wire(self) -> None:
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()

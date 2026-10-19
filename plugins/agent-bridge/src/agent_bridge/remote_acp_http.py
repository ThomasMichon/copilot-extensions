"""Qualified HTTP/2 ACP POST plus connection/per-session SSE transport."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable

import httpx
from credential_relay.http_auth import HttpAuthInjector
from .remote_acp_config import REMOTE_ACP_VERSION, RemoteAcpTarget

from .remote_acp_transport import RemoteTransport, RemoteTransportError


class HttpAcpTransport(RemoteTransport):
    def __init__(
        self, target: RemoteAcpTarget, auth: HttpAuthInjector | None = None, *,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        cookies=None, on_lost=None,
    ) -> None:
        super().__init__(target, auth, on_lost=on_lost)
        # httpcore DEBUG includes raw response headers, including affinity
        # cookies. Application debug logging must never enable wire secrets.
        for namespace in ("httpcore", "hpack", "h2"):
            logging.getLogger(namespace).setLevel(logging.WARNING)
        self._client_factory = client_factory or (
            lambda: httpx.AsyncClient(
                http2=True, follow_redirects=False, trust_env=False,
                timeout=httpx.Timeout(target.timeout_seconds),
            )
        )
        self._client = self._client_factory()
        if cookies is not None:
            self._client.cookies.update(cookies)
        self._stream_clients: set[httpx.AsyncClient] = set()
        self._connection_id: str | None = None
        self._streams: dict[str | None, asyncio.Event] = {}
        self._send_lock = asyncio.Lock()
        self.cookies = self._client.cookies

    async def _request(
        self, method: str, headers: dict[str, str], content: bytes | None = None,
        *, client: httpx.AsyncClient | None = None,
    ) -> httpx.Response:
        client = client if client is not None else self._client
        for attempt in range(2):
            auth = await self.headers()
            version_header = (
                {"Acp-Protocol-Version": str(REMOTE_ACP_VERSION)} if self._connection_id else {}
            )
            request = client.build_request(
                method, self.target.endpoint,
                headers={"Accept-Encoding": "identity", **version_header, **headers, **auth},
                content=content,
                timeout=httpx.Timeout(
                    self.target.timeout_seconds,
                    read=self.target.idle_seconds if method == "GET" else self.target.timeout_seconds,
                ),
            )
            try:
                response = await asyncio.wait_for(
                    client.send(request, stream=True, follow_redirects=False),
                    self.target.timeout_seconds,
                )
            except Exception as exc:
                raise RemoteTransportError(
                    f"Remote HTTP request failed ({type(exc).__name__}); outcome unknown",
                ) from None
            if response.status_code == 401 and attempt == 0 and self.auth is not None:
                await response.aclose()
                await self.refresh_auth()
                continue
            if response.http_version != "HTTP/2":
                await response.aclose()
                raise RemoteTransportError("Remote ACP requires HTTP/2")
            if response.headers.get("content-encoding", "identity") != "identity":
                await response.aclose()
                raise RemoteTransportError("Remote ACP content encoding refused")
            if response.status_code not in {200, 202}:
                status = response.status_code
                await response.aclose()
                raise RemoteTransportError(f"Remote HTTP refused ({status})")
            return response
        raise RemoteTransportError("Remote authentication refused")

    async def _body(self, response: httpx.Response) -> bytes:
        body = bytearray()
        async for chunk in response.aiter_raw():
            body.extend(chunk)
            if len(body) > self.target.max_frame_bytes:
                raise RemoteTransportError("Remote HTTP body limit exceeded")
        return bytes(body)

    async def send(self, message: dict) -> None:
        # POST requests must not serialize the wait for their asynchronous RPC
        # result: server callbacks have to POST while a prompt remains pending.
        async with self._send_lock:
            try:
                raw = self.encode(message)
                sid = self.outgoing_session(message)
                initialize = message.get("method") == "initialize"
                headers = {"Content-Type": "application/json"}
                if not initialize:
                    if self._connection_id is None:
                        raise RemoteTransportError("Remote ACP is not initialized")
                    headers["Acp-Connection-Id"] = self._connection_id
                if sid:
                    headers["Acp-Session-Id"] = sid
                response = await self._request("POST", headers, raw)
                try:
                    body = await asyncio.wait_for(
                        self._body(response), self.target.timeout_seconds,
                    )
                    if initialize:
                        if (
                            response.status_code != 200
                            or response.headers.get("content-type", "").split(";")[0] != "application/json"
                        ):
                            raise RemoteTransportError("Invalid remote initialize status")
                        connection_id = response.headers.get("Acp-Connection-Id", "")
                        if (
                            not connection_id or len(connection_id) > 1024
                            or any(c in connection_id for c in "\r\n")
                        ):
                            raise RemoteTransportError("Missing remote connection identity")
                        self._connection_id = connection_id
                        await self._open_stream(None)
                        await self.incoming(self.decode(body))
                    elif response.status_code != 202 or body.strip():
                        raise RemoteTransportError("Remote ACP POST must return empty 202")
                finally:
                    await response.aclose()
            except asyncio.CancelledError:
                self.fail("Remote HTTP send cancelled; outcome unknown")
                raise
            except Exception as exc:
                reason = (
                    str(exc) if isinstance(exc, RemoteTransportError)
                    else f"Remote HTTP send failed ({type(exc).__name__}); outcome unknown"
                )
                self.fail(reason)
                raise RemoteTransportError(reason) from None

    async def session_ready(self, session_id: str) -> None:
        if len(self.sessions) > self.target.max_queue_messages:
            raise RemoteTransportError("Remote session limit exceeded")
        await self._open_stream(session_id)

    async def _open_stream(self, session_id: str | None) -> None:
        if self.closed or self._ended.is_set():
            raise RemoteTransportError("Remote ACP connection closed during stream setup")
        if session_id in self._streams:
            return
        ready = self._streams[session_id] = asyncio.Event()
        self.task(self._consume(session_id, ready))
        await asyncio.wait_for(ready.wait(), self.target.timeout_seconds)
        if self.closed or self._ended.is_set():
            raise RemoteTransportError("Remote ACP connection closed during stream setup")
        if self._failure:
            raise self._failure

    async def _consume(self, session_id, ready) -> None:
        try:
            async with contextlib.AsyncExitStack() as resources:
                client = self._client_factory()
                if client is self._client or client in self._stream_clients or client.is_closed:
                    raise RemoteTransportError("Remote SSE client factory must return a fresh client")
                self._stream_clients.add(client)
                resources.push_async_callback(self._release_stream_client, client)
                # Quiet SSE readers must not hold the HTTP/2 pool's read lock
                # over a completed control response or another session stream.
                client.cookies = self.cookies.jar
                headers = {
                    "Accept": "text/event-stream",
                    "Acp-Connection-Id": self._connection_id,
                }
                if session_id:
                    headers["Acp-Session-Id"] = session_id
                response = await self._request("GET", headers, client=client)
                resources.push_async_callback(response.aclose)
                if (
                    response.status_code != 200
                    or response.headers.get("content-type", "").split(";")[0] != "text/event-stream"
                ):
                    raise RemoteTransportError("Invalid remote SSE response")
                ready.set()
                await self._consume_response(response, session_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.fail(f"Remote SSE stream lost ({type(exc).__name__}); outcome unknown")
        finally:
            ready.set()
            if not self.closed:
                self.fail("Remote SSE stream lost; outcome unknown")

    async def _consume_response(self, response: httpx.Response, session_id: str | None) -> None:
        iterator = response.aiter_raw().__aiter__()
        buffer = b""
        lines: list[bytes] = []
        size = 0
        previous_cr = False
        while not self.closed:
            chunk = await asyncio.wait_for(iterator.__anext__(), self.target.idle_seconds)
            if self.closed:
                return
            if not chunk:
                continue
            if previous_cr and chunk.startswith(b"\n"):
                chunk = chunk[1:]
            previous_cr = chunk.endswith(b"\r")
            chunk = chunk.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                size += len(line) + 1
                if size > self.target.max_frame_bytes:
                    raise RemoteTransportError("Remote SSE frame limit exceeded")
                if not line:
                    if lines:
                        await self.incoming(self.decode(b"\n".join(lines)), session_id)
                    lines, size = [], 0
                elif line.startswith(b"data:"):
                    lines.append(line[5:].removeprefix(b" "))
            if len(buffer) + size > self.target.max_frame_bytes:
                raise RemoteTransportError("Remote SSE frame limit exceeded")

    async def _release_stream_client(self, client: httpx.AsyncClient) -> None:
        await client.aclose()
        self._stream_clients.discard(client)

    async def _close_wire(self) -> None:
        async with contextlib.AsyncExitStack() as resources:
            resources.push_async_callback(self._client.aclose)
            for client in tuple(self._stream_clients):
                resources.push_async_callback(self._release_stream_client, client)
            if self._connection_id:
                response = await self._request(
                    "DELETE", {"Acp-Connection-Id": self._connection_id},
                )
                try:
                    if response.status_code != 202:
                        raise RemoteTransportError("Remote close refused")
                finally:
                    await response.aclose()

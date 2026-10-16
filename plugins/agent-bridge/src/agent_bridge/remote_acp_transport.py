"""Bounded message transport, consumed by the public ACP Transport interface."""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from typing import Any
from collections.abc import Callable

from credential_relay.http_auth import HttpAuthContext, HttpAuthInjector

from .remote_acp_config import RemoteAcpTarget


class RemoteTransportError(ConnectionError):
    """Sanitized transport refusal; never carries remote response/secret text."""


def rpc_key(value: object) -> tuple[type, object]:
    if type(value) not in (str, int):
        raise RemoteTransportError("Invalid remote RPC identity")
    return type(value), value


class RemoteTransport:
    def __init__(
        self, target: RemoteAcpTarget, auth: HttpAuthInjector | None = None,
        *, on_lost: Any = None,
    ) -> None:
        self.target = target
        self.generation = uuid.uuid4().hex
        self.context = HttpAuthContext(
            target.origin, target.auth.identity, self.generation,
            resource=target.auth.resource, scope=target.auth.scope,
        )
        self.auth = auth
        self.on_lost = on_lost
        self.on_request_completed: Callable[[object], None] | None = None
        self.closed = False
        self._failure: RemoteTransportError | None = None
        self._inbox: asyncio.Queue[dict] = asyncio.Queue(target.max_queue_messages)
        self._ended = asyncio.Event()
        self._tasks: set[asyncio.Task] = set()
        self._callbacks: dict[tuple[type, object], str | None] = {}
        self._pending: dict[tuple[type, object], tuple[str, str | None]] = {}
        self._deadlines: dict[tuple[type, object], float] = {}
        self._deadline_changed = asyncio.Event()
        self._watching = False
        self._close_task: asyncio.Task | None = None
        self.sessions: set[str] = set()
        self._adopting: dict[str, asyncio.Event] = {}

    async def send(self, message: dict[str, Any]) -> None:
        raise NotImplementedError("Select an explicit HTTP or WebSocket transport")

    async def headers(self) -> dict[str, str]:
        if self.auth is None:
            if self.target.auth.source != "none":
                raise RemoteTransportError("Remote credential provider unavailable")
            return {}
        try:
            result = dict(await asyncio.wait_for(
                self.auth.headers(self.context), self.target.timeout_seconds,
            ))
            if any(
                not k or any(c in k for c in "\r\n: ")
                or any(c in v for c in "\r\n")
                or k.lower() in {
                    "host", "cookie", "acp-connection-id", "acp-session-id",
                    "content-type", "accept", "connection", "upgrade",
                    "acp-protocol-version",
                }
                for k, v in result.items()
            ):
                raise ValueError
            return result
        except Exception:
            raise RemoteTransportError("Remote credential acquisition failed") from None

    async def refresh_auth(self) -> None:
        try:
            await asyncio.wait_for(
                self.auth.invalidate(self.context), self.target.timeout_seconds,
            )
        except Exception:
            raise RemoteTransportError("Remote authentication refresh failed") from None

    def encode(self, message: dict) -> bytes:
        try:
            raw = json.dumps(message, separators=(",", ":"), allow_nan=False).encode()
        except (TypeError, ValueError):
            raise RemoteTransportError("Invalid outbound ACP message") from None
        if len(raw) > self.target.max_frame_bytes:
            raise RemoteTransportError("Remote ACP frame limit exceeded")
        return raw

    def decode(self, raw: bytes | str) -> dict:
        if len(raw.encode() if isinstance(raw, str) else raw) > self.target.max_frame_bytes:
            raise RemoteTransportError("Remote ACP frame limit exceeded")
        try:
            result = json.loads(raw)
            if not isinstance(result, dict) or result.get("jsonrpc") != "2.0":
                raise ValueError
            return result
        except (ValueError, UnicodeError):
            raise RemoteTransportError("Malformed remote ACP frame") from None

    def outgoing_session(self, message: dict) -> str | None:
        if self.closed or self._ended.is_set():
            raise RemoteTransportError("Remote ACP connection closed")
        method = message.get("method")
        params = message.get("params") or {}
        sid = params.get("sessionId")
        if method is None:
            key = rpc_key(message.get("id"))
            if key not in self._callbacks:
                raise RemoteTransportError("Unknown remote callback response")
            return self._callbacks.pop(key)
        if method.startswith("session/") and method not in {"session/new", "session/list"}:
            if not isinstance(sid, str) or not sid:
                raise RemoteTransportError("Missing remote session identity")
            if method != "session/load" and sid not in self.sessions:
                raise RemoteTransportError("Unknown remote session")
        if "id" in message:
            key = rpc_key(message["id"])
            if key in self._pending or len(self._pending) >= self.target.max_queue_messages:
                raise RemoteTransportError("Remote ACP request limit exceeded")
            self._pending[key] = method, sid
            self._deadlines[key] = asyncio.get_running_loop().time() + self.target.rpc_timeout_seconds
            self._deadline_changed.set()
            if not self._watching:
                self._watching = True
                self.task(self._watch_requests())
        return sid

    async def incoming(self, message: dict, stream_session: str | None = None) -> None:
        sid = (message.get("params") or {}).get("sessionId")
        request_scoped = False
        if sid is None and message.get("method") == "elicitation/create":
            request_id = (message.get("params") or {}).get("requestId")
            request_scoped = self.has_request(request_id)
            sid = self.request_session(request_id)
        if stream_session is not None and sid is not None and sid != stream_session:
            raise RemoteTransportError("Remote ACP session routing mismatch")
        if sid in self._adopting and "method" in message:
            await asyncio.wait_for(self._adopting[sid].wait(), self.target.timeout_seconds)
        if "method" in message and "id" in message:
            key = rpc_key(message["id"])
            if not request_scoped and (not isinstance(sid, str) or sid not in self.sessions):
                raise RemoteTransportError("Unknown remote callback session")
            if len(self._callbacks) >= self.target.max_queue_messages or key in self._callbacks:
                raise RemoteTransportError("Remote ACP callback limit exceeded")
            self._callbacks[key] = sid
        elif "id" in message:
            key = rpc_key(message["id"])
            request = self._pending.pop(key, None)
            self._deadlines.pop(key, None)
            self._deadline_changed.set()
            if request is None:
                raise RemoteTransportError("Unexpected remote ACP response")
            method, requested_sid = request
            if "error" in message:
                code = (message.get("error") or {}).get("code", -32603)
                message = {
                    "jsonrpc": "2.0", "id": message["id"],
                    "error": {"code": code if type(code) is int else -32603,
                              "message": "Remote ACP request refused"},
                }
            if method in {"session/new", "session/load"} and "result" in message:
                sid = message["result"].get("sessionId") or requested_sid
                if not isinstance(sid, str) or not sid:
                    raise RemoteTransportError("Missing remote session identity")
                if sid not in self.sessions and len(self.sessions) >= self.target.max_queue_messages:
                    raise RemoteTransportError("Remote ACP session limit exceeded")
                self.sessions.add(sid)
                self._adopting[sid] = asyncio.Event()
            elif stream_session is not None and requested_sid != stream_session:
                raise RemoteTransportError("Remote response session mismatch")
            if self.on_request_completed is not None:
                self.on_request_completed(message["id"])
        elif sid is not None and sid not in self.sessions:
            # Load replay is historical and may precede the load response.
            if not any(m == "session/load" and s == sid for m, s in self._pending.values()):
                raise RemoteTransportError("Unknown remote update session")
        try:
            await asyncio.wait_for(self._inbox.put(message), self.target.timeout_seconds)
        except asyncio.TimeoutError:
            raise RemoteTransportError("Remote ACP receive queue backpressure deadline exceeded") from None

    async def session_ready(self, session_id: str) -> None:
        pass

    async def adopt_session(self, session_id: str) -> None:
        event = self._adopting.pop(session_id, None)
        if event is not None:
            event.set()
        await self.session_ready(session_id)

    def request_session(self, request_id: object) -> str | None:
        if type(request_id) not in (str, int):
            return None
        request = self._pending.get(rpc_key(request_id))
        return request[1] if request else None

    def has_request(self, request_id: object) -> bool:
        return type(request_id) in (str, int) and rpc_key(request_id) in self._pending

    async def _watch_requests(self) -> None:
        while not self.closed and not self._ended.is_set():
            self._deadline_changed.clear()
            deadline = min(self._deadlines.values(), default=None)
            if deadline is None:
                await self._deadline_changed.wait()
                continue
            remaining = max(0, deadline - asyncio.get_running_loop().time())
            try:
                await asyncio.wait_for(self._deadline_changed.wait(), remaining)
            except asyncio.TimeoutError:
                self.fail("Remote ACP RPC deadline exceeded; outcome unknown")
                return

    def fail(self, reason: str) -> None:
        if self._ended.is_set():
            return
        self._failure = RemoteTransportError(reason)
        self._ended.set()
        self._callbacks.clear()
        self._pending.clear()
        self._deadlines.clear()
        self._deadline_changed.set()
        self._begin_close()
        if self.on_lost:
            self.on_lost()

    async def receive(self) -> dict | None:
        if self._ended.is_set():
            if self._failure:
                raise self._failure
            return None
        get = asyncio.create_task(self._inbox.get())
        end = asyncio.create_task(self._ended.wait())
        try:
            await asyncio.wait({get, end}, return_when=asyncio.FIRST_COMPLETED)
            if self._ended.is_set():
                if self._failure:
                    raise self._failure
                return None
            return get.result()
        finally:
            for task in (get, end):
                task.cancel()
            await asyncio.gather(get, end, return_exceptions=True)

    def task(self, coroutine: Any) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _begin_close(self) -> asyncio.Task:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close_resources())
            self._close_task.add_done_callback(
                lambda task: task.exception() if not task.cancelled() else None,
            )
        return self._close_task

    async def close(self) -> None:
        await asyncio.shield(self._begin_close())

    async def _close_resources(self) -> None:
        self.closed = True
        self._ended.set()
        self._callbacks.clear()
        self._pending.clear()
        self._deadlines.clear()
        self._deadline_changed.set()
        tasks = [task for task in self._tasks if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        if tasks:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(
                    asyncio.gather(*tasks, return_exceptions=True), self.target.timeout_seconds,
                )
        await asyncio.wait_for(self._close_wire(), self.target.timeout_seconds)

    async def _close_wire(self) -> None:
        pass

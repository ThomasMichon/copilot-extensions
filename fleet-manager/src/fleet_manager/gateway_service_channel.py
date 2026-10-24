"""Bounded service wire protocol and generation-owned Gateway request queues.

One response chunk is in flight per request. ACK follows client write/drain,
not WebSocket receipt. A channel object owns its correlation map exclusively.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import re
import secrets
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from aiohttp import ClientWebSocketResponse, WSMsgType, web

from . import planning
from .gateway_service_policy import CallerGrant, ServiceAuthority, ServiceGrant
from .gateway_service_context import ServicePolicyError
from .runtime_identity import CLOCK_SKEW, MAX_REPLAYS, PROOF_AGE

FRAME_BYTES = 65536
BODY_BYTES = 32768
CHUNK_BYTES = 16384
RESPONSE_BYTES = 8 * 1024 * 1024
QUEUE_SIZE = 8
CHANNEL_REQUESTS = 8
GLOBAL_REQUESTS = 32
DEADLINE = 60
SEND_TIMEOUT = 5
CONTROL_BURST = 32
CONTROL_RATE = 16
REQUEST_BURST = 128
REQUEST_RATE = (MAX_REPLAYS - REQUEST_BURST) // (PROOF_AGE + 2 * CLOCK_SKEW + 1)
REQUEST_HEADERS = frozenset({
    "accept", "accept-language", "content-type", "if-none-match", "if-modified-since",
    "if-match", "if-unmodified-since",
})
RESPONSE_HEADERS = frozenset({
    "content-type", "cache-control", "etag", "last-modified", "content-disposition",
    "vary", "date", "age", "expires",
})


class TransportError(ValueError):
    """Static refusal without transport, credential or backend values."""

    def __init__(self, code: str, status: int = 502) -> None:
        self.code, self.status = code, status
        super().__init__(code)


class ServerSocket(web.WebSocketResponse):
    async def close(self, *, code: int = 1000, message: bytes = b"",
                    drain: bool = True) -> bool:
        return await asyncio.wait_for(
            super().close(code=code, message=message, drain=drain), SEND_TIMEOUT)


class ClientSocket(ClientWebSocketResponse):
    async def close(self, *, code: int = 1000, message: bytes = b"") -> bool:
        return await asyncio.wait_for(
            super().close(code=code, message=message), SEND_TIMEOUT)


class ValueFreeLogger(logging.LoggerAdapter):
    """aiohttp parser/handler diagnostics must not render supplied byte values."""

    def log(self, level: int, msg: object, *args: object, **kwargs: object) -> None:
        self.logger.log(level, "service-http-error code=parser-or-handler-failed")


def check(condition: bool, code: str = "invalid-frame", status: int = 400) -> None:
    if not condition:
        raise TransportError(code, status)


def origin(value: object, *, tls: bool = True) -> str:
    check(isinstance(value, str) and 0 < len(value) <= 2048, "invalid-origin")
    assert isinstance(value, str)
    try:
        parsed = urlsplit(value)
        check(parsed.scheme in (("https",) if tls else ("http", "https"))
              and bool(parsed.hostname) and parsed.username is None
              and parsed.password is None and parsed.path == ""
              and not parsed.query and not parsed.fragment
              and "?" not in value and "#" not in value
              and "\\" not in value and "%" not in value
              and all(32 < ord(char) < 127 for char in value), "invalid-origin")
        check(parsed.port is None or 0 <= parsed.port <= 65535, "invalid-origin")
    except ValueError:
        raise TransportError("invalid-origin", 400) from None
    return value


def target(value: object) -> tuple[str, str]:
    """Canonical root-relative path plus opaque bounded query; never an origin."""
    check(isinstance(value, str) and 0 < len(value) <= 2048, "invalid-path")
    assert isinstance(value, str)
    path, mark, query = value.partition("?")
    check(re.fullmatch(r"/[A-Za-z0-9._~/-]*", path) is not None
          and "//" not in path and not any(p in (".", "..") for p in path.split("/"))
          and all(32 < ord(c) < 127 and c not in "#\\" for c in query),
          "invalid-path")
    return path, mark + query


def headers(items: Iterable[tuple[str, str]], allowed: frozenset[str],
            *, strict: bool = False) -> dict[str, str]:
    pairs = list(items)
    check(len(pairs) <= 64 and sum(len(k) + len(v) for k, v in pairs) <= 16384,
          "invalid-headers")
    nominated = {part.strip().lower() for k, v in pairs if k.lower() == "connection"
                 for part in v.split(",")}
    result: dict[str, str] = {}
    for key, value in pairs:
        name = key.lower()
        check(not (name in allowed and name in nominated and (
            name.startswith("if-") or name in {
                "cache-control", "etag", "last-modified", "vary", "date", "age", "expires",
            })),
              "invalid-headers")
        if name not in allowed or name in nominated:
            check(not strict, "invalid-headers")
            continue
        check(name not in result and len(value) <= 4096
              and all(32 <= ord(c) < 127 for c in value), "invalid-headers")
        result[name] = value
    return result


def wire_headers(value: object, allowed: frozenset[str]) -> dict[str, str]:
    check(isinstance(value, dict), "invalid-headers")
    assert isinstance(value, dict)
    check(all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()),
          "invalid-headers")
    return headers(value.items(), allowed, strict=True)


def response_status(value: object) -> None:
    check(type(value) is int and 200 <= value <= 599, "invalid-backend-response", 502)
    assert isinstance(value, int)
    check(not 300 <= value <= 399 or value == 304, "invalid-backend-response", 502)


def unpack(raw: object) -> dict:
    check(isinstance(raw, str) and len(raw.encode("utf-8")) <= FRAME_BYTES)
    assert isinstance(raw, str)
    try:
        value = planning.decode(raw.encode("utf-8"))
    except planning.PlanError:
        raise TransportError("invalid-frame", 400) from None
    check(isinstance(value, dict))
    return value


def shape(value: dict, names: set[str]) -> None:
    check(set(value) == names)


def binary(value: object, maximum: int) -> bytes:
    check(isinstance(value, str) and len(value) <= 4 * ((maximum + 2) // 3))
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise TransportError("invalid-frame", 400) from None
    check(len(decoded) <= maximum)
    return decoded


def encode_frame(value: dict) -> str:
    raw = planning.canonical(value).decode("utf-8")
    check(len(raw.encode("utf-8")) <= FRAME_BYTES, "frame-limit", 413)
    return raw


async def send_encoded(
    ws: web.WebSocketResponse | ClientWebSocketResponse, raw: str
) -> None:
    await asyncio.wait_for(ws.send_str(raw), SEND_TIMEOUT)


async def send(
    ws: web.WebSocketResponse | ClientWebSocketResponse, value: dict
) -> None:
    await send_encoded(ws, encode_frame(value))


async def close_socket(
    ws: web.WebSocketResponse | ClientWebSocketResponse, *,
    code: int = 1001, message: bytes = b"",
) -> None:
    try:
        await asyncio.wait_for(ws.close(code=code, message=message), SEND_TIMEOUT)
    except asyncio.TimeoutError:
        logging.getLogger(__name__).warning("service-channel-closed code=close-timeout")


async def no_store_response(_request: web.Request, response: web.StreamResponse) -> None:
    response.headers["Cache-Control"] = "no-store"


@dataclass(eq=False)
class Pending:
    caller: CallerGrant
    path: str
    method: str
    target_uri: str
    id: str = field(default_factory=lambda: secrets.token_hex(24))
    queue: asyncio.Queue[dict | TransportError] = field(
        default_factory=lambda: asyncio.Queue(QUEUE_SIZE))
    phase: str = "initial"
    sequence: int = 0
    total: int = 0
    dispatched: bool = False
    unacknowledged: bool = False

    def fail(self, code: str, status: int = 502) -> None:
        while not self.queue.empty():
            self.queue.get_nowait()
        self.phase = "failed"
        self.queue.put_nowait(TransportError(code, status))


class Channel:
    """An exact grant/generation owns every accepted frame and cancellation."""

    def __init__(self, ws: web.WebSocketResponse, authority: ServiceAuthority,
                 grant: ServiceGrant, *, caller_context: bool = False) -> None:
        self.ws, self.authority, self.grant = ws, authority, grant
        self.caller_context = caller_context
        self.pending: dict[str, Pending] = {}
        self.retired: deque[str] = deque(maxlen=64)
        self.closed = False

    def admit(self, pending: Pending) -> None:
        self.authority.revalidate_service(self.grant)
        check(not self.closed, "service-unavailable", 503)
        check(len(self.pending) < CHANNEL_REQUESTS, "request-limit", 429)
        self.pending[pending.id] = pending

    async def cancel(self, pending: Pending) -> None:
        if self.pending.pop(pending.id, None) is not pending:
            return
        self.retired.append(pending.id)
        if not self.ws.closed:
            try:
                await send(self.ws, {"type": "cancel", "id": pending.id})
            except (OSError, asyncio.TimeoutError):
                await self.close("channel-disconnected")

    async def close(self, code: str = "channel-disconnected") -> None:
        if self.closed:
            return
        self.closed = True
        for pending in self.pending.values():
            pending.fail("outcome-unknown" if pending.dispatched and pending.method
                         not in ("GET", "HEAD", "OPTIONS") else code)
        self.authority.release_service(self.grant)
        await close_socket(self.ws, code=1001, message=code.encode("ascii"))

    def receive(self, value: dict) -> None:
        kind, correlation = value.get("type"), value.get("id")
        check(isinstance(correlation, str) and len(correlation) == 48)
        pending = self.pending.get(correlation)
        if pending is None:
            check(correlation in self.retired, "unknown-correlation")
            return
        self.authority.revalidate_service(self.grant)
        self.authority.revalidate_caller(pending.caller)
        if kind == "headers":
            shape(value, {"type", "id", "status", "headers"})
            check(pending.phase == "initial")
            response_status(value["status"])
            wire_headers(value["headers"], RESPONSE_HEADERS)
            pending.phase = "body"
        elif kind == "chunk":
            shape(value, {"type", "id", "seq", "data"})
            check(pending.phase == "body" and not pending.unacknowledged
                  and type(value["seq"]) is int
                  and value["seq"] == pending.sequence + 1)
            data = binary(value["data"], CHUNK_BYTES)
            check(bool(data) and pending.total + len(data) <= RESPONSE_BYTES,
                  "response-limit", 502)
            pending.sequence += 1
            pending.total += len(data)
            pending.unacknowledged = True
        elif kind == "end":
            shape(value, {"type", "id"})
            check(pending.phase == "body" and not pending.unacknowledged)
            pending.phase = "ended"
        elif kind == "error":
            shape(value, {"type", "id", "code"})
            check(value["code"] in ("backend-unavailable", "backend-timeout",
                                     "response-limit", "invalid-backend-response",
                                     "caller-context-expired", "request-disconnected"))
            pending.fail("outcome-unknown" if pending.method not in
                         ("GET", "HEAD", "OPTIONS") else value["code"],
                         504 if value["code"] == "backend-timeout" else 502)
            return
        else:
            raise TransportError("invalid-frame")
        check(not pending.queue.full(), "response-window")
        pending.queue.put_nowait(value)

    async def run(self) -> None:
        try:
            async for message in self.ws:
                check(message.type == WSMsgType.TEXT)
                value = unpack(message.data)
                try:
                    self.receive(value)
                except ServicePolicyError:
                    correlation = value.get("id")
                    pending = self.pending.get(correlation) if isinstance(correlation, str) else None
                    if pending:
                        pending.fail("authorization-withdrawn", 403)
                        await self.cancel(pending)
                    self.authority.revalidate_service(self.grant)
        finally:
            await self.close()

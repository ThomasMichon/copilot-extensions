"""Opt-in channel-derived caller context for bounded in-process service handlers.

These values are not credentials or offline permits. Only the connector's
authenticated Gateway channel supplies them; an HTTP proxy never forwards them.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from threading import Event
from typing import TYPE_CHECKING

from aiohttp import ClientWebSocketResponse

from .gateway_service_channel import (
    BODY_BYTES, CHUNK_BYTES, RESPONSE_HEADERS, TransportError,
    check, headers, response_status, send, shape,
)
from .gateway_service_context import ServicePolicyError, identifier

PROFILE = "caller-context-v1"

if TYPE_CHECKING:
    from .gateway_service_connector import Exchange


class RequestDisconnected(TransportError):
    """Loss of request custody is not proof that its mutation did not commit."""

    def __init__(self) -> None:
        super().__init__("request-disconnected", 503)


class RequestLifetime:
    """Connector-local cancellation signal, safe to check from a disk worker.

    This is not a credential, policy snapshot or atomic external-effect fence.
    A close racing an already-started effect leaves its outcome uncertain.
    """

    def __init__(self) -> None:
        self._closed = Event()

    def close(self) -> None:
        self._closed.set()

    def check(self) -> None:
        if self._closed.is_set():
            raise RequestDisconnected()


@dataclass(frozen=True)
class CallerContext:
    fleet: str
    gateway: str
    slot: str
    principal: str
    expires_at: int

    @classmethod
    def parse(cls, value: object) -> CallerContext:
        check(isinstance(value, dict), "invalid-caller-context")
        assert isinstance(value, dict)
        shape(value, {"fleet", "gateway", "slot", "principal", "expires_at"})
        try:
            names = [identifier(value[name]) for name in ("fleet", "gateway", "slot", "principal")]
        except ServicePolicyError:
            raise TransportError("invalid-caller-context") from None
        expires = value["expires_at"]
        check(type(expires) is int and 0 < expires < 2**53, "invalid-caller-context")
        return cls(*names, expires)


@dataclass(frozen=True)
class ServiceRequest:
    context: CallerContext
    method: str
    target: str
    headers: tuple[tuple[str, str], ...]
    body: bytes
    lifetime: RequestLifetime = field(default_factory=RequestLifetime, repr=False, compare=False)


@dataclass(frozen=True)
class ServiceResponse:
    status: int
    headers: tuple[tuple[str, str], ...] = ()
    body: bytes = b""

    def __post_init__(self) -> None:
        response_status(self.status)
        check(type(self.body) is bytes and len(self.body) <= BODY_BYTES, "response-limit")
        check(type(self.headers) is tuple and all(
            type(pair) is tuple and len(pair) == 2
            and all(isinstance(item, str) for item in pair) for pair in self.headers),
            "invalid-headers")
        headers(self.headers, RESPONSE_HEADERS, strict=True)


@dataclass(frozen=True)
class HandlerBinding:
    """Trusted deployment input, never selected by an incoming request.

    The handler maps the admitted principal to its own account/resource policy.
    It must cooperate with task cancellation. Current Gateway checks and channel
    cancellation are not an atomic fence for an external effect.
    """

    fleet: str
    gateway: str
    handle: Callable[[ServiceRequest], Awaitable[ServiceResponse]]

    def __post_init__(self) -> None:
        identifier(self.fleet)
        identifier(self.gateway)
        check(callable(self.handle), "invalid-handler")

    def context(self, value: object, slot: str) -> CallerContext:
        context = CallerContext.parse(value)
        check((context.fleet, context.gateway, context.slot)
              == (self.fleet, self.gateway, slot), "caller-context-owner-mismatch")
        return context


async def respond(
    ws: ClientWebSocketResponse, exchange: Exchange,
    binding: HandlerBinding, request: ServiceRequest,
) -> None:
    """One bounded response, using the same channel ACK/backpressure contract."""
    request.lifetime.check()
    check(request.context.expires_at > time.time(), "caller-context-expired")
    try:
        response = await binding.handle(request)
    except (TransportError, OSError, asyncio.TimeoutError):
        raise
    except Exception:
        logging.getLogger(__name__).error("service-handler-refused code=handler-failed")
        raise TransportError("handler-failed") from None
    request.lifetime.check()
    check(request.context.expires_at > time.time(), "caller-context-expired")
    check(type(response) is ServiceResponse, "invalid-backend-response")
    response_status(response.status)
    check(type(response.body) is bytes and len(response.body) <= BODY_BYTES,
          "response-limit")
    selected = headers(response.headers, RESPONSE_HEADERS, strict=True)
    await send(ws, {"type": "headers", "id": exchange.id,
                    "status": response.status, "headers": selected})
    for offset in range(0, len(response.body), CHUNK_BYTES):
        request.lifetime.check()
        check(request.context.expires_at > time.time(), "caller-context-expired")
        exchange.sequence += 1
        exchange.ack = asyncio.get_running_loop().create_future()
        await send(ws, {
            "type": "chunk", "id": exchange.id, "seq": exchange.sequence,
            "data": base64.b64encode(response.body[offset:offset + CHUNK_BYTES]).decode("ascii"),
        })
        await exchange.ack
    request.lifetime.check()
    check(request.context.expires_at > time.time(), "caller-context-expired")
    await send(ws, {"type": "end", "id": exchange.id})

"""Outbound SDK with one deployment-selected HTTP backend or bounded handler.

Credentials come from an embedding callback, never argv/environment/config data.
Reconnect creates a new admission; requests from a lost channel are never retried.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import ssl
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import aiohttp
from yarl import URL

from .gateway_auth import make_proof
from .gateway_service_channel import (
    BODY_BYTES, CHANNEL_REQUESTS, CHUNK_BYTES, ClientSocket, DEADLINE, FRAME_BYTES,
    REQUEST_HEADERS, RESPONSE_BYTES, RESPONSE_HEADERS, TransportError,
    binary, check, close_socket, headers, origin, response_status, send, shape, target,
    unpack, wire_headers,
)
from .gateway_service_context import METHODS
from .gateway_service_handler import (
    PROFILE, HandlerBinding, ServiceRequest, respond,
)

LOG = logging.getLogger(__name__)
RequestSpec = tuple[str, str, dict[str, str], bytes, float, ServiceRequest | None]

def verified_tls(ca: str | None) -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=ca)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


@asynccontextmanager
async def channel_socket(
    session: aiohttp.ClientSession, uri: str, tls: ssl.SSLContext,
) -> AsyncIterator[aiohttp.ClientWebSocketResponse]:
    async with session.ws_connect(
        uri, ssl=tls, heartbeat=10, max_msg_size=FRAME_BYTES, compress=0,
        autoclose=False, timeout=aiohttp.ClientWSTimeout(ws_close=2),
    ) as ws:
        try:
            yield ws
        finally:
            await close_socket(ws)


@dataclass(eq=False)
class Exchange:
    id: str
    task: asyncio.Task[None] | None = None
    ack: asyncio.Future[None] | None = None
    sequence: int = 0
    request: ServiceRequest | None = None

    def disconnect(self) -> None:
        if self.request is not None:
            self.request.lifetime.close()


class ServiceConnector:
    """One explicit service assignment and backend; no issuer or provider login.

    ``access_token_callback`` is async and returns a current token. ``proof_key``
    is an externally provisioned P-256 private key accepted by make_proof.
    ``gateway_ca``/``backend_ca`` are optional explicit CA file references;
    omission uses platform trust, never disabled verification.
    Select exactly one of ``backend_origin`` and ``handler``. Only a handler
    negotiates channel-derived caller context; no identity headers are added
    to the HTTP backend path.
    """

    def __init__(self, *, slot: str, gateway_origin: str, backend_origin: str | None = None,
                 access_token_callback: Callable[[], Awaitable[str]], proof_key: Any,
                 gateway_ca: str | None = None, backend_ca: str | None = None,
                 methods: frozenset[str] = METHODS,
                 handler: HandlerBinding | None = None) -> None:
        check(isinstance(slot, str) and 0 < len(slot) <= 128, "invalid-slot")
        check(bool(methods) and methods <= METHODS, "invalid-methods")
        check(callable(access_token_callback), "invalid-token-source")
        check((backend_origin is None) != (handler is None), "select-one-backend")
        check(handler is None or type(handler) is HandlerBinding, "invalid-handler")
        self.slot = slot
        self.gateway = origin(gateway_origin)
        self.backend = origin(backend_origin, tls=False) if backend_origin is not None else None
        self.handler = handler
        self.methods = frozenset(methods)
        self._access, self._key = access_token_callback, proof_key
        self._gateway_tls, self._backend_tls = verified_tls(gateway_ca), verified_tls(backend_ca)
        self.connected = asyncio.Event()
        self.active: dict[str, Exchange] = {}
        self.retired: deque[str] = deque(maxlen=64)
        self._connecting = False
        self._attached_lifetime = 0.0

    async def _json(self, response: aiohttp.ClientResponse) -> dict:
        check(response.status == 200, "gateway-admission-refused", 401)
        raw = bytearray()
        async for chunk in response.content.iter_chunked(4096):
            check(len(raw) + len(chunk) <= 16384, "gateway-response-limit")
            raw.extend(chunk)
        try:
            return unpack(raw.decode("utf-8"))
        except UnicodeError:
            raise TransportError("invalid-frame") from None

    async def _admit(self, session: aiohttp.ClientSession) -> str:
        async with session.get(self.gateway + "/_fleet/challenge",
                               ssl=self._gateway_tls, allow_redirects=False) as response:
            challenge = await self._json(response)
        shape(challenge, {"nonce"})
        access = await asyncio.wait_for(self._access(), 5)
        check(isinstance(access, str) and 0 < len(access) <= 8192, "invalid-token-source")
        uri = self.gateway + "/_fleet/service-admit"
        proof = make_proof(self._key, access, nonce=challenge["nonce"], target_uri=uri)
        async with session.post(uri, json={"slot": self.slot, "access": access, "proof": proof,
                                          **({"profile": PROFILE} if self.handler else {})},
                                ssl=self._gateway_tls, allow_redirects=False) as response:
            ticket = await self._json(response)
        shape(ticket, {"ticket"})
        check(isinstance(ticket["ticket"], str) and len(ticket["ticket"]) == 64)
        return ticket["ticket"]

    def _request(self, value: dict) -> RequestSpec:
        names = {"type", "id", "method", "target", "headers", "body", "timeout"}
        shape(value, names | {"context"} if self.handler else names)
        check(value["type"] == "request"
              and isinstance(value["id"], str) and len(value["id"]) == 48)
        check(isinstance(value["method"], str) and value["method"] in self.methods,
              "method-denied")
        path, query = target(value["target"])
        selected = wire_headers(value["headers"], REQUEST_HEADERS)
        body = binary(value["body"], BODY_BYTES)
        timeout = value["timeout"]
        check(type(timeout) in (int, float) and 0 < timeout <= DEADLINE)
        request = None
        if self.handler:
            context = self.handler.context(value["context"], self.slot)
            request = ServiceRequest(
                context, value["method"], path + query, tuple(selected.items()), body)
        return value["method"], path + query, selected, body, timeout, request

    async def _proxy(self, ws: aiohttp.ClientWebSocketResponse,
                     session: aiohttp.ClientSession,
                     exchange: Exchange, request: RequestSpec) -> None:
        try:
            await self._exchange(ws, session, exchange, request)
        except (aiohttp.ClientError, OSError, asyncio.TimeoutError):
            exchange.disconnect()
            LOG.warning("service-connector-closed code=response-writer-failed")
            await close_socket(ws, code=1011, message=b"response-writer-failed")
        finally:
            self._retire(exchange)

    def _retire(self, exchange: Exchange) -> None:
        exchange.disconnect()
        if self.active.get(exchange.id) is exchange:
            del self.active[exchange.id]
            self.retired.append(exchange.id)

    async def _exchange(self, ws: aiohttp.ClientWebSocketResponse,
                        session: aiohttp.ClientSession,
                        exchange: Exchange, request: RequestSpec) -> None:
        method, path, selected, body, timeout, admitted = request
        try:
            if admitted is not None:
                assert self.handler is not None
                response = respond(ws, exchange, self.handler, admitted)
            else:
                response = self._response(ws, session, exchange, method, path, selected, body)
            response_task = asyncio.create_task(response)
            try:
                done, _ = await asyncio.wait({response_task}, timeout=timeout)
                if not done:
                    raise asyncio.TimeoutError
                await response_task
            finally:
                exchange.disconnect()
                if not response_task.done():
                    response_task.cancel()
                await asyncio.gather(response_task, return_exceptions=True)
        except asyncio.TimeoutError:
            exchange.disconnect()
            await send(ws, {"type": "error", "id": exchange.id, "code": "backend-timeout"})
        except (aiohttp.ClientError, OSError):
            exchange.disconnect()
            await send(ws, {"type": "error", "id": exchange.id, "code": "backend-unavailable"})
        except TransportError as exc:
            exchange.disconnect()
            code = (exc.code if exc.code in {
                "response-limit", "caller-context-expired", "request-disconnected"}
                    else "invalid-backend-response")
            await send(ws, {"type": "error", "id": exchange.id, "code": code})
    async def _response(
        self, ws: aiohttp.ClientWebSocketResponse, session: aiohttp.ClientSession,
        exchange: Exchange, method: str, path: str, selected: dict[str, str], body: bytes,
    ) -> None:
        # encoded=True preserves the checked path/query instead of URL normalization.
        assert self.backend is not None
        async with session.request(
            method, URL(self.backend + path, encoded=True), headers=selected, data=body,
            ssl=self._backend_tls, allow_redirects=False, skip_auto_headers={
                "User-Agent", "Accept", "Accept-Encoding", "Content-Type",
            },
        ) as response:
            response_status(response.status)
            check(response.headers.getall("Content-Encoding", ["identity"]) == ["identity"],
                  "invalid-backend-response")
            selected = headers(response.headers.items(), RESPONSE_HEADERS)
            await send(ws, {"type": "headers", "id": exchange.id,
                            "status": response.status, "headers": selected})
            total = 0
            async for data in response.content.iter_chunked(CHUNK_BYTES):
                total += len(data)
                check(total <= RESPONSE_BYTES, "response-limit")
                exchange.sequence += 1
                exchange.ack = asyncio.get_running_loop().create_future()
                await send(ws, {"type": "chunk", "id": exchange.id, "seq": exchange.sequence,
                                "data": base64.b64encode(data).decode("ascii")})
                await exchange.ack
            await send(ws, {"type": "end", "id": exchange.id})

    async def connect_once(self) -> None:
        """Serve one actual authenticated channel; close every owned request on exit."""
        check(not self._connecting, "connector-already-running")
        self._connecting = True
        self._attached_lifetime = 0.0
        try:
            await self._connect()
        finally:
            self._connecting = False

    async def _connect(self) -> None:
        check(not self.active and not self.connected.is_set(), "connector-already-running")
        timeout = aiohttp.ClientTimeout(total=DEADLINE, connect=5, sock_read=15)
        connector = aiohttp.TCPConnector(limit=CHANNEL_REQUESTS + 2)
        trace = aiohttp.TraceConfig()

        async def refuse_redirect(*args: object) -> None:
            raise TransportError("redirect-refused")

        trace.on_request_redirect.append(refuse_redirect)
        async with aiohttp.ClientSession(
            timeout=timeout, connector=connector, trust_env=False,
            cookie_jar=aiohttp.DummyCookieJar(), auto_decompress=False,
            trace_configs=[trace],
            ws_response_class=ClientSocket,
        ) as session:
            # aiohttp 3 has no public toggle for replaying PUT/DELETE on disconnect.
            session._retry_connection = False
            ticket = await self._admit(session)
            async with channel_socket(
                session, self.gateway + "/_fleet/service-channel", self._gateway_tls,
            ) as ws:
                await send(ws, {"ticket": ticket})
                attached = await asyncio.wait_for(ws.receive(), 5)
                check(attached.type == aiohttp.WSMsgType.TEXT, "channel-refused")
                confirmation = unpack(attached.data)
                check(confirmation == {"type": "attached", **(
                    {"profile": PROFILE} if self.handler else {})}, "channel-refused")
                attached_at = asyncio.get_running_loop().time()
                self.connected.set()
                try:
                    async for message in ws:
                        check(message.type == aiohttp.WSMsgType.TEXT)
                        value = unpack(message.data)
                        kind, correlation = value.get("type"), value.get("id")
                        check(isinstance(correlation, str) and len(correlation) == 48)
                        if kind == "request":
                            request = self._request(value)
                            check(correlation not in self.active and correlation not in self.retired
                                  and len(self.active) < CHANNEL_REQUESTS)
                            exchange = Exchange(correlation, request=request[-1])
                            self.active[correlation] = exchange
                            exchange.task = asyncio.create_task(
                                self._proxy(ws, session, exchange, request))
                        elif kind == "ack":
                            shape(value, {"type", "id", "seq"})
                            exchange = self.active.get(correlation)
                            if exchange is None and correlation in self.retired:
                                continue
                            check(exchange is not None)
                            assert exchange is not None
                            check(type(value["seq"]) is int
                                  and value["seq"] == exchange.sequence
                                  and exchange.ack is not None and not exchange.ack.done())
                            exchange.ack.set_result(None)
                        elif kind == "cancel":
                            shape(value, {"type", "id"})
                            exchange = self.active.get(correlation)
                            if exchange and exchange.task:
                                exchange.disconnect()
                                exchange.task.cancel()
                                await asyncio.gather(exchange.task, return_exceptions=True)
                                self._retire(exchange)
                        else:
                            raise TransportError("invalid-frame")
                finally:
                    self._attached_lifetime = asyncio.get_running_loop().time() - attached_at
                    self.connected.clear()
                    for exchange in self.active.values():
                        exchange.disconnect()
                    tasks = [e.task for e in self.active.values() if e.task is not None]
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    self.active.clear()

    async def run(self, stop: asyncio.Event) -> None:
        """Reconnect at most five consecutive failures, with 0.5..4 second backoff.

        Successful attachment resets the failure budget only after 60 seconds;
        flapping channels cannot create a tight infinite admission loop.
        """
        failures = 0
        while not stop.is_set():
            task, stopped = asyncio.create_task(self.connect_once()), asyncio.create_task(stop.wait())
            try:
                done, _ = await asyncio.wait({task, stopped}, return_when=asyncio.FIRST_COMPLETED)
                if stopped in done:
                    return
                try:
                    await task
                except (aiohttp.ClientError, OSError, asyncio.TimeoutError, TransportError):
                    LOG.warning("service-connector-retry code=channel-unavailable")
                if self._attached_lifetime >= DEADLINE:
                    failures = 0
                failures += 1
                if failures >= 5:
                    raise TransportError("reconnect-exhausted", 503)
                try:
                    await asyncio.wait_for(stop.wait(), min(4, 0.5 * 2 ** (failures - 1)))
                except asyncio.TimeoutError:
                    continue
            finally:
                task.cancel()
                stopped.cancel()
                await asyncio.gather(task, stopped, return_exceptions=True)

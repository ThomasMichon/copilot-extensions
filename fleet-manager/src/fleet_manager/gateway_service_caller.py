"""Finite authenticated service requests; no discovery, retries or background owner."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

import aiohttp
from yarl import URL

from .gateway_auth import make_proof
from .gateway_service_channel import (
    BODY_BYTES, CHANNEL_REQUESTS, DEADLINE, REQUEST_HEADERS, RESPONSE_BYTES,
    RESPONSE_HEADERS, TransportError, check, headers, origin, response_status,
    shape, target, unpack,
)
from .gateway_service_connector import verified_tls
from .gateway_service_context import METHODS


@dataclass(frozen=True)
class ServiceResponse:
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes = field(repr=False)


async def _read(response: aiohttp.ClientResponse, maximum: int) -> bytes:
    check(response.headers.getall("Content-Encoding", ["identity"]) == ["identity"],
          "invalid-gateway-response", 502)
    check(response.method == "HEAD" or response.status == 304
          or response.content_length is None or response.content_length <= maximum,
          "gateway-response-limit", 502)
    raw = bytearray()
    async for chunk in response.content.iter_chunked(16384):
        check(len(raw) + len(chunk) <= maximum, "gateway-response-limit", 502)
        raw.extend(chunk)
    return bytes(raw)


class ServiceCaller:
    """Caller for one explicit canonical HTTPS Gateway, not its static index API.

    The embedding owns the current async token callback, externally provisioned
    P-256 proof key and optional CA file. Each finite operation fetches a fresh
    challenge and token. No credential or challenge is retained between calls.
    Instances are used on one event loop; excess concurrent work is refused,
    never queued. Cancellation propagates; any transmitted mutation may already
    have committed, so recovery must reconcile its original application ID.
    """

    def __init__(self, *, gateway_origin: str,
                 access_token_callback: Callable[[], Awaitable[str]], proof_key: Any,
                 gateway_ca: str | None = None, timeout: float = DEADLINE) -> None:
        self.gateway = origin(gateway_origin)
        check(callable(access_token_callback), "invalid-token-source")
        check(type(timeout) in (int, float) and 0 < timeout <= DEADLINE,
              "invalid-timeout")
        self._access, self._key = access_token_callback, proof_key
        self._tls = verified_tls(gateway_ca)
        self._timeout = timeout
        self._active = 0

    async def request(self, method: str, request_target: str, *,
                      request_headers: Iterable[tuple[str, str]] = (),
                      body: bytes = b"", response_limit: int = RESPONSE_BYTES,
                      ) -> ServiceResponse:
        check(isinstance(method, str) and method in METHODS, "invalid-method")
        path, query = target(request_target)
        selected = headers(request_headers, REQUEST_HEADERS, strict=True)
        check(type(body) is bytes and len(body) <= BODY_BYTES, "request-body-limit", 413)
        check(type(response_limit) is int and 0 < response_limit <= RESPONSE_BYTES,
              "invalid-response-limit")
        check(self._active < CHANNEL_REQUESTS, "request-limit", 429)
        self._active += 1
        attempted = False

        async def exchange() -> ServiceResponse:
            nonlocal attempted
            async with aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=self._tls),
                trust_env=False, cookie_jar=aiohttp.DummyCookieJar(),
                auto_decompress=False, timeout=aiohttp.ClientTimeout(total=self._timeout),
                skip_auto_headers={"User-Agent", "Accept", "Accept-Encoding", "Content-Type"},
            ) as session:
                # aiohttp retries idempotent requests even on a fresh connection.
                session._retry_connection = False
                async with session.get(self.gateway + "/_fleet/challenge",
                                       allow_redirects=False) as response:
                    check(response.status == 200, "gateway-challenge-refused", 401)
                    raw = await _read(response, 16384)
                try:
                    challenge = unpack(raw.decode("utf-8"))
                except UnicodeError:
                    raise TransportError("invalid-gateway-response", 502) from None
                shape(challenge, {"nonce"})
                nonce = challenge["nonce"]
                check(isinstance(nonce, str) and len(nonce) == 43
                      and all(c.isascii() and (c.isalnum() or c in "-_") for c in nonce),
                      "invalid-gateway-response", 502)
                try:
                    access = await asyncio.wait_for(self._access(), 5)
                    check(isinstance(access, str) and 0 < len(access) <= 8192
                          and all(32 < ord(c) < 127 for c in access), "invalid-token-source")
                    proof = make_proof(self._key, access, nonce=nonce,
                                       target_uri=self.gateway + path, method=method)
                except Exception:
                    raise TransportError("caller-credentials-unavailable", 401) from None
                scheme = "DP" + "oP "
                authenticated = selected | {"Authorization": scheme + access, "DPoP": proof}
                attempted = True
                async with session.request(
                    method, URL(self.gateway + path + query, encoded=True),
                    headers=authenticated, data=body, allow_redirects=False,
                ) as response:
                    response_status(response.status)
                    retained = headers(response.headers.items(), RESPONSE_HEADERS)
                    raw = await _read(response, response_limit)
                    return ServiceResponse(response.status, tuple(retained.items()), raw)

        try:
            return await asyncio.wait_for(exchange(), self._timeout)
        except (aiohttp.ClientError, OSError, asyncio.TimeoutError, TransportError) as exc:
            if attempted and method not in ("GET", "HEAD", "OPTIONS"):
                raise TransportError("outcome-unknown", 502) from None
            if isinstance(exc, TransportError):
                raise
            code = "gateway-timeout" if isinstance(exc, asyncio.TimeoutError) else "gateway-unavailable"
            raise TransportError(code, 502) from None
        finally:
            self._active -= 1

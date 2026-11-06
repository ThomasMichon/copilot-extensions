"""Fixed HTTP account-source leases over an already-approved auth injector."""

from __future__ import annotations

import asyncio
import json
import math
import re
import ssl
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from credential_relay.account_relay import read_reference
from credential_relay.http_auth import HttpAuthContext, HttpAuthInjector

LEASE_CONTRACT = "credential-relay.account-source-lease/v1"
HTTP_SUPPORT = {"lease_contract": LEASE_CONTRACT, "exchange": "http/v1"}


class SourceLeaseError(RuntimeError):
    """A static diagnostic which never includes provider responses or secrets."""


@dataclass
class HttpSourceLease:
    """In-memory source authorization; neither token expiry nor execution authority."""

    token: str = field(repr=False)
    account: str
    host: str
    lease_id: str
    issued_at: float
    expires_at: float
    _wall: float = field(repr=False)
    _monotonic: float = field(repr=False)
    _remaining: float = field(repr=False)
    _expired: bool = field(default=False, init=False, repr=False)

    def remaining_seconds(self) -> float:
        """Conservatively consume both clocks, permanently expiring on regression."""
        wall, monotonic = time.time(), time.monotonic()
        if wall < self._wall or monotonic < self._monotonic:
            self._expired = True
        self._remaining = min(
            self._remaining - (monotonic - self._monotonic), self.expires_at - wall,
        )
        self._wall, self._monotonic = wall, monotonic
        self._expired |= self._remaining <= 0
        return 0.0 if self._expired else self._remaining


def _pairs(values: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in values:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _text(value: object, limit: int = 2048) -> bool:
    return (
        isinstance(value, str) and 0 < len(value) <= limit
        and all(32 < ord(char) < 127 for char in value)
    )


class HttpSourceLeaseClient:
    """One explicit endpoint, account binding and caller capability; no fallback.

    The embedding owns approval of the injected auth source and the HTTPS
    endpoint. Construction does not approve or acquire either credential.
    """

    def __init__(
        self, endpoint: str, *, auth: HttpAuthInjector, context: HttpAuthContext,
        source: str, account: str, recipient: str, host: str, capability_file: Path,
        tls_context: ssl.SSLContext | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        try:
            url = urlsplit(endpoint)
            url.port
            if (
                url.scheme != "https" or not url.hostname
                or url.username is not None or url.password is not None
                or url.query or url.fragment or not _text(endpoint)
                or context.origin != f"https://{url.netloc.lower()}"
                or not context.identity or not context.generation
                or not capability_file.is_absolute()
                or any(not _text(item) for item in (source, account, recipient, host))
                or tls_context is not None and (
                    not tls_context.check_hostname or tls_context.verify_mode != ssl.CERT_REQUIRED
                )
            ):
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise SourceLeaseError("source-lease-binding-invalid") from None
        self._endpoint, self._auth, self._context = endpoint, auth, context
        self._capability_file, self._transport = capability_file, transport
        self._tls_context = tls_context
        self._binding = {
            "source": source, "account": account, "recipient": recipient,
            "host": host, "action": "get-github-token",
        }

    async def acquire(self, *, timeout: float = 20) -> HttpSourceLease:
        """Negotiate before reading the capability; never retry an uncertain POST."""
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise SourceLeaseError("source-lease-timeout-invalid")
        started_wall, started = time.time(), time.monotonic()
        try:
            return await asyncio.wait_for(self._acquire(started_wall, started), timeout)
        except SourceLeaseError:
            raise
        except Exception:
            raise SourceLeaseError("source-lease-unavailable") from None

    async def _acquire(self, started_wall: float, started: float) -> HttpSourceLease:
        async with httpx.AsyncClient(
            trust_env=False, follow_redirects=False, transport=self._transport,
            verify=self._tls_context if self._tls_context is not None else True,
        ) as client:
            support = await self._request(client, "GET")
            if support != HTTP_SUPPORT:
                raise SourceLeaseError("source-lease-unsupported")
            capability = read_reference(str(self._capability_file), 4096).decode("ascii").strip()
            if not _text(capability) or len(capability) < 32:
                raise SourceLeaseError("source-lease-capability-invalid")
            value = await self._request(client, "POST", {
                **self._binding, "capability": capability, "lease_contract": LEASE_CONTRACT,
            })
        expected = {
            "account", "host", "token", "lease_contract", "lease_id",
            "lease_issued_at", "lease_expires_at", "lease_remaining_seconds",
        }
        if (
            set(value) != expected or value["lease_contract"] != LEASE_CONTRACT
            or value["account"] != self._binding["account"]
            or value["host"] != self._binding["host"] or not _text(value["token"], 16384)
            or not isinstance(value["lease_id"], str)
            or re.fullmatch("[0-9a-f]{32}", value["lease_id"]) is None
        ):
            raise SourceLeaseError("source-lease-response-invalid")
        issued, expires, remaining = (
            value[key] for key in ("lease_issued_at", "lease_expires_at", "lease_remaining_seconds")
        )
        if any(type(number) not in (int, float) or not math.isfinite(number)
               for number in (issued, expires, remaining)):
            raise SourceLeaseError("source-lease-response-invalid")
        wall, monotonic = time.time(), time.monotonic()
        if (
            not 0 < issued < expires < 1e12 or expires - issued > 3600
            or not 0 < remaining <= expires - issued or issued > wall
            or wall < started_wall or monotonic < started
        ):
            raise SourceLeaseError("source-lease-response-invalid")
        budget = min(expires - wall, remaining - (monotonic - started))
        if budget <= 0:
            raise SourceLeaseError("source-lease-expired")
        return HttpSourceLease(
            value["token"], value["account"], value["host"], value["lease_id"],
            issued, expires, wall, monotonic, budget,
        )

    async def _request(
        self, client: httpx.AsyncClient, method: str, payload: dict[str, str] | None = None,
    ) -> dict:
        headers = dict(await self._auth.headers(self._context))
        async with client.stream(
            method, self._endpoint, headers=headers, json=payload, follow_redirects=False,
        ) as response:
            if response.status_code != 200:
                raise SourceLeaseError("source-lease-unavailable")
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                raw.extend(chunk)
                if len(raw) > 65536:
                    raise SourceLeaseError("source-lease-response-invalid")
            value = json.loads(raw, object_pairs_hook=_pairs)
            if not isinstance(value, dict):
                raise SourceLeaseError("source-lease-response-invalid")
            return value

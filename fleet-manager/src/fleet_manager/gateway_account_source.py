"""Account-source HTTP adapter for an existing outbound service connector.

The local custodian still validates capabilities, grants and provider results.
Neither channel admission nor a source lease downscopes an account credential.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from dataclasses import dataclass
from typing import Protocol

from .gateway_service_handler import HandlerBinding, ServiceRequest, ServiceResponse
from .planning import PlanError, decode

LEASE_CONTRACT = "credential-relay.account-source-lease/v1"
_FIELDS = frozenset({"source", "account", "recipient", "host", "action"})
_LEASE_FIELDS = frozenset({"lease_contract", "lease_id", "lease_issued_at", "lease_expires_at"})


class AccountSource(Protocol):
    async def resolve(
        self, fields: dict[str, str], capability: str, *, source_lease: bool = False,
    ) -> str | None:
        """Current custodian validation; compatible with credential_relay.AccountRelay."""
        ...


def _text(value: object, limit: int = 2048) -> bool:
    return (isinstance(value, str) and 0 < len(value) <= limit
            and all(32 < ord(char) < 127 for char in value))


@dataclass(frozen=True)
class AccountSourceGrant:
    principal: str
    source: str
    account: str
    recipient: str
    host: str
    action: str

    def __post_init__(self) -> None:
        if (not all(_text(getattr(self, name)) for name in _FIELDS | {"principal"})
                or self.action != "get-github-token"):
            raise ValueError("invalid-account-source-grant")

    def fields(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in _FIELDS}


def _json(status: int, value: dict) -> ServiceResponse:
    return ServiceResponse(
        status, (("content-type", "application/json"), ("cache-control", "no-store")),
        json.dumps(value, allow_nan=False, separators=(",", ":")).encode(),
    )


def _error(status: int, code: str) -> ServiceResponse:
    return _json(status, {"error": code})


class AccountSourceHandler:
    """Explicit local source assignment, hosted by ServiceConnector, not a daemon.

    Grants bind the channel-derived principal to an exact existing source grant.
    The caller's capability is passed once, never read from a controller file.
    ``installation`` identifies this selected handler, not native execution
    authority or a credential-controller enrollment in the scoped-token broker.
    """

    def __init__(
        self, source: AccountSource, *, fleet: str, gateway: str, slot: str,
        installation: str, grants: tuple[AccountSourceGrant, ...],
    ) -> None:
        if (not callable(getattr(source, "resolve", None))
                or not all(_text(item) for item in (fleet, gateway, slot, installation))
                or type(grants) is not tuple or not 0 < len(grants) <= 128
                or any(type(grant) is not AccountSourceGrant for grant in grants)
                or len(set(grants)) != len(grants)):
            raise ValueError("invalid-account-source-binding")
        self._source, self._grants = source, grants
        self._fleet, self._gateway, self._slot = fleet, gateway, slot
        self._installation = installation

    def binding(self) -> HandlerBinding:
        return HandlerBinding(self._fleet, self._gateway, self.handle)

    async def handle(self, request: ServiceRequest) -> ServiceResponse:
        request.lifetime.check()
        context = request.context
        if ((context.fleet, context.gateway, context.slot)
                != (self._fleet, self._gateway, self._slot)
                or context.expires_at <= time.time()):
            return _error(403, "source-caller-denied")
        selected = tuple(grant for grant in self._grants if grant.principal == context.principal)
        if not selected:
            return _error(403, "source-caller-denied")
        if request.method == "GET" and request.target == "/health" and request.body == b"":
            return _json(200, {
                "installationId": self._installation,
                "source_credentials_contract": {"lease_contract": LEASE_CONTRACT, "exchange": "http/v1"},
            })
        if request.method != "POST" or request.target != "/source-credentials":
            return _error(404, "source-route-unavailable")
        try:
            if type(request.body) is not bytes or not 0 < len(request.body) <= 16384:
                raise ValueError
            value = decode(request.body)
            keys = _FIELDS | {"capability"}
            if (not isinstance(value, dict) or set(value) not in (keys, keys | {"lease_contract"})
                    or not all(_text(value[name]) for name in keys)
                    or len(value["capability"]) < 32):
                raise ValueError
        except (PlanError, ValueError, UnicodeError, RecursionError):
            return _error(400, "source-request-invalid")
        leased = "lease_contract" in value
        if leased and value["lease_contract"] != LEASE_CONTRACT:
            return _error(409, "source-lease-unsupported")
        fields = {name: value[name] for name in _FIELDS}
        if not any(grant.fields() == fields for grant in selected):
            return _error(403, "source-binding-denied")
        started_wall, started = time.time(), time.monotonic()
        timeout = min(20.0, context.expires_at - started_wall)
        if timeout <= 0:
            return _error(403, "source-caller-denied")
        try:
            raw = await asyncio.wait_for(self._source.resolve(
                fields, value["capability"], source_lease=leased,
            ), timeout)
            request.lifetime.check()
            if time.time() >= context.expires_at:
                return _error(403, "source-caller-denied")
            result = self._result(raw, fields, leased, started_wall, started)
        except asyncio.CancelledError:
            raise
        except Exception:
            return _error(503, "source-credential-unavailable")
        return _json(200, result | {"installationId": self._installation})

    @staticmethod
    def _result(raw: str | None, fields: dict, leased: bool,
                started_wall: float, started: float) -> dict:
        if not isinstance(raw, str) or not 0 < len(raw) <= 16384:
            raise ValueError
        response = {}
        for line in raw.strip("\n").split("\n"):
            key, separator, value = line.partition("=")
            if not separator or key in response or not _text(value):
                raise ValueError
            response[key] = value
        expected = {"protocol", "host", "username", "token"} | (_LEASE_FIELDS if leased else set())
        if (set(response) != expected or response["protocol"] != "https"
                or response["host"] != fields["host"] or response["username"] != fields["account"]):
            raise ValueError
        result = {"token": response["token"], "account": fields["account"], "host": fields["host"]}
        if leased:
            if (response["lease_contract"] != LEASE_CONTRACT
                    or re.fullmatch("[0-9a-f]{32}", response["lease_id"]) is None):
                raise ValueError
            issued, expires = (float(response[name]) for name in ("lease_issued_at", "lease_expires_at"))
            wall, monotonic = time.time(), time.monotonic()
            if (not all(math.isfinite(number) for number in (issued, expires, wall, monotonic))
                    or not 0 < issued < expires < 1e12 or expires - issued > 3600
                    or wall < started_wall or monotonic < started or issued > wall):
                raise ValueError
            remaining = min(expires - wall, expires - issued - max(
                wall - started_wall, monotonic - started,
            ))
            if remaining <= 0:
                raise ValueError
            result |= {
                "lease_contract": LEASE_CONTRACT, "lease_id": response["lease_id"],
                "lease_issued_at": issued, "lease_expires_at": expires,
                "lease_remaining_seconds": remaining,
            }
        return result

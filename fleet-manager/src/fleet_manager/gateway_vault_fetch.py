"""Code-registered Gateway selection over current Controller account custodians."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from .gateway_account_source import AccountSourceGrant, _error, _json, _text
from .gateway_controller_services import ConnectedService, ControllerSourceBinding
from .gateway_local_handler import LocalHandlerBinding, LocalServiceRequest
from .gateway_manifest import ServiceSlot
from .gateway_service_handler import ServiceResponse
from .gateway_vault_available import (
    NORMAL_FETCH_CONTRACT, NORMAL_LEASE_SUPPORT, SOURCE_LEASE_CONTRACT,
    AvailableSource, FetchedToken, NormalFetchCoordinator,
    SourceAvailability, SourceIdentity, SourceLease, TokenKey,
)
from .planning import PlanError, canonical as _canonical, decode as _decode


canonical: Callable[[object], bytes] = _canonical
decode: Callable[[bytes], Any] = _decode


@dataclass(frozen=True)
class AccountSourcePin:
    """Trusted deployment selection, separate from a live connection observation."""

    controller: str
    source: str
    installation: str

    def __post_init__(self) -> None:
        SourceIdentity(self.controller, self.installation, self.source)


@dataclass(frozen=True)
class AccountFetchGrant:
    """One exact original caller and account purpose; not OAuth downscoping."""

    principal: str
    binding: str
    account: str
    recipient: str
    host: str
    sources: tuple[AccountSourcePin, ...]

    def __post_init__(self) -> None:
        if (type(self.sources) is not tuple or not 0 < len(self.sources) <= 32
                or any(type(pin) is not AccountSourcePin for pin in self.sources)
                or len({pin.controller for pin in self.sources}) != len(self.sources)):
            raise ValueError("invalid-account-fetch-grant")
        for pin in self.sources:
            AccountSourceGrant(self.principal, **self.fields(pin))
        self.key()

    def fields(self, pin: AccountSourcePin) -> dict[str, str]:
        return {
            "source": pin.source, "account": self.account, "recipient": self.recipient,
            "host": self.host, "action": "get-github-token",
        }

    def key(self) -> TokenKey:
        # These are request-selection dimensions, not asserted token scopes.
        return TokenKey(
            "gh-auth", self.account, "not-applicable", self.host, self.host,
            ("get-github-token",), self.principal, self.binding,
        )


@dataclass(frozen=True)
class _ControllerProvider:
    local: LocalServiceRequest = field(repr=False)
    offer: ConnectedService
    grant: AccountFetchGrant
    pin: AccountSourcePin
    capability: str = field(repr=False)
    leased: bool = False
    contract = NORMAL_FETCH_CONTRACT

    async def fetch_normal(self, key: TokenKey, *, deadline: float) -> SourceAvailability:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0 or key != self.grant.key():
            return SourceAvailability("uncertain")
        fields = self.grant.fields(self.pin)
        started_wall, started = time.time(), time.monotonic()
        response = await self.local.call_service(
            self.offer, target="/token-fetch", timeout=min(remaining, 30),
            body=canonical({
                **fields, "capability": self.capability,
                "installationId": self.pin.installation, "fetch_contract": self.contract,
                **({"lease_contract": SOURCE_LEASE_CONTRACT} if self.leased else {}),
            }))
        if response.status != 200:
            return SourceAvailability(
                "unsupported" if response.status in (404, 409) else
                "unavailable" if response.status in (400, 403, 503) else "uncertain")
        try:
            if not 0 < len(response.body) <= 32768:
                raise ValueError
            value = decode(response.body)
            expected = {
                **fields, "fetch_contract": self.contract, "installationId": self.pin.installation,
                "acquisition": "normal-fetch",
            }
            lease_keys = {
                "lease_contract", "lease_id", "lease_issued_at",
                "lease_expires_at", "lease_remaining_seconds",
            } if self.leased else set()
            if (type(value) is not dict
                    or set(value) != set(expected) | {"token", "token_expires_at"} | lease_keys
                    or any(value[name] != item for name, item in expected.items())):
                raise ValueError
            lease = None
            if self.leased:
                if value["lease_contract"] != SOURCE_LEASE_CONTRACT:
                    raise ValueError
                wall, monotonic = time.time(), time.monotonic()
                if wall < started_wall or monotonic < started:
                    raise ValueError
                lease = SourceLease(
                    value["lease_id"], value["lease_issued_at"], value["lease_expires_at"],
                    value["lease_remaining_seconds"], wall, monotonic,
                )
                lease.remaining -= max(wall - started_wall, monotonic - started)
                if lease.remaining_seconds() <= 0:
                    raise ValueError
            token = FetchedToken(
                key, value["token"], value["token_expires_at"], source_lease=lease,
            )
        except (PlanError, ValueError, UnicodeError, RecursionError):
            return SourceAvailability("uncertain")
        return SourceAvailability("fetched", token)


@dataclass(frozen=True)
class _Lookup:
    local: LocalServiceRequest = field(repr=False)
    service: str
    key: TokenKey
    providers: tuple[tuple[ConnectedService, AvailableSource], ...] = field(repr=False)

    def sources(self, key: TokenKey) -> tuple[AvailableSource, ...]:
        self.local.checkpoint()
        if key != self.key:
            raise ValueError("account-fetch-binding-changed")
        current = self.local.service_offers(self.service)
        return tuple(source for offer, source in self.providers if offer in current)


class GatewayAccountFetch:
    """Gateway-owned normal retrieval; no credential cache, login or retry chain.

    The composition owner must call ``stop`` and ``drain`` at shutdown. Pending
    source work remains owned/countable after its caller deadline; unknown
    outcomes never trigger an available-only downgrade or replacement replay.
    """

    def __init__(
        self, *, slot: ServiceSlot, service: str, grants: tuple[AccountFetchGrant, ...],
        budget: float = 30, installation: str | None = None,
    ) -> None:
        if (type(grants) is not tuple or not 0 < len(grants) <= 128
                or any(type(grant) is not AccountFetchGrant for grant in grants)
                or len({(grant.principal, grant.binding) for grant in grants}) != len(grants)):
            raise ValueError("invalid-gateway-account-fetch")
        if installation is not None and not _text(installation):
            raise ValueError("invalid-gateway-account-fetch")
        self._installation = installation
        controllers = tuple(sorted({pin.controller for grant in grants for pin in grant.sources}))
        sources = ControllerSourceBinding(service, controllers)
        self._binding = LocalHandlerBinding(slot, self.handle, (sources,))
        self._service, self._grants = service, grants
        self._lookup: ContextVar[_Lookup] = ContextVar("gateway-account-fetch")
        self._coordinator = NormalFetchCoordinator(
            lambda key: self._lookup.get().sources(key), budget=budget)

    def binding(self) -> LocalHandlerBinding:
        return self._binding

    @property
    def pending_count(self) -> int:
        return self._coordinator.pending_count

    async def handle(self, local: LocalServiceRequest) -> ServiceResponse:
        local.checkpoint()
        request = local.request
        if (request.method == "GET" and request.target == "/health"
                and request.body == b"" and self._installation is not None):
            return _json(200, {
                "installationId": self._installation,
                "normal_fetch_contract": NORMAL_FETCH_CONTRACT,
                "normal_source_lease_contract": NORMAL_LEASE_SUPPORT,
            })
        if request.method != "POST" or request.target != "/token-fetch":
            return _error(404, "vault-fetch-route-unavailable")
        try:
            if not 0 < len(request.body) <= 16384:
                raise ValueError
            value = decode(request.body)
            keys = {"fetch_contract", "binding", "sources"}
            if (type(value) is not dict
                    or set(value) not in (keys, keys | {"lease_contract", "installationId"})
                    or type(value["binding"]) is not str
                    or type(value["sources"]) is not dict
                    or not 0 < len(value["sources"]) <= 32
                    or any(type(name) is not str or type(capability) is not str
                           or not 32 <= len(capability) <= 2048
                           or any(not 33 <= ord(char) < 127 for char in capability)
                           for name, capability in value["sources"].items())):
                raise ValueError
        except (PlanError, ValueError, UnicodeError, RecursionError):
            return _error(400, "vault-fetch-request-invalid")
        if value["fetch_contract"] != NORMAL_FETCH_CONTRACT:
            return _error(409, "vault-fetch-unsupported")
        leased = "lease_contract" in value
        if leased and value["lease_contract"] != SOURCE_LEASE_CONTRACT:
            return _error(409, "vault-fetch-lease-unsupported")
        if leased and (self._installation is None
                       or value["installationId"] != self._installation):
            return _error(409, "vault-fetch-installation-mismatch")
        grant = next((item for item in self._grants if (
            item.principal == request.context.principal and item.binding == value["binding"])), None)
        if grant is None:
            return _error(403, "vault-fetch-binding-denied")
        pins = {pin.controller: pin for pin in grant.sources}
        if not set(value["sources"]) <= pins.keys():
            return _error(403, "vault-fetch-source-denied")
        key = grant.key()
        providers = []
        for offer in local.service_offers(self._service):
            if offer.controller not in value["sources"]:
                continue
            pin = pins[offer.controller]
            provider = _ControllerProvider(
                local, offer, grant, pin, value["sources"][offer.controller], leased,
            )
            providers.append((offer, AvailableSource(
                SourceIdentity(offer.controller, offer.connection + ":" + offer.generation, pin.source),
                provider)))
        context = self._lookup.set(_Lookup(local, self._service, key, tuple(providers)))
        try:
            result = await self._coordinator.lookup(key)
            local.checkpoint()
            if result.state != "fetched":
                return _json(503, {"fetch_contract": NORMAL_FETCH_CONTRACT, "state": result.state})
            assert type(result.token) is FetchedToken and result.source is not None
            lease_fields: dict[str, str | float | None] = {}
            if leased:
                if result.token.source_lease is None:
                    return _error(503, "vault-fetch-lease-unavailable")
                lease_fields = {
                    **result.token.source_lease.fields(), "installationId": self._installation,
                }
                if result.token.source_lease.remaining_seconds() <= 0:
                    return _error(503, "vault-fetch-lease-expired")
            return _json(200, {
                "fetch_contract": NORMAL_FETCH_CONTRACT, "state": "fetched",
                "acquisition": result.token.acquisition, "token": result.token.value,
                "token_expires_at": result.token.expires_at,
                "binding": grant.binding, "account": grant.account, "host": grant.host,
                **lease_fields,
            })
        finally:
            self._lookup.reset(context)

    def stop(self) -> None:
        self._coordinator.stop()

    async def drain(self, *, timeout: float = 1) -> bool:
        return await self._coordinator.drain(timeout=timeout)

"""Bounded token selection with distinct available-only and normal-fetch profiles."""

from __future__ import annotations

import asyncio
import math
import re
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from typing import Literal, Protocol


AVAILABLE_CONTRACT = "fleet-manager.vault-available/v1"
NORMAL_FETCH_CONTRACT = "fleet-manager.vault-normal-fetch/v1"
SOURCE_LEASE_CONTRACT = "credential-relay.account-source-lease/v1"
NORMAL_LEASE_SUPPORT = {
    "fetch_contract": NORMAL_FETCH_CONTRACT, "lease_contract": SOURCE_LEASE_CONTRACT,
}
AvailabilityState = Literal[
    "available", "fetched", "cache-miss", "unavailable", "uncertain", "unsupported",
]
LookupState = Literal[
    "available", "fetched", "no-source", "cache-exhausted", "unavailable", "uncertain",
]


def _text(value: object) -> bool:
    return (type(value) is str and 0 < len(value) <= 2048
            and all(32 < ord(char) < 127 for char in value))


@dataclass(frozen=True)
class TokenKey:
    """Trusted request identity, not caller-supplied authorization or a capability."""

    provider: str
    account: str
    tenant: str
    audience: str
    resource: str
    scopes: tuple[str, ...]
    consumer: str
    binding: str

    def __post_init__(self) -> None:
        if (not all(_text(value) for value in (
                self.provider, self.account, self.tenant, self.audience,
                self.resource, self.consumer, self.binding,
        )) or type(self.scopes) is not tuple or not 0 < len(self.scopes) <= 64
                or not all(_text(scope) for scope in self.scopes)
                or tuple(sorted(set(self.scopes))) != self.scopes):
            raise ValueError("invalid-vault-token-key")


@dataclass(frozen=True)
class AvailableToken:
    key: TokenKey
    value: str = field(repr=False)
    expires_at: float
    acquisition: Literal["cache", "silent-refresh"] = "cache"

    def __post_init__(self) -> None:
        if (type(self.key) is not TokenKey or type(self.value) is not str
                or not 0 < len(self.value) <= 32768
                or any(not 33 <= ord(char) < 127 for char in self.value)
                or type(self.expires_at) not in (int, float)
                or not math.isfinite(self.expires_at)
                or self.acquisition not in ("cache", "silent-refresh")):
            raise ValueError("invalid-vault-available-token")


@dataclass
class SourceLease:
    """Custodian-issued delivery authorization, never actual credential expiry."""

    lease_id: str
    issued_at: float
    expires_at: float
    remaining: float
    wall: float = field(default_factory=time.time, repr=False)
    monotonic: float = field(default_factory=time.monotonic, repr=False)
    _expired: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if (type(self.lease_id) is not str
                or re.fullmatch("[0-9a-f]{32}", self.lease_id) is None
                or any(type(value) not in (int, float) or not -1e12 < value < 1e12
                       or not math.isfinite(value)
                       for value in (self.issued_at, self.expires_at, self.remaining,
                                     self.wall, self.monotonic))
                or not 0 < self.issued_at < self.expires_at < 1e12
                or self.expires_at - self.issued_at > 3600
                or not 0 < self.remaining <= self.expires_at - self.issued_at
                or self.issued_at > self.wall):
            raise ValueError("invalid-vault-source-lease")

    def remaining_seconds(self) -> float:
        wall, monotonic = time.time(), time.monotonic()
        if wall < self.wall or monotonic < self.monotonic:
            self._expired = True
        self.remaining = min(
            self.expires_at - wall,
            self.remaining - max(0, wall - self.wall, monotonic - self.monotonic),
        )
        self.wall, self.monotonic = wall, monotonic
        self._expired |= self.remaining <= 0
        return 0 if self._expired else self.remaining

    def fields(self) -> dict[str, str | float]:
        return {
            "lease_contract": SOURCE_LEASE_CONTRACT, "lease_id": self.lease_id,
            "lease_issued_at": self.issued_at, "lease_expires_at": self.expires_at,
            "lease_remaining_seconds": self.remaining_seconds(),
        }


@dataclass(frozen=True)
class FetchedToken:
    """Normal provider retrieval, not a cache-only or known-expiry attestation."""

    key: TokenKey
    value: str = field(repr=False)
    expires_at: float | None = None
    acquisition: Literal["normal-fetch"] = "normal-fetch"
    source_lease: SourceLease | None = None

    def __post_init__(self) -> None:
        if (type(self.key) is not TokenKey or type(self.value) is not str
                or not 0 < len(self.value) <= 32768
                or any(not 33 <= ord(char) < 127 for char in self.value)
                or (self.expires_at is not None and (
                    type(self.expires_at) not in (int, float)
                    or not -1e12 < self.expires_at < 1e12
                    or not math.isfinite(self.expires_at)))
                or self.acquisition != "normal-fetch"
                or self.source_lease is not None and type(self.source_lease) is not SourceLease):
            raise ValueError("invalid-vault-fetched-token")


@dataclass(frozen=True)
class SourceAvailability:
    state: AvailabilityState
    token: AvailableToken | FetchedToken | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if (self.state not in (
                "available", "fetched", "cache-miss", "unavailable", "uncertain", "unsupported")
                or (self.state == "available") != (type(self.token) is AvailableToken)
                or (self.state == "fetched") != (type(self.token) is FetchedToken)
                or (self.state not in ("available", "fetched") and self.token is not None)):
            raise ValueError("invalid-vault-availability")


class AvailableTokenProvider(Protocol):
    contract: str

    async def get_available(self, key: TokenKey, *, deadline: float) -> SourceAvailability:
        """Only cache reads or guaranteed noninteractive refresh; monotonic deadline.

        Provider custody must validate the exact access binding and actual token
        expiry. An opaque resolve/login API is not an implementation of this port.
        """
        ...


class NormalTokenProvider(Protocol):
    contract: str

    async def fetch_normal(self, key: TokenKey, *, deadline: float) -> SourceAvailability:
        """Bounded ordinary retrieval; normal provider refresh may occur."""
        ...


@dataclass(frozen=True)
class SourceIdentity:
    controller: str
    incarnation: str
    source: str

    def __post_init__(self) -> None:
        if not all(_text(value) for value in (
                self.controller, self.incarnation, self.source)):
            raise ValueError("invalid-vault-source-identity")


@dataclass(frozen=True)
class AvailableSource:
    identity: SourceIdentity
    provider: AvailableTokenProvider | NormalTokenProvider = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self.identity) is not SourceIdentity:
            raise ValueError("invalid-vault-source")


@dataclass(frozen=True)
class SourceObservation:
    identity: SourceIdentity
    state: AvailabilityState


@dataclass(frozen=True)
class AvailableLookup:
    state: LookupState
    token: AvailableToken | FetchedToken | None = field(default=None, repr=False)
    source: SourceIdentity | None = None
    observations: tuple[SourceObservation, ...] = ()


class AvailableTokenCoordinator:
    """Gateway-hosted selection over an owner's *current authorized* snapshot.

    ``sources`` must revalidate the original caller, logical request and each
    source binding on every call, returning only connected admitted incarnations.
    Advertisements alone and this coordinator's affinity are never authority.
    Use one instance per event loop; no work starts until ``lookup`` is called.
    """

    _contract = AVAILABLE_CONTRACT
    _method = "get_available"
    _success: AvailabilityState = "available"
    _token_type: type[AvailableToken] | type[FetchedToken] = AvailableToken

    def __init__(
        self, sources: Callable[[TokenKey], tuple[AvailableSource, ...]], *,
        budget: float = 30.0, affinity_limit: int = 256, probe_limit: int = 128,
    ) -> None:
        if (not callable(sources) or type(budget) not in (int, float)
                or not 0 < budget <= 30 or type(affinity_limit) is not int
                or not 1 <= affinity_limit <= 4096 or type(probe_limit) is not int
                or not 1 <= probe_limit <= 256):
            raise ValueError("invalid-vault-coordinator")
        self._sources, self._budget = sources, budget
        self._affinity_limit, self._probe_limit = affinity_limit, probe_limit
        self._affinity: OrderedDict[TokenKey, tuple[str, str]] = OrderedDict()
        self._probes: dict[tuple[TokenKey, str, str], asyncio.Task[SourceAvailability]] = {}
        self._closed = False

    @property
    def pending_count(self) -> int:
        return len(self._probes)

    def _snapshot(self, key: TokenKey) -> tuple[AvailableSource, ...]:
        sources = self._sources(key)
        if (type(sources) is not tuple or len(sources) > 32
                or any(type(source) is not AvailableSource for source in sources)
                or len({source.identity for source in sources}) != len(sources)):
            raise ValueError("invalid-vault-source-snapshot")
        return sources

    def _current(self, key: TokenKey, source: AvailableSource) -> bool:
        return any(item.identity == source.identity and item.provider is source.provider
                   for item in self._snapshot(key))

    def _settled(
        self, probe_key: tuple[TokenKey, str, str], task: asyncio.Task[SourceAvailability],
    ) -> None:
        if self._probes.get(probe_key) is task:
            del self._probes[probe_key]
        if not task.cancelled():
            task.exception()

    def _usable(self, key: TokenKey, token: AvailableToken | FetchedToken) -> bool:
        return (type(token) is self._token_type and token.key == key
                and (token.expires_at is None or token.expires_at > time.time())
                and (not isinstance(token, FetchedToken) or token.source_lease is None
                     or token.source_lease.remaining_seconds() > 0))

    async def _probe(
        self, key: TokenKey, source: AvailableSource, deadline: float,
        authorize: Callable[[AvailableSource], bool],
    ) -> SourceAvailability:
        loop = asyncio.get_running_loop()
        if self._closed or loop.time() >= deadline:
            return SourceAvailability("uncertain")
        if not authorize(source):
            return SourceAvailability("unavailable")
        if self._closed or loop.time() >= deadline:
            return SourceAvailability("uncertain")
        try:
            result = await getattr(source.provider, self._method)(key, deadline=deadline)
            if (type(result) is not SourceAvailability
                    or (result.token is not None and (
                        result.state != self._success or type(result.token) is not self._token_type))):
                return SourceAvailability("uncertain")
            return result
        except asyncio.CancelledError:
            raise
        except Exception:
            # Provider errors may contain secrets; only the typed outcome escapes.
            return SourceAvailability("uncertain")

    async def lookup(self, key: TokenKey, *, timeout: float | None = None) -> AvailableLookup:
        if self._closed:
            raise RuntimeError("vault-coordinator-closed")
        if (type(key) is not TokenKey or (timeout is not None and (
                type(timeout) not in (int, float) or not 0 < timeout <= 30))):
            raise ValueError("invalid-vault-lookup")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + min(self._budget, timeout if timeout is not None else self._budget)
        for probe_key, task in tuple(self._probes.items()):
            if task.done():
                self._settled(probe_key, task)
        sources = self._snapshot(key)
        if not sources:
            return AvailableLookup("no-source")
        preferred = self._affinity.get(key)
        sources = tuple(sorted(sources, key=lambda source: (
            (source.identity.controller, source.identity.source) != preferred)))
        tasks: dict[asyncio.Task[SourceAvailability], AvailableSource] = {}
        observations: dict[SourceIdentity, SourceObservation] = {}
        candidates: list[tuple[AvailableSource, AvailableToken | FetchedToken]] = []
        policy_error: Exception | None = None

        def authorize(source: AvailableSource) -> bool:
            nonlocal policy_error
            if policy_error is not None:
                raise policy_error
            try:
                return self._current(key, source)
            except Exception as error:
                policy_error = error
                raise

        try:
            for source in sources:
                probe_key = (key, source.identity.controller, source.identity.source)
                if (getattr(source.provider, "contract", None) != self._contract
                        or not callable(getattr(source.provider, self._method, None))):
                    observations[source.identity] = SourceObservation(source.identity, "unsupported")
                elif probe_key in self._probes or len(self._probes) >= self._probe_limit:
                    observations[source.identity] = SourceObservation(source.identity, "uncertain")
                else:
                    task = loop.create_task(self._probe(key, source, deadline, authorize))
                    self._probes[probe_key] = task
                    task.add_done_callback(partial(self._settled, probe_key))
                    tasks[task] = source
            while tasks and not self._closed:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                done, _ = await asyncio.wait(
                    tasks, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
                for task in tuple(tasks):
                    if task not in done:
                        continue
                    source = tasks.pop(task)
                    result = (SourceAvailability("uncertain") if task.cancelled() else task.result())
                    if type(result) is not SourceAvailability:
                        result = SourceAvailability("uncertain")
                    token = result.token
                    current = authorize(source)
                    if not current:
                        result = SourceAvailability("unavailable")
                    elif token is not None and not self._usable(key, token):
                        result = SourceAvailability("unavailable")
                    elif result.state == self._success and (
                            self._closed or loop.time() >= deadline):
                        result = SourceAvailability("uncertain")
                    observations[source.identity] = SourceObservation(source.identity, result.state)
                    if (result.state == self._success and not self._closed
                            and loop.time() < deadline):
                        assert result.token is not None
                        candidates.append((source, result.token))
                if candidates:
                    break
            for source in tasks.values():
                observations[source.identity] = SourceObservation(source.identity, "uncertain")
        finally:
            # Do not let cancellation-resistant adapters extend the caller deadline.
            # They remain counted/owned until actual settlement; never reused.
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.sleep(0)
        if policy_error is not None:
            raise policy_error
        for source, token in candidates:
            current = authorize(source)
            if (current and not self._closed and loop.time() < deadline
                    and self._usable(key, token)):
                self._affinity[key] = (source.identity.controller, source.identity.source)
                self._affinity.move_to_end(key)
                while len(self._affinity) > self._affinity_limit:
                    self._affinity.popitem(last=False)
                return AvailableLookup(
                    "fetched" if self._success == "fetched" else "available",
                    token, source.identity, tuple(observations.values()))
            observations[source.identity] = SourceObservation(
                source.identity, "uncertain" if self._closed or loop.time() >= deadline
                else "unavailable")
        states = {item.state for item in observations.values()}
        state: LookupState = (
            "uncertain" if self._closed or loop.time() >= deadline or "uncertain" in states else
            "cache-exhausted" if states == {"cache-miss"} else "unavailable")
        return AvailableLookup(state, observations=tuple(observations.values()))

    def stop(self) -> None:
        self._closed = True
        for task in tuple(self._probes.values()):
            task.cancel()

    async def drain(self, *, timeout: float = 1.0) -> bool:
        """Finite owner cleanup; False preserves unresolved probe responsibility."""
        if type(timeout) not in (int, float) or not 0 < timeout <= 30:
            raise ValueError("invalid-vault-drain")
        tasks = tuple(self._probes.values())
        if tasks:
            await asyncio.wait(tasks, timeout=timeout)
        return all(task.done() for task in tasks)


class NormalFetchCoordinator(AvailableTokenCoordinator):
    """The same bounded current-source selection, with ordinary retrieval semantics.

    It never accepts an available-only provider by an implicit fallback, and its
    results cannot pass the available-only profile's finite-expiry checks.
    """

    _contract = NORMAL_FETCH_CONTRACT
    _method = "fetch_normal"
    _success: AvailabilityState = "fetched"
    _token_type = FetchedToken

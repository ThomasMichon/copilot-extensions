"""Bounded available-token selection; no credential store or interactive operation."""

from __future__ import annotations

import asyncio
import math
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol


AVAILABLE_CONTRACT = "fleet-manager.vault-available/v1"
AvailabilityState = Literal["available", "cache-miss", "unavailable", "uncertain", "unsupported"]


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


@dataclass(frozen=True)
class SourceAvailability:
    state: AvailabilityState
    token: AvailableToken | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if (self.state not in ("available", "cache-miss", "unavailable", "uncertain", "unsupported")
                or (self.state == "available") != (type(self.token) is AvailableToken)
                or (self.state != "available" and self.token is not None)):
            raise ValueError("invalid-vault-availability")


class AvailableTokenProvider(Protocol):
    contract: str

    async def get_available(self, key: TokenKey, *, deadline: float) -> SourceAvailability:
        """Only cache reads or guaranteed noninteractive refresh; monotonic deadline.

        Provider custody must validate the exact access binding and actual token
        expiry. An opaque resolve/login API is not an implementation of this port.
        """
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
    provider: AvailableTokenProvider = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self.identity) is not SourceIdentity:
            raise ValueError("invalid-vault-source")


@dataclass(frozen=True)
class SourceObservation:
    identity: SourceIdentity
    state: AvailabilityState


@dataclass(frozen=True)
class AvailableLookup:
    state: Literal["available", "no-source", "cache-exhausted", "unavailable", "uncertain"]
    token: AvailableToken | None = field(default=None, repr=False)
    source: SourceIdentity | None = None
    observations: tuple[SourceObservation, ...] = ()


class AvailableTokenCoordinator:
    """Gateway-hosted selection over an owner's *current authorized* snapshot.

    ``sources`` must revalidate the original caller, logical request and each
    source binding on every call, returning only connected admitted incarnations.
    Advertisements alone and this coordinator's affinity are never authority.
    Use one instance per event loop; no work starts until ``lookup`` is called.
    """

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
        self._probes: dict[tuple[TokenKey, str, str], asyncio.Task] = {}
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

    def _settled(self, probe_key: tuple[TokenKey, str, str], task: asyncio.Task) -> None:
        if self._probes.get(probe_key) is task:
            del self._probes[probe_key]
        if not task.cancelled():
            task.exception()

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
            return await source.provider.get_available(key, deadline=deadline)
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
        tasks: dict[asyncio.Task, AvailableSource] = {}
        observations: dict[SourceIdentity, SourceObservation] = {}
        candidates: list[tuple[AvailableSource, AvailableToken]] = []
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
                if (getattr(source.provider, "contract", None) != AVAILABLE_CONTRACT
                        or not callable(getattr(source.provider, "get_available", None))):
                    observations[source.identity] = SourceObservation(source.identity, "unsupported")
                elif probe_key in self._probes or len(self._probes) >= self._probe_limit:
                    observations[source.identity] = SourceObservation(source.identity, "uncertain")
                else:
                    task = loop.create_task(self._probe(key, source, deadline, authorize))
                    self._probes[probe_key] = task
                    task.add_done_callback(
                        lambda done, probe_key=probe_key: self._settled(probe_key, done))
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
                    elif token is not None and (
                            token.key != key or token.expires_at <= time.time()):
                        result = SourceAvailability("unavailable")
                    elif result.state == "available" and (
                            self._closed or loop.time() >= deadline):
                        result = SourceAvailability("uncertain")
                    observations[source.identity] = SourceObservation(source.identity, result.state)
                    if (result.state == "available" and not self._closed
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
                    and token.expires_at > time.time()):
                self._affinity[key] = (source.identity.controller, source.identity.source)
                self._affinity.move_to_end(key)
                while len(self._affinity) > self._affinity_limit:
                    self._affinity.popitem(last=False)
                return AvailableLookup(
                    "available", token, source.identity, tuple(observations.values()))
            observations[source.identity] = SourceObservation(
                source.identity, "uncertain" if self._closed or loop.time() >= deadline
                else "unavailable")
        states = {item.state for item in observations.values()}
        state = ("uncertain" if self._closed or loop.time() >= deadline or "uncertain" in states else
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

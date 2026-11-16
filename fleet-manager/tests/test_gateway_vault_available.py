"""Available-token selection never implicitly starts an authentication ceremony."""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any, Literal

import pytest

from fleet_manager.gateway_vault_available import (
    AVAILABLE_CONTRACT, AvailableLookup, AvailableSource, AvailableToken, AvailableTokenCoordinator,
    AvailableTokenProvider, NormalTokenProvider,
    NORMAL_FETCH_CONTRACT, FetchedToken, NormalFetchCoordinator,
    SourceAvailability, SourceIdentity, TokenKey,
)


KEY = TokenKey("provider-a", "account-a", "tenant-a", "audience-a", "resource-a",
               ("read",), "consumer-a", "binding-a")


class NormalProvider:
    contract = NORMAL_FETCH_CONTRACT

    def __init__(self, expiry: float | None = None) -> None:
        self.expiry = expiry
        self.calls: list[tuple[TokenKey, float]] = []

    async def fetch_normal(self, key: TokenKey, *, deadline: float) -> SourceAvailability:
        self.calls.append((key, deadline))
        return SourceAvailability("fetched", FetchedToken(key, "synthetic-token", self.expiry))


def test_normal_fetch_allows_unknown_expiry_but_cannot_pass_available_only_selection() -> None:
    async def run() -> None:
        provider = NormalProvider()
        sources = (source("one", provider),)
        normal = NormalFetchCoordinator(lambda key: sources)
        result = await normal.lookup(KEY)
        assert result.token is not None
        assert result.state == "fetched" and result.token.expires_at is None
        assert result.token.acquisition == "normal-fetch"
        assert "synthetic-token" not in repr(result.token)
        available = AvailableTokenCoordinator(lambda key: sources)
        assert (await available.lookup(KEY)).state == "unavailable"
        assert len(provider.calls) == 1
        provider.expiry = 1
        assert (await normal.lookup(KEY)).state == "unavailable"
        normal.stop()
        available.stop()
        assert await normal.drain() and await available.drain()

    asyncio.run(run())


def test_available_provider_cannot_smuggle_normal_fetch_into_strict_profile() -> None:
    async def run() -> None:
        provider = Provider(lambda key: asyncio.sleep(0, result=SourceAvailability(
            "fetched", FetchedToken(key, "synthetic-token"))))
        coordinator = AvailableTokenCoordinator(lambda key: (source("one", provider),))
        result = await coordinator.lookup(KEY)
        assert result.state == "uncertain" and result.token is None
        assert await coordinator.drain()

    asyncio.run(run())


@pytest.mark.parametrize("expiry", [True, float("nan"), float("inf"), 10 ** 400])
def test_normal_fetch_invalid_expiry_is_not_unknown_or_infinite(expiry: float | int) -> None:
    with pytest.raises(ValueError, match="invalid-vault-fetched-token"):
        FetchedToken(KEY, "synthetic-token", expiry)


class Provider:
    contract = AVAILABLE_CONTRACT

    def __init__(
        self, callback: Callable[[TokenKey], Awaitable[SourceAvailability | None]] | None = None,
    ) -> None:
        self.callback = callback
        self.calls: list[tuple[TokenKey, float]] = []

    async def get_available(self, key: TokenKey, *, deadline: float) -> Any:
        # This test provider also models malformed responses at the provider boundary.
        self.calls.append((key, deadline))
        return await self.callback(key) if self.callback else SourceAvailability("cache-miss")


def source(
    name: str, provider: AvailableTokenProvider | NormalTokenProvider | None = None, *,
    incarnation: str = "connection-a",
) -> AvailableSource:
    return AvailableSource(SourceIdentity(name, incarnation, "source-a"), provider or Provider())


def hit(
    key: TokenKey = KEY, *, expires_at: float | None = None,
    acquisition: Literal["cache", "silent-refresh"] = "cache",
) -> SourceAvailability:
    return SourceAvailability("available", AvailableToken(
        key, "synthetic-token", expires_at if expires_at is not None else time.time() + 60,
        acquisition,
    ))


def test_warm_cache_and_recent_success_order_are_key_scoped() -> None:
    async def run() -> None:
        order: list[str] = []

        async def first(key: TokenKey) -> SourceAvailability:
            order.append("first")
            return hit(key) if len(order) > 2 else SourceAvailability("cache-miss")

        async def second(key: TokenKey) -> SourceAvailability:
            order.append("second")
            return hit(key)

        sources = (source("first", Provider(first)), source("second", Provider(second)))
        coordinator = AvailableTokenCoordinator(lambda key: sources)
        assert (await coordinator.lookup(KEY)).source == sources[1].identity
        order.clear()
        assert (await coordinator.lookup(KEY)).source == sources[1].identity
        assert order == ["second", "first"]
        for field in ("provider", "account", "tenant", "audience", "resource", "consumer", "binding"):
            order.clear()
            changes: dict[str, Any] = {field: "different"}
            result = await coordinator.lookup(replace(KEY, **changes))
            assert order == ["first", "second"]
            assert result.state == "available"
        order.clear()
        await coordinator.lookup(replace(KEY, scopes=("write",)))
        assert order == ["first", "second"]
        assert await coordinator.drain()
        assert coordinator.pending_count == 0

    asyncio.run(run())


def test_slow_preferred_does_not_starve_cached_alternative() -> None:
    async def run() -> None:
        blocked = asyncio.Event()
        preferred_ready = True

        async def preferred(key: TokenKey) -> SourceAvailability:
            if preferred_ready:
                return hit(key)
            await blocked.wait()
            return hit(key)

        sources = (source("preferred", Provider(preferred)), source("other", Provider(
            lambda key: asyncio.sleep(0, result=hit(key)))),
        )
        coordinator = AvailableTokenCoordinator(lambda key: sources, budget=0.1)
        assert (await coordinator.lookup(KEY)).source == sources[0].identity
        preferred_ready = False
        result = await asyncio.wait_for(coordinator.lookup(KEY), 0.5)
        assert result.source == sources[1].identity
        assert await coordinator.drain()
        assert not blocked.is_set()

    asyncio.run(run())


@pytest.mark.parametrize("response,state", [
    (SourceAvailability("cache-miss"), "cache-exhausted"),
    (SourceAvailability("unavailable"), "unavailable"),
    (SourceAvailability("uncertain"), "uncertain"),
    (SourceAvailability("unsupported"), "unavailable"),
    (None, "uncertain"),
])
def test_outcomes_are_not_interactive_fallbacks(
    response: SourceAvailability | None, state: str,
) -> None:
    async def run() -> None:
        provider = Provider(lambda key: asyncio.sleep(0, result=response))
        coordinator = AvailableTokenCoordinator(lambda key: (source("one", provider),))
        result = await coordinator.lookup(KEY)
        assert result.state == state
        assert result.token is None
        assert len(provider.calls) == 1
        assert await coordinator.drain()

    asyncio.run(run())


def test_no_source_and_legacy_provider_do_not_call_resolve() -> None:
    class Legacy:
        async def resolve(self, *args: object, **kwargs: object) -> None:
            pytest.fail("opaque legacy resolution is not an available-token API")

    async def run() -> None:
        coordinator = AvailableTokenCoordinator(lambda key: ())
        assert (await coordinator.lookup(KEY)).state == "no-source"
        legacy: Any = Legacy()
        coordinator = AvailableTokenCoordinator(lambda key: (source("legacy", legacy),))
        result = await coordinator.lookup(KEY)
        assert result.state == "unavailable"
        assert result.observations[0].state == "unsupported"
        assert coordinator.pending_count == 0

    asyncio.run(run())


def test_cache_misses_sweep_all_sources_and_revocation_prevents_dispatch() -> None:
    async def run() -> None:
        sources = (source("one"), source("two"), source("three"))
        coordinator = AvailableTokenCoordinator(lambda key: sources)
        result = await coordinator.lookup(KEY)
        assert result.state == "cache-exhausted"
        assert len(result.observations) == 3
        assert all(isinstance(item.provider, Provider) and len(item.provider.calls) == 1
                   for item in sources)

        calls = 0

        def snapshot(key: TokenKey) -> tuple[AvailableSource, ...]:
            nonlocal calls
            calls += 1
            return sources if calls == 1 else ()

        coordinator = AvailableTokenCoordinator(snapshot)
        assert (await coordinator.lookup(KEY)).state == "unavailable"
        assert all(isinstance(item.provider, Provider) and len(item.provider.calls) == 1
                   for item in sources)

    asyncio.run(run())


@pytest.mark.parametrize("changed", [
    {"account": "other"}, {"tenant": "other"}, {"audience": "other"},
    {"resource": "other"}, {"scopes": ("write",)}, {"consumer": "other"},
    {"binding": "other"}, {"provider": "other"},
])
def test_misbound_token_cannot_win(changed: dict[str, Any]) -> None:
    async def run() -> None:
        provider = Provider(lambda key: asyncio.sleep(0, result=hit(replace(key, **changed))))
        coordinator = AvailableTokenCoordinator(lambda key: (source("one", provider),))
        assert (await coordinator.lookup(KEY)).state == "unavailable"

    asyncio.run(run())


def test_expired_result_rejected_and_silent_refresh_remains_distinct() -> None:
    async def run() -> None:
        provider = Provider(lambda key: asyncio.sleep(0, result=hit(key, expires_at=1)))
        coordinator = AvailableTokenCoordinator(lambda key: (source("one", provider),))
        assert (await coordinator.lookup(KEY)).state == "unavailable"
        provider.callback = lambda key: asyncio.sleep(0, result=hit(
            key, acquisition="silent-refresh"))
        result = await coordinator.lookup(KEY)
        assert result.token is not None
        assert result.token.acquisition == "silent-refresh"
        assert "synthetic-token" not in repr(result)
        assert "synthetic-token" not in repr(result.token)

    asyncio.run(run())


@pytest.mark.parametrize("change", ["disconnect", "incarnation", "provider", "caller"])
def test_current_authority_rechecked_before_accepting_result(change: str) -> None:
    async def run() -> None:
        sources: list[AvailableSource] = []
        denied = False

        async def respond(key: TokenKey) -> SourceAvailability:
            nonlocal denied
            if change == "disconnect":
                sources.clear()
            elif change == "incarnation":
                sources[:] = [source("one", provider, incarnation="new-connection")]
            elif change == "provider":
                sources[:] = [source("one", Provider())]
            else:
                denied = True
            return hit(key)

        def snapshot(key: TokenKey) -> tuple[AvailableSource, ...]:
            if denied:
                raise PermissionError("caller-no-longer-authorized")
            return tuple(sources)

        provider = Provider(respond)
        sources[:] = [source("one", provider)]
        coordinator = AvailableTokenCoordinator(snapshot)
        if change == "caller":
            with pytest.raises(PermissionError):
                await coordinator.lookup(KEY)
        else:
            assert (await coordinator.lookup(KEY)).state == "unavailable"
        assert await coordinator.drain()

    asyncio.run(run())


def test_source_withdrawal_during_loser_cancellation_cannot_release_a_token() -> None:
    async def run() -> None:
        sources: list[AvailableSource] = []

        async def loser(key: TokenKey) -> None:
            try:
                await asyncio.Event().wait()
            finally:
                sources.clear()

        sources[:] = [
            source("winner", Provider(lambda key: asyncio.sleep(0, result=hit(key)))),
            source("loser", Provider(loser)),
        ]
        coordinator = AvailableTokenCoordinator(lambda key: tuple(sources))
        result = await coordinator.lookup(KEY)
        assert result.state != "available"
        assert result.token is None
        assert not coordinator._affinity
        assert await coordinator.drain()

    asyncio.run(run())


@pytest.mark.parametrize("denied_check", [2, 3])
def test_dispatch_policy_denial_aborts_lookup_and_cancels_started_reads(denied_check: int) -> None:
    async def run() -> None:
        cancelled = asyncio.Event()

        async def wait(key: TokenKey) -> None:
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        sources = (
            source("one", Provider(wait)),
            source("two", Provider(lambda key: asyncio.sleep(0, result=hit(key)))),
            source("three", Provider(lambda key: asyncio.sleep(0, result=hit(key)))),
        )
        checks = 0

        def snapshot(key: TokenKey) -> tuple[AvailableSource, ...]:
            nonlocal checks
            checks += 1
            if checks == denied_check:
                raise PermissionError("caller-denied-at-dispatch")
            return sources

        coordinator = AvailableTokenCoordinator(snapshot)
        with pytest.raises(PermissionError, match="caller-denied-at-dispatch"):
            await coordinator.lookup(KEY)
        assert isinstance(sources[0].provider, Provider)
        assert len(sources[0].provider.calls) == (denied_check == 3)
        assert all(isinstance(item.provider, Provider) and item.provider.calls == []
                   for item in sources[1:])
        assert await coordinator.drain()
        assert cancelled.is_set() == (denied_check == 3)
        assert coordinator.pending_count == 0
        assert not coordinator._affinity

    asyncio.run(run())


def test_provider_failure_is_not_an_owner_policy_denial_or_a_secret_diagnostic() -> None:
    async def run() -> None:
        async def broken(key: TokenKey) -> SourceAvailability:
            raise PermissionError("synthetic-secret-provider-detail")

        selected = source("one", Provider(broken))
        coordinator = AvailableTokenCoordinator(lambda key: (selected,))
        result = await coordinator.lookup(KEY)
        assert result.state == "uncertain"
        assert "synthetic-secret-provider-detail" not in repr(result)
        assert await coordinator.drain()

    asyncio.run(run())


def test_completed_authorized_alternative_survives_provisional_winner_withdrawal() -> None:
    async def run() -> None:
        sources: list[AvailableSource] = []

        async def ready(key: TokenKey) -> SourceAvailability:
            return hit(key)

        async def loser(key: TokenKey) -> None:
            try:
                await asyncio.Event().wait()
            finally:
                sources.pop(0)

        sources[:] = [
            source("first", Provider(ready)), source("second", Provider(ready)),
            source("loser", Provider(loser)),
        ]
        second = sources[1].identity
        coordinator = AvailableTokenCoordinator(lambda key: tuple(sources))
        result = await coordinator.lookup(KEY)
        assert result.state == "available"
        assert result.source == second
        assert await coordinator.drain()

    asyncio.run(run())


@pytest.mark.parametrize("delay", ["queued", "policy"])
def test_expired_phase_never_starts_queued_or_policy_delayed_provider_work(
    monkeypatch: pytest.MonkeyPatch, delay: str,
) -> None:
    async def run() -> None:
        loop = asyncio.get_running_loop()
        original_time = loop.time
        offset, calls = [0.0], 0
        provider = Provider(lambda key: asyncio.sleep(0, result=hit(key)))
        selected = source("one", provider)

        def expire() -> None:
            offset[0] = 31.0

        def snapshot(key: TokenKey) -> tuple[AvailableSource, ...]:
            nonlocal calls
            calls += 1
            if delay == "queued" and calls == 1:
                loop.call_soon(expire)
            elif delay == "policy" and calls == 2:
                expire()
            return (selected,)

        with monkeypatch.context() as clock:
            clock.setattr(loop, "time", lambda: original_time() + offset[0])
            coordinator = AvailableTokenCoordinator(snapshot, budget=1)
            result = await coordinator.lookup(KEY)
            assert result.state == "uncertain"
            assert provider.calls == []
            assert await coordinator.drain()
            assert coordinator.pending_count == 0

    asyncio.run(run())


@pytest.mark.parametrize("expiry", ["provider", "final-policy", "loser-cleanup"])
def test_late_completion_and_final_cleanup_expiry_remain_uncertain(
    monkeypatch: pytest.MonkeyPatch, expiry: str,
) -> None:
    async def run() -> None:
        loop = asyncio.get_running_loop()
        original_time = loop.time
        offset, checks = [0.0], 0

        async def ready(key: TokenKey) -> SourceAvailability:
            await asyncio.sleep(0)
            if expiry == "provider":
                offset[0] = 31.0
            return hit(key)

        async def loser(key: TokenKey) -> None:
            try:
                await asyncio.Event().wait()
            finally:
                offset[0] = 31.0

        sources: tuple[AvailableSource, ...] = (source("ready", Provider(ready)),)
        if expiry == "loser-cleanup":
            sources += (source("loser", Provider(loser)),)

        def snapshot(key: TokenKey) -> tuple[AvailableSource, ...]:
            nonlocal checks
            checks += 1
            if expiry == "final-policy" and checks == 4:
                offset[0] = 31.0
            return sources

        with monkeypatch.context() as clock:
            clock.setattr(loop, "time", lambda: original_time() + offset[0])
            coordinator = AvailableTokenCoordinator(snapshot, budget=1)
            result = await coordinator.lookup(KEY)
            assert result.state == "uncertain"
            assert result.token is None
            assert any(item.state == "uncertain" for item in result.observations)
            assert not coordinator._affinity
            assert await coordinator.drain()

    asyncio.run(run())


def test_one_phase_deadline_retains_cancellation_resistant_probe_custody() -> None:
    async def run() -> None:
        release = asyncio.Event()
        cancelled = asyncio.Event()

        async def stalled(key: TokenKey) -> SourceAvailability:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
            return hit(key)

        providers = (Provider(stalled), Provider(lambda key: asyncio.sleep(
            0, result=SourceAvailability("cache-miss"))))
        sources = tuple(source(str(index), provider) for index, provider in enumerate(providers))
        coordinator = AvailableTokenCoordinator(lambda key: sources, budget=0.02)
        started = asyncio.get_running_loop().time()
        result = await asyncio.wait_for(coordinator.lookup(KEY), 0.5)
        elapsed = asyncio.get_running_loop().time() - started
        assert result.state == "uncertain"
        assert elapsed < 0.5
        await cancelled.wait()
        assert not await coordinator.drain(timeout=0.01)
        assert coordinator.pending_count == 1
        assert (await coordinator.lookup(KEY)).state == "uncertain"
        assert len(providers[0].calls) == 1
        release.set()
        assert await coordinator.drain()
        await asyncio.sleep(0)
        assert coordinator.pending_count == 0
        assert (await coordinator.lookup(KEY)).state == "uncertain"
        await cancelled.wait()
        release.set()
        assert await coordinator.drain()

    asyncio.run(run())


def test_reconnect_cannot_duplicate_an_unsettled_logical_source_refresh() -> None:
    async def run() -> None:
        release, cancelled = asyncio.Event(), asyncio.Event()

        async def stalled(key: TokenKey) -> SourceAvailability:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
            return hit(key)

        old, replacement = Provider(stalled), Provider(
            lambda key: asyncio.sleep(0, result=hit(key)))
        sources = [source("one", old)]
        coordinator = AvailableTokenCoordinator(lambda key: tuple(sources), budget=0.02)
        assert (await coordinator.lookup(KEY)).state == "uncertain"
        await cancelled.wait()
        sources[:] = [source("one", replacement, incarnation="new-connection")]
        assert (await coordinator.lookup(KEY)).state == "uncertain"
        assert replacement.calls == []
        assert coordinator.pending_count == 1
        release.set()
        assert await coordinator.drain()
        result = await coordinator.lookup(KEY)
        assert result.state == "available"
        assert result.source == sources[0].identity
        assert len(old.calls) == len(replacement.calls) == 1
        assert await coordinator.drain()

    asyncio.run(run())


def test_caller_cancellation_and_stop_cancel_owned_reads_without_replay() -> None:
    async def run() -> None:
        entered = asyncio.Event()

        async def wait(key: TokenKey) -> None:
            entered.set()
            await asyncio.Event().wait()

        provider = Provider(wait)
        coordinator = AvailableTokenCoordinator(lambda key: (source("one", provider),))
        task: asyncio.Task[AvailableLookup] = asyncio.create_task(coordinator.lookup(KEY))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert await coordinator.drain()
        assert len(provider.calls) == 1
        coordinator.stop()
        with pytest.raises(RuntimeError, match="closed"):
            await coordinator.lookup(KEY)

    asyncio.run(run())


def test_probe_capacity_and_affinity_are_bounded_without_new_effects() -> None:
    async def run() -> None:
        entered = asyncio.Event()

        async def wait(key: TokenKey) -> None:
            entered.set()
            await asyncio.Event().wait()

        provider = Provider(wait)
        coordinator = AvailableTokenCoordinator(
            lambda key: (source("one", provider),), budget=0.05, probe_limit=1,
        )
        running: asyncio.Task[AvailableLookup] = asyncio.create_task(coordinator.lookup(KEY))
        await entered.wait()
        assert (await coordinator.lookup(replace(KEY, consumer="other"))).state == "uncertain"
        assert len(provider.calls) == 1
        coordinator.stop()
        assert (await running).state == "uncertain"
        assert await coordinator.drain()

        fast = Provider(lambda key: asyncio.sleep(0, result=hit(key)))
        coordinator = AvailableTokenCoordinator(
            lambda key: (source("one", fast),), affinity_limit=1,
        )
        await coordinator.lookup(KEY)
        await coordinator.lookup(replace(KEY, consumer="other"))
        assert len(coordinator._affinity) == 1

    asyncio.run(run())


def test_maximum_inventory_has_bounded_inprocess_policy_work() -> None:
    async def run() -> None:
        sources = tuple(source(str(index)) for index in range(32))
        checks = 0

        def snapshot(key: TokenKey) -> tuple[AvailableSource, ...]:
            nonlocal checks
            checks += 1
            return sources

        coordinator = AvailableTokenCoordinator(snapshot)
        started = time.perf_counter()
        result = await coordinator.lookup(KEY)
        elapsed = time.perf_counter() - started
        assert result.state == "cache-exhausted"
        assert all(isinstance(item.provider, Provider) and len(item.provider.calls) == 1
                   for item in sources)
        assert checks <= 1 + 3 * len(sources)
        assert await coordinator.drain()
        assert coordinator.pending_count == 0
        print(f"availability-source-sample sources={len(sources)} "
              f"policy_callbacks={checks} elapsed_seconds={elapsed:.6f}")

    asyncio.run(run())


def test_reconnected_controller_keeps_affinity_without_reusing_old_binding() -> None:
    async def run() -> None:
        order: list[str] = []

        async def first(key: TokenKey) -> SourceAvailability:
            order.append("first")
            return SourceAvailability("cache-miss")

        async def second(key: TokenKey) -> SourceAvailability:
            order.append("second")
            return hit(key)

        old = source("second", Provider(second))
        sources = [source("first", Provider(first)), old]
        coordinator = AvailableTokenCoordinator(lambda key: tuple(sources))
        await coordinator.lookup(KEY)
        order.clear()
        current = source("second", Provider(second), incarnation="connection-b")
        sources[1] = current
        result = await coordinator.lookup(KEY)
        assert order == ["second", "first"]
        assert result.source == current.identity
        assert isinstance(old.provider, Provider)
        assert len(old.provider.calls) == 1

    asyncio.run(run())

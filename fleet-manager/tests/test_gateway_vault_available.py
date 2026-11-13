"""Available-token selection never implicitly starts an authentication ceremony."""

import asyncio
import time
from dataclasses import replace

import pytest

from fleet_manager.gateway_vault_available import (
    AVAILABLE_CONTRACT, AvailableSource, AvailableToken, AvailableTokenCoordinator,
    SourceAvailability, SourceIdentity, TokenKey,
)


KEY = TokenKey("provider-a", "account-a", "tenant-a", "audience-a", "resource-a",
               ("read",), "consumer-a", "binding-a")


class Provider:
    contract = AVAILABLE_CONTRACT

    def __init__(self, callback=None):
        self.callback, self.calls = callback, []

    async def get_available(self, key, *, deadline):
        self.calls.append((key, deadline))
        return await self.callback(key) if self.callback else SourceAvailability("cache-miss")


def source(name, provider=None, *, incarnation="connection-a"):
    return AvailableSource(SourceIdentity(name, incarnation, "source-a"), provider or Provider())


def hit(key=KEY, *, expires_at=None, acquisition="cache"):
    return SourceAvailability("available", AvailableToken(
        key, "synthetic-token", expires_at if expires_at is not None else time.time() + 60,
        acquisition,
    ))


def test_warm_cache_and_recent_success_order_are_key_scoped():
    async def run():
        order = []

        async def first(key):
            order.append("first")
            return hit(key) if len(order) > 2 else SourceAvailability("cache-miss")

        async def second(key):
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
            result = await coordinator.lookup(replace(KEY, **{field: "different"}))
            assert order == ["first", "second"]
            assert result.state == "available"
        order.clear()
        await coordinator.lookup(replace(KEY, scopes=("write",)))
        assert order == ["first", "second"]
        assert await coordinator.drain()
        assert coordinator.pending_count == 0

    asyncio.run(run())


def test_slow_preferred_does_not_starve_cached_alternative():
    async def run():
        blocked = asyncio.Event()
        preferred_ready = True

        async def preferred(key):
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
def test_outcomes_are_not_interactive_fallbacks(response, state):
    async def run():
        provider = Provider(lambda key: asyncio.sleep(0, result=response))
        coordinator = AvailableTokenCoordinator(lambda key: (source("one", provider),))
        result = await coordinator.lookup(KEY)
        assert result.state == state
        assert result.token is None
        assert len(provider.calls) == 1
        assert await coordinator.drain()

    asyncio.run(run())


def test_no_source_and_legacy_provider_do_not_call_resolve():
    class Legacy:
        async def resolve(self, *args, **kwargs):
            pytest.fail("opaque legacy resolution is not an available-token API")

    async def run():
        coordinator = AvailableTokenCoordinator(lambda key: ())
        assert (await coordinator.lookup(KEY)).state == "no-source"
        coordinator = AvailableTokenCoordinator(lambda key: (source("legacy", Legacy()),))
        result = await coordinator.lookup(KEY)
        assert result.state == "unavailable"
        assert result.observations[0].state == "unsupported"
        assert coordinator.pending_count == 0

    asyncio.run(run())


def test_cache_misses_sweep_all_sources_and_revocation_prevents_dispatch():
    async def run():
        sources = (source("one"), source("two"), source("three"))
        coordinator = AvailableTokenCoordinator(lambda key: sources)
        result = await coordinator.lookup(KEY)
        assert result.state == "cache-exhausted"
        assert len(result.observations) == 3
        assert all(len(item.provider.calls) == 1 for item in sources)

        calls = 0

        def snapshot(key):
            nonlocal calls
            calls += 1
            return sources if calls == 1 else ()

        coordinator = AvailableTokenCoordinator(snapshot)
        assert (await coordinator.lookup(KEY)).state == "unavailable"
        assert all(len(item.provider.calls) == 1 for item in sources)

    asyncio.run(run())


@pytest.mark.parametrize("changed", [
    {"account": "other"}, {"tenant": "other"}, {"audience": "other"},
    {"resource": "other"}, {"scopes": ("write",)}, {"consumer": "other"},
    {"binding": "other"}, {"provider": "other"},
])
def test_misbound_token_cannot_win(changed):
    async def run():
        provider = Provider(lambda key: asyncio.sleep(0, result=hit(replace(key, **changed))))
        coordinator = AvailableTokenCoordinator(lambda key: (source("one", provider),))
        assert (await coordinator.lookup(KEY)).state == "unavailable"

    asyncio.run(run())


def test_expired_result_rejected_and_silent_refresh_remains_distinct():
    async def run():
        provider = Provider(lambda key: asyncio.sleep(0, result=hit(key, expires_at=1)))
        coordinator = AvailableTokenCoordinator(lambda key: (source("one", provider),))
        assert (await coordinator.lookup(KEY)).state == "unavailable"
        provider.callback = lambda key: asyncio.sleep(0, result=hit(
            key, acquisition="silent-refresh"))
        result = await coordinator.lookup(KEY)
        assert result.token.acquisition == "silent-refresh"
        assert "synthetic-token" not in repr(result)
        assert "synthetic-token" not in repr(result.token)

    asyncio.run(run())


@pytest.mark.parametrize("change", ["disconnect", "incarnation", "provider", "caller"])
def test_current_authority_rechecked_before_accepting_result(change):
    async def run():
        sources, denied = [], False

        async def respond(key):
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

        def snapshot(key):
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


def test_source_withdrawal_during_loser_cancellation_cannot_release_a_token():
    async def run():
        sources = []

        async def loser(key):
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


@pytest.mark.parametrize("delay", ["queued", "policy"])
def test_expired_phase_never_starts_queued_or_policy_delayed_provider_work(monkeypatch, delay):
    async def run():
        loop = asyncio.get_running_loop()
        original_time = loop.time
        offset, calls = [0.0], 0
        provider = Provider(lambda key: asyncio.sleep(0, result=hit(key)))
        selected = source("one", provider)

        def expire():
            offset[0] = 31.0

        def snapshot(key):
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


def test_one_phase_deadline_retains_cancellation_resistant_probe_custody():
    async def run():
        release = asyncio.Event()
        cancelled = asyncio.Event()

        async def stalled(key):
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


def test_caller_cancellation_and_stop_cancel_owned_reads_without_replay():
    async def run():
        entered = asyncio.Event()

        async def wait(key):
            entered.set()
            await asyncio.Event().wait()

        provider = Provider(wait)
        coordinator = AvailableTokenCoordinator(lambda key: (source("one", provider),))
        task = asyncio.create_task(coordinator.lookup(KEY))
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


def test_probe_capacity_and_affinity_are_bounded_without_new_effects():
    async def run():
        entered = asyncio.Event()

        async def wait(key):
            entered.set()
            await asyncio.Event().wait()

        provider = Provider(wait)
        coordinator = AvailableTokenCoordinator(
            lambda key: (source("one", provider),), budget=0.05, probe_limit=1,
        )
        running = asyncio.create_task(coordinator.lookup(KEY))
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


def test_reconnected_controller_keeps_affinity_without_reusing_old_binding():
    async def run():
        order = []

        async def first(key):
            order.append("first")
            return SourceAvailability("cache-miss")

        async def second(key):
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
        assert len(old.provider.calls) == 1

    asyncio.run(run())

"""Tests for :mod:`agent_bridge.agent_registry_cache` (pivot-streaming-
transport Phase 3b: the agent-bridge daemon-side roster cache).

Covers the Validation Plan's named 3b acceptance criteria: an uninitialized
namespace never silently published as complete; recovery from a hung/
crashed background refresh via the freshness deadline + opportunistic
single-flight join; a provider added/removed/replaced at runtime (including
the replacement-constructor-failure path keeping the prior resolver/cache
entry authoritative); the default (no ``require_complete``) response shape
staying completely unchanged even when the cache is badly incomplete;
concurrent refreshes of the same namespace (single-flight plus generation-
guarded publication); the `503` fail-closed contract for daemon startup
before any discovery, initial-scan retry exhaustion, and a stale discovery
generation; and the zero-downtime cutover warm-up gate.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from agent_bridge.agent_registry import NamespaceAgentInfo
from agent_bridge.agent_registry_cache import AgentRosterCache, _NamespaceEntry
from agent_bridge.agent_registry_discovery import DiscoveryResult
from agent_bridge.agent_registry_resolver import AgentResolver


class _ControllableResolver:
    """A namespace resolver whose ``list()`` outcome is controlled per-call
    by the test via a mutable queue of callables."""

    def __init__(self, prefix: str):
        self._prefix = prefix
        self.outcomes: list = []
        self.call_count = 0
        self.invalidate_count = 0

    @property
    def prefix(self) -> str:
        return self._prefix

    async def list(self):
        self.call_count += 1
        outcome = self.outcomes.pop(0) if self.outcomes else []
        if isinstance(outcome, BaseException):
            raise outcome
        if callable(outcome):
            return await outcome()
        return outcome

    def invalidate_list_cache(self) -> None:
        self.invalidate_count += 1

    async def resolve(self, name):  # pragma: no cover - unused here
        raise NotImplementedError

    async def ensure_ready(self, name):  # pragma: no cover - unused
        raise NotImplementedError


def _agent(name: str) -> NamespaceAgentInfo:
    return NamespaceAgentInfo(
        name=name, display_name=name, description="", state="available",
    )


def _make_resolver_with_namespace(prefix: str = "ns") -> tuple[AgentResolver, _ControllableResolver]:
    resolver = AgentResolver({}, {})
    ns_resolver = _ControllableResolver(prefix)
    resolver.register_namespace_resolver(ns_resolver)
    return resolver, ns_resolver


# -- default watchdog_timeout must never undercut the scan's own timeout ------


def test_default_watchdog_timeout_floored_at_namespace_list_resolver_timeout(monkeypatch):
    """A short ``refresh_interval`` (e.g. a 1s ``agent_roster_cache_
    interval``, yielding a cadence-based 5s bound) must never derive a
    watchdog timeout shorter than the configured per-resolver ``list()``
    timeout -- otherwise a healthy, merely slow provider finishing well
    within its own configured timeout would get cancelled by the watchdog
    on every single cycle before it ever completes."""
    monkeypatch.setenv("AGENT_BRIDGE_NAMESPACE_LIST_RESOLVER_TIMEOUT", "8")
    resolver = AgentResolver({}, {})
    cache = AgentRosterCache(resolver, refresh_interval=1.0)
    assert cache._watchdog_timeout > 8.0


def test_default_watchdog_timeout_not_floored_when_resolver_timeout_disabled(monkeypatch):
    """With the per-resolver timeout explicitly disabled (``0``/``off``),
    there is no legitimate scan-duration floor to respect -- the plain
    cadence-based default applies."""
    monkeypatch.setenv("AGENT_BRIDGE_NAMESPACE_LIST_RESOLVER_TIMEOUT", "0")
    resolver = AgentResolver({}, {})
    cache = AgentRosterCache(resolver, refresh_interval=1.0)
    assert cache._watchdog_timeout == 5.0


def test_explicit_watchdog_timeout_is_never_overridden_by_the_floor(monkeypatch):
    """An explicitly-passed ``watchdog_timeout`` is the operator's own
    deliberate choice -- the resolver-timeout floor only applies to the
    *derived default*, never overriding an explicit value."""
    monkeypatch.setenv("AGENT_BRIDGE_NAMESPACE_LIST_RESOLVER_TIMEOUT", "8")
    resolver = AgentResolver({}, {})
    cache = AgentRosterCache(resolver, refresh_interval=1.0, watchdog_timeout=0.1)
    assert cache._watchdog_timeout == 0.1


# -- uninitialized / warm-up ---------------------------------------------------


@pytest.mark.asyncio
async def test_uninitialized_namespace_never_published_as_complete():
    """Mid-scan (before the very first scan attempt has resolved), the
    namespace must read as UNINITIALIZED internally -- never silently
    treated as a clean, empty-but-apparently-authoritative roster."""
    resolver, ns = _make_resolver_with_namespace()
    started = asyncio.Event()
    release = asyncio.Event()

    async def _slow_list():
        started.set()
        await release.wait()
        return [_agent("a1")]

    ns.outcomes = [_slow_list]
    cache = AgentRosterCache(resolver, refresh_interval=60.0)

    task = asyncio.create_task(cache.get_snapshot())
    await started.wait()
    assert cache._entries["ns"].state.value == "uninitialized"

    release.set()
    snapshot = await task
    assert snapshot.complete is True
    assert any(r["name"] == "ns:a1" for r in snapshot.rows)


@pytest.mark.asyncio
async def test_wait_until_warm_blocks_until_first_scan_attempt_and_discovery():
    resolver, ns = _make_resolver_with_namespace()
    ns.outcomes = [[_agent("a1")]]
    cache = AgentRosterCache(resolver, refresh_interval=60.0)
    cache.start()
    try:
        warmed = await cache.wait_until_warm(timeout=5.0)
        assert warmed is True
        snapshot = await cache.get_snapshot()
        assert snapshot.complete is True
        assert any(r["name"] == "ns:a1" for r in snapshot.rows)
    finally:
        await cache.stop()


@pytest.mark.asyncio
async def test_wait_until_warm_does_not_block_forever_on_a_persistently_broken_namespace():
    """A namespace that is reachably, persistently broken (every scan fails)
    must still let warm-up complete (FAILED counts as "attempted"), not hang
    cutover promotion forever."""
    resolver, ns = _make_resolver_with_namespace()
    ns.outcomes = [RuntimeError("boom")] * 5
    cache = AgentRosterCache(resolver, refresh_interval=60.0)
    cache.start()
    try:
        warmed = await cache.wait_until_warm(timeout=5.0)
        assert warmed is True
    finally:
        await cache.stop()


@pytest.mark.asyncio
async def test_drop_unfinished_inflight_bumps_generation_so_late_result_is_discarded():
    """Mirrors the force-fail fix below: `_drop_unfinished_inflight` (the
    watchdog's own abandon-and-restart cleanup) must also invalidate the
    generation of a scan it gives up on right when it gives up -- not
    leave that to whenever a replacement scan happens to start. A
    cancellation-resistant resolver can finish *between* this cleanup and
    that later `_refresh_namespace()` call; until the generation actually
    moves, the abandoned task's own captured generation still matches the
    live entry's, so its late result would otherwise pass the publication
    guard and briefly publish stale rows under a fresh timestamp."""
    resolver, ns = _make_resolver_with_namespace()
    cache = AgentRosterCache(resolver, refresh_interval=60.0)
    release = asyncio.Event()

    async def _slow_then_succeeds():
        await release.wait()
        return [_agent("late")]

    ns.outcomes = [_slow_then_succeeds]
    task = cache._refresh_namespace("ns")
    await asyncio.sleep(0)  # let the scan actually start and block on release

    entry = cache._entries["ns"]
    generation_before = entry.generation
    assert not task.done()

    cache._drop_unfinished_inflight({"ns": task})

    assert entry.generation == generation_before + 1
    assert cache._inflight.get("ns") is None
    assert cache._inflight_token.get("ns") is None

    # Let the orphaned task finally complete -- its result must be
    # discarded by the (now-bumped) generation guard, never published.
    release.set()
    await asyncio.wait_for(task, timeout=2.0)
    assert entry.state.value == "uninitialized"
    assert entry.rows == []


@pytest.mark.asyncio
async def test_force_fail_bumps_generation_so_an_abandoned_scans_late_result_is_discarded():
    """Same bug, the other call site: an abandoned (cancellation-resistant)
    scan `force_fail_uninitialized_namespaces()` gives up on must have its
    generation invalidated immediately, not left for a future scan to bump
    -- otherwise the orphaned task's eventual, belated `ok=True` result
    would still match the live entry's generation and silently flip the
    just-forced FAILED state back to OK.

    Needs a genuinely cancellation-*resistant* scan (one `force_fail`'s
    bounded wait actually times out on, taking the abandon path this fix
    targets) rather than one that completes via ordinary cancellation --
    a plain `Event.wait()` propagates `CancelledError` immediately, which
    only exercises `force_fail`'s unconditional FAILED tail, never the
    generation-bump branch under test."""
    resolver, ns = _make_resolver_with_namespace()
    cache = AgentRosterCache(resolver, refresh_interval=60.0)
    cache._CANCELLATION_GRACE = 0.05
    release = asyncio.Event()

    async def _resists_cancellation():
        while True:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                if release.is_set():
                    raise
                continue  # swallow and keep running -- cancellation-resistant

    ns.outcomes = [_resists_cancellation]
    cache._refresh_namespace("ns")
    await asyncio.sleep(0)  # let the scan actually start
    entry = cache._entries["ns"]
    stale_generation = entry.generation

    await cache.force_fail_uninitialized_namespaces()
    assert entry.state.value == "failed"
    # The abandoned scan's own captured generation is now stale -- proving
    # the fix: without it, a belated publish under `stale_generation` would
    # still pass the guard and overwrite the just-forced FAILED state.
    assert entry.generation != stale_generation

    # Directly exercise the same guard the orphaned task's own belated
    # publish would hit, under its original (now-stale) generation --
    # confirms it is discarded rather than flipping FAILED back to OK.
    ns.outcomes = [[_agent("late")]]
    await cache._do_scan_namespace("ns", ns, stale_generation)
    assert entry.state.value == "failed"
    assert entry.rows == []

    release.set()
    await cache.stop()




@pytest.mark.asyncio
async def test_get_snapshot_bounds_the_join_so_a_hung_scan_cannot_hang_the_request():
    """A namespace single-flight task `get_snapshot()` joins can be the
    exact task object a concurrent watchdog-triggered restart later
    force-abandons (dropped from `_inflight` for *future* callers only) if
    its resolver is cancellation-resistant -- this request's own shielded
    await on that same, now-orphaned task must never hang forever.
    Bounding the join at `_watchdog_timeout` ensures `get_snapshot()`
    always returns, with the namespace reported incomplete, rather than
    blocking the response indefinitely."""
    resolver, ns = _make_resolver_with_namespace()
    hang_forever = asyncio.Event()

    async def _hang():
        await hang_forever.wait()
        return [_agent("never")]  # pragma: no cover - never reached

    ns.outcomes = [_hang]
    cache = AgentRosterCache(resolver, refresh_interval=60.0, watchdog_timeout=0.1)

    start = time.monotonic()
    snapshot = await asyncio.wait_for(cache.get_snapshot(), timeout=2.0)
    elapsed = time.monotonic() - start

    assert elapsed < 1.0  # bounded well under the outer 2.0s test timeout
    assert "ns" in snapshot.incomplete_namespaces
    assert snapshot.complete is False

    hang_forever.set()
    await cache.stop()


@pytest.mark.asyncio
async def test_force_fail_uninitialized_namespaces_cancels_a_hung_scan():
    """A namespace resolver with its own timeout disabled can hang past
    `wait_until_warm`'s bound entirely, leaving it genuinely UNINITIALIZED
    forever. `force_fail_uninitialized_namespaces()` must cancel that
    in-flight scan and mark the namespace FAILED -- a terminal state,
    never left in limbo -- so readiness can safely proceed."""
    resolver, ns = _make_resolver_with_namespace()
    hang_forever = asyncio.Event()

    async def _hang():
        await hang_forever.wait()
        return [_agent("never")]  # pragma: no cover - never reached

    ns.outcomes = [_hang]
    cache = AgentRosterCache(resolver, refresh_interval=60.0)
    cache.start()
    try:
        warmed = await cache.wait_until_warm(timeout=0.3)
        assert warmed is False
        assert cache._entries["ns"].state.value == "uninitialized"

        await cache.force_fail_uninitialized_namespaces()
        assert cache._entries["ns"].state.value == "failed"
    finally:
        hang_forever.set()
        await cache.stop()


@pytest.mark.asyncio
async def test_force_fail_does_not_orphan_a_replacement_scan_installed_mid_wait():
    """While `force_fail_uninitialized_namespaces()` awaits a cancelled
    scan's bounded cancellation, a concurrent caller can already install a
    brand-new, legitimately in-flight scan under the same namespace key
    (e.g. after its own reconcile detects a provider swap). The stale,
    abandoned task's own tracking must be dropped -- never the newer
    replacement's, which would otherwise be orphaned (unreachable to
    shutdown and later single-flight joins)."""
    resolver, ns = _make_resolver_with_namespace()
    cache = AgentRosterCache(resolver, refresh_interval=60.0)
    cache._CANCELLATION_GRACE = 0.1
    cache._entries["ns"] = _NamespaceEntry(provider_ref=ns)
    replacement_installed = asyncio.Event()
    cancelled_once = False

    async def _old_scan_that_resists_cancellation_once():
        nonlocal cancelled_once
        while True:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                if not cancelled_once:
                    cancelled_once = True
                    # Simulate a concurrent caller installing a brand-new,
                    # legitimately in-flight scan for "ns" under the same
                    # key while this cancellation wait is still ongoing --
                    # swallow this first cancellation to force force_fail's
                    # own bounded wait to time out.
                    new_task = asyncio.create_task(asyncio.sleep(3600))
                    cache._inflight["ns"] = new_task
                    cache._inflight_token["ns"] = ns
                    replacement_installed.set()
                    continue
                raise

    old_task = asyncio.create_task(_old_scan_that_resists_cancellation_once())
    cache._inflight["ns"] = old_task
    cache._inflight_token["ns"] = ns
    await asyncio.sleep(0)  # let the task actually start and reach its await

    await cache.force_fail_uninitialized_namespaces()
    assert replacement_installed.is_set()

    # The replacement task's own tracking must still be intact -- never
    # orphaned by force_fail's cleanup of the OLD, abandoned task.
    replacement = cache._inflight.get("ns")
    assert replacement is not None
    assert not replacement.done()

    old_task.cancel()
    replacement.cancel()
    for t in (old_task, replacement):
        try:
            await t
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_force_fail_rereconciles_after_a_concurrent_swap_mid_cancellation():
    """A concurrent caller (e.g. an already-served request's own
    `get_snapshot()`/`_reconcile_namespace_set()` -- topology is marked
    ready before warm-up completes) can replace a namespace's entry object
    outright -- a provider swap -- while `force_fail_uninitialized_
    namespaces()` is still awaiting that namespace's cancelled scan.
    Mutating the stale, now-detached entry object captured before the
    await must not be mistaken for actually failing the *current*, live
    entry."""
    resolver, ns = _make_resolver_with_namespace()
    cache = AgentRosterCache(resolver, refresh_interval=60.0)
    cache._entries["ns"] = _NamespaceEntry(provider_ref=ns)
    swapped = asyncio.Event()

    async def _scan_that_swaps_on_cancel():
        try:
            await asyncio.Event().wait()  # never set -- only exits via cancel
        except asyncio.CancelledError:
            # Simulate a concurrent caller's provider swap landing exactly
            # while force_fail is awaiting this task's own cancellation.
            resolver.unregister_namespace_resolver("ns")
            new_ns = _ControllableResolver("ns")
            resolver.register_namespace_resolver(new_ns)
            cache._reconcile_namespace_set()
            swapped.set()
            raise

    task = asyncio.create_task(_scan_that_swaps_on_cancel())
    cache._inflight["ns"] = task
    cache._inflight_token["ns"] = ns
    await asyncio.sleep(0)  # let the task actually start and reach its await

    await cache.force_fail_uninitialized_namespaces()
    assert swapped.is_set()

    # The *live*, current entry (now backing the swapped-in new provider)
    # must end up FAILED -- not merely the old, now-detached entry object.
    assert cache._entries["ns"].provider_ref is not ns
    assert cache._entries["ns"].state.value == "failed"


@pytest.mark.asyncio
async def test_caller_cancellation_does_not_abort_shared_single_flight_scan():
    """A request that observes a stale namespace and joins its single-flight
    refresh must not abort that refresh for other concurrent waiters (or
    the periodic loop) just because the request itself gets cancelled
    (e.g. a client disconnect) -- the shared task must be shielded from a
    caller-side cancellation it was never meant to control."""
    resolver, ns = _make_resolver_with_namespace()
    started = asyncio.Event()
    release = asyncio.Event()

    async def _slow_list():
        started.set()
        await release.wait()
        return [_agent("a1")]

    ns.outcomes = [_slow_list]
    cache = AgentRosterCache(resolver, refresh_interval=60.0)

    task = asyncio.create_task(cache.get_snapshot())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # The underlying scan must still be running (not aborted by the
    # cancelled caller) -- a second, fresh snapshot request joins the SAME
    # single-flight scan and gets its real result once it completes.
    assert "ns" in cache._inflight
    second = asyncio.create_task(cache.get_snapshot())
    await asyncio.sleep(0.02)
    release.set()
    snapshot = await second
    assert any(r["name"] == "ns:a1" for r in snapshot.rows)


# -- recovery from a stalled/failed refresh ------------------------------------


@pytest.mark.asyncio
async def test_failed_scan_retains_last_known_good_internally_but_marks_incomplete():
    resolver, ns = _make_resolver_with_namespace()
    ns.outcomes = [[_agent("a1")]]
    cache = AgentRosterCache(resolver, refresh_interval=60.0)
    first = await cache.get_snapshot()
    assert first.complete is True
    assert any(r["name"] == "ns:a1" for r in first.rows)

    # Next scan fails -- the default response must NOT substitute the stale
    # last-known-good row; it must skip the namespace exactly like today.
    # Simulate staleness so the opportunistic join actually triggers a
    # rescan (force_refresh no longer rescans an already-fresh namespace).
    cache._entries["ns"].last_refreshed_monotonic -= 10_000
    ns.outcomes = [RuntimeError("boom")]
    second = await cache.get_snapshot()
    assert second.incomplete_namespaces == ["ns"]
    assert not any(r.get("name", "").startswith("ns:") for r in second.rows)


@pytest.mark.asyncio
async def test_stale_entry_opportunistically_rescans_without_force_refresh():
    """Past its freshness deadline, a plain (non-force_refresh) GET must
    still trigger a real rescan -- the opportunistic-join half of the
    design, independent of `force_refresh`."""
    resolver, ns = _make_resolver_with_namespace()
    ns.outcomes = [[_agent("a1")]]
    # refresh_interval is floor-clamped to 1.0s internally, so the shortest
    # freshness deadline this cache can have is also ~1.0s.
    cache = AgentRosterCache(resolver, refresh_interval=1.0, freshness_multiplier=1.0)
    await cache.get_snapshot()
    assert ns.call_count == 1

    await asyncio.sleep(1.1)
    ns.outcomes = [[_agent("a1"), _agent("a2")]]
    snapshot = await cache.get_snapshot()  # no force_refresh
    assert ns.call_count == 2
    assert {r["name"] for r in snapshot.rows} == {"ns:a1", "ns:a2"}


@pytest.mark.asyncio
async def test_get_snapshot_preserves_resolver_registration_order():
    """`list_agents_async()`'s own (non-cached) rows/incomplete_namespaces
    preserve namespace *registration* order, never alphabetical --
    the cached path's default response must match that byte-for-byte,
    not silently resort to sorted() order for namespaces registered in a
    non-alphabetical sequence."""
    resolver = AgentResolver({}, {})
    zzz = _ControllableResolver("zzz")
    aaa = _ControllableResolver("aaa")
    # Deliberately registered out of alphabetical order.
    resolver.register_namespace_resolver(zzz)
    resolver.register_namespace_resolver(aaa)
    zzz.outcomes = [RuntimeError("boom")]  # incomplete
    aaa.outcomes = [RuntimeError("boom")]  # incomplete
    cache = AgentRosterCache(resolver, refresh_interval=60.0)

    snapshot = await cache.get_snapshot()

    assert snapshot.incomplete_namespaces == ["zzz", "aaa"]


@pytest.mark.asyncio
async def test_get_snapshot_reconciles_again_after_the_join_before_assembling_rows():
    """While `get_snapshot()`'s join (`asyncio.gather`) is awaiting, an
    out-of-band `refresh_provider_resolvers()` call (bypassing this cache
    entirely) can still replace or remove a provider after its *old* scan
    already published. The final assembly must reconcile again after the
    join, not serve a provider's last-known-good rows once after it was
    already gone or replaced."""
    resolver, ns = _make_resolver_with_namespace()
    gate = asyncio.Event()

    async def _slow_list():
        await gate.wait()
        return [_agent("old")]

    ns.outcomes = [_slow_list]
    cache = AgentRosterCache(resolver, refresh_interval=60.0)

    snapshot_task = asyncio.create_task(cache.get_snapshot())
    await asyncio.sleep(0.05)  # let get_snapshot start joining the scan

    # Out-of-band removal while the join is still in flight -- bypasses
    # this cache's own `_reconcile_namespace_set` entirely.
    resolver.unregister_namespace_resolver("ns")

    gate.set()
    snapshot = await snapshot_task

    assert "ns" not in cache._entries
    assert not any(r["name"] == "ns:old" for r in snapshot.rows)


# -- single-flight + generation-guarded concurrent refresh ---------------------


@pytest.mark.asyncio
async def test_concurrent_refreshes_of_same_namespace_single_flight():
    resolver, ns = _make_resolver_with_namespace()
    started = asyncio.Event()
    release = asyncio.Event()

    async def _slow_list():
        started.set()
        await release.wait()
        return [_agent("a1")]

    ns.outcomes = [_slow_list]
    cache = AgentRosterCache(resolver, refresh_interval=60.0)

    task_a = asyncio.create_task(cache.get_snapshot(force_refresh=True))
    await started.wait()
    task_b = asyncio.create_task(cache.get_snapshot(force_refresh=True))
    await asyncio.sleep(0.05)  # let task_b observe the in-flight scan
    release.set()
    snap_a, snap_b = await asyncio.gather(task_a, task_b)

    # Only ONE scan ran -- task_b joined the same in-flight task rather than
    # starting a duplicate.
    assert ns.call_count == 1
    assert snap_a.complete is True
    assert snap_b.complete is True


@pytest.mark.asyncio
async def test_slower_earlier_scan_never_overwrites_faster_later_one():
    """Generation-guarded publication: a scan that started earlier but
    finishes later than a subsequent scan of the same namespace must not
    clobber the newer result."""
    resolver, ns = _make_resolver_with_namespace()
    cache = AgentRosterCache(resolver, refresh_interval=60.0)

    release_first = asyncio.Event()

    async def _slow_first():
        await release_first.wait()
        return [_agent("stale")]

    ns.outcomes = [_slow_first]
    first_task = cache._refresh_namespace("ns")
    await asyncio.sleep(0.02)  # let the first scan actually start

    # Replace the registered resolver so the second refresh is a genuinely
    # newer generation against the same namespace -- single-flight would
    # otherwise just return the same in-flight `first_task`, deadlocking
    # the `await second_task` below until `release_first.set()` (never
    # called yet at that point).
    resolver.unregister_namespace_resolver("ns")
    new_ns = _ControllableResolver("ns")
    new_ns.outcomes = [[_agent("fresh")]]
    resolver.register_namespace_resolver(new_ns)
    second_task = cache._refresh_namespace("ns")
    await second_task

    # Now let the slow first scan finish -- its (stale) result must be
    # discarded, not published over the fresher one.
    release_first.set()
    await first_task

    snapshot = await cache.get_snapshot()
    names = {r["name"] for r in snapshot.rows}
    assert "ns:fresh" in names
    assert "ns:stale" not in names


# -- default response shape is unchanged without require_complete -------------


@pytest.mark.asyncio
async def test_default_response_shape_unchanged_even_when_badly_incomplete():
    resolver, ns = _make_resolver_with_namespace()
    ns.outcomes = [RuntimeError("boom")]
    cache = AgentRosterCache(resolver, refresh_interval=60.0)
    snapshot = await cache.get_snapshot()  # no require_complete semantics here
    # The shape itself: rows + incomplete_namespaces, same as Phase 2 --
    # `complete` exists but a non-require_complete caller (the route) never
    # surfaces it as anything other than a plain 200.
    assert snapshot.incomplete_namespaces == ["ns"]
    assert snapshot.complete is False
    assert snapshot.rows == []  # no static agents registered in this test


# -- dynamic namespace set: add / remove / replace -----------------------------


@pytest.mark.asyncio
async def test_namespace_removed_from_provider_is_retired_from_cache():
    resolver, ns = _make_resolver_with_namespace()
    ns.outcomes = [[_agent("a1")]]
    cache = AgentRosterCache(resolver, refresh_interval=60.0)
    await cache.get_snapshot()
    assert "ns" in cache._entries

    resolver.unregister_namespace_resolver("ns")
    cache._reconcile_namespace_set()
    assert "ns" not in cache._entries
    snapshot = await cache.get_snapshot()
    assert snapshot.incomplete_namespaces == []
    assert snapshot.rows == []


@pytest.mark.asyncio
async def test_namespace_removal_cancels_its_in_flight_scan():
    """A namespace removed (or replaced) while its scan is still in flight
    must have that scan actually cancelled, not merely untracked -- an
    untracked-but-still-running scan would survive `stop()` entirely,
    since shutdown only cancels/awaits tasks still reachable from
    `_inflight`."""
    resolver, ns = _make_resolver_with_namespace()
    started = asyncio.Event()
    release = asyncio.Event()

    async def _slow_list():
        started.set()
        await release.wait()
        return [_agent("a1")]  # pragma: no cover - cancelled before this

    ns.outcomes = [_slow_list]
    cache = AgentRosterCache(resolver, refresh_interval=60.0)
    task = cache._refresh_namespace("ns")
    await started.wait()

    resolver.unregister_namespace_resolver("ns")
    cache._reconcile_namespace_set()
    assert "ns" not in cache._inflight

    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()  # nothing left waiting on it, but don't leak the event


@pytest.mark.asyncio
async def test_namespace_replacement_invalidates_prior_cache_entry():
    """A same-namespace provider replacement must never keep serving the
    old provider's last-known-good agents as authoritative across the
    swap -- the new provider gets its own fresh UNINITIALIZED entry."""
    resolver, ns = _make_resolver_with_namespace()
    ns.outcomes = [[_agent("old-agent")]]
    cache = AgentRosterCache(resolver, refresh_interval=60.0)
    first = await cache.get_snapshot()
    assert any(r["name"] == "ns:old-agent" for r in first.rows)

    # Replace the registered resolver object for the same namespace prefix --
    # mirrors what `refresh_provider_resolvers()`'s transactional swap does.
    resolver.unregister_namespace_resolver("ns")
    new_ns = _ControllableResolver("ns")
    new_ns.outcomes = [[_agent("new-agent")]]
    resolver.register_namespace_resolver(new_ns)

    cache._reconcile_namespace_set()
    entry = cache._entries["ns"]
    assert entry.state.value == "uninitialized"
    assert entry.rows == []  # the old provider's last-known-good is gone

    second = await cache.get_snapshot()
    names = {r["name"] for r in second.rows}
    assert "ns:new-agent" in names
    assert "ns:old-agent" not in names


@pytest.mark.asyncio
async def test_out_of_band_provider_swap_mid_scan_discards_stale_rows():
    """A provider swap performed out-of-band -- e.g. another route calling
    ``refresh_provider_resolvers()`` directly, bypassing this cache's own
    ``_reconcile_namespace_set`` -- while a namespace scan is still awaiting
    the *old* resolver's ``list()`` must not let that now-superseded scan
    publish its rows. The live resolver identity is rechecked at publish
    time, not only the cache's own (possibly stale, not-yet-reconciled)
    ``entry.provider_ref``."""
    resolver, ns = _make_resolver_with_namespace()
    release = asyncio.Event()

    async def _slow_list():
        await release.wait()
        return [_agent("stale")]

    ns.outcomes = [_slow_list]
    cache = AgentRosterCache(resolver, refresh_interval=60.0)
    task = cache._refresh_namespace("ns")
    await asyncio.sleep(0.05)  # let the scan start and begin awaiting list()

    # Out-of-band swap: unregister/register directly on the resolver,
    # mirroring what a concurrent `refresh_provider_resolvers()` caller
    # would do -- deliberately *without* calling
    # `cache._reconcile_namespace_set()`, so the cache's own `entry.
    # provider_ref` has not yet been updated to reflect the swap.
    resolver.unregister_namespace_resolver("ns")
    new_ns = _ControllableResolver("ns")
    new_ns.outcomes = [[_agent("fresh")]]
    resolver.register_namespace_resolver(new_ns)

    release.set()
    await task

    # The stale scan's rows must never have been published, even though
    # the cache's own entry still pointed at the old provider_ref when the
    # scan finished.
    entry = cache._entries.get("ns")
    assert entry is not None
    assert entry.rows == []
    assert entry.state.value == "uninitialized"


# -- replacement-constructor-failure path (resolver-level) ---------------------


def test_provider_replacement_constructor_failure_keeps_old_resolver_authoritative(
    monkeypatch, tmp_path,
):
    """If the new resolver's construction fails, the previous resolver (and
    whatever cache entry it backs) must remain registered and authoritative
    -- never a brief false-success gap."""
    import json

    monkeypatch.setenv("AGENT_BRIDGE_PROVIDERS_DIR", str(tmp_path))

    def _write(name: str, payload: dict) -> None:
        (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")

    import sys

    _write("codespaces.json", {"namespace": "codespace", "command": [sys.executable, "one"]})
    resolver = AgentResolver({}, {})
    result = resolver.refresh_provider_resolvers(force=True)
    assert result.ok is True
    original = resolver.namespace_resolvers["codespace"]

    from agent_bridge import agent_registry_discovery as discovery_module

    class _BrokenCliNamespaceResolver(discovery_module.CliNamespaceResolver):
        def __init__(self, *a, **kw):  # noqa: D401 - test double
            raise ValueError("construction boom")

    monkeypatch.setattr(discovery_module, "CliNamespaceResolver", _BrokenCliNamespaceResolver)
    _write("codespaces.json", {"namespace": "codespace", "command": [sys.executable, "two"]})
    result = resolver.refresh_provider_resolvers(force=True)

    assert result.ok is False
    assert result.failed_namespaces == ["codespace"]
    # The ORIGINAL resolver is still registered -- never unregistered ahead
    # of a successful replacement construction.
    assert resolver.namespace_resolvers["codespace"] is original


def test_discovery_result_distinguishes_raised_partial_and_clean(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_BRIDGE_PROVIDERS_DIR", str(tmp_path))
    resolver = AgentResolver({}, {})

    # A clean pass with nothing to discover.
    clean = resolver.refresh_provider_resolvers(force=True)
    assert clean == DiscoveryResult(ok=True)

    # A scan-level exception.
    from agent_bridge import agent_registry_discovery as discovery_module

    def _raise(*a, **kw):
        raise RuntimeError("scan boom")

    monkeypatch.setattr(discovery_module, "scan_provider_registry", _raise)
    raised = resolver.refresh_provider_resolvers(force=True)
    assert raised.ok is False
    assert raised.raised is True


@pytest.mark.asyncio
async def test_discovery_completion_reconciles_namespaces_atomically():
    """``wait_until_warm()`` polls ``_discovery_ever_ok and all(... for e
    in self._entries.values())`` -- if discovery could flip
    ``_discovery_ever_ok`` to ``True`` *before* the namespace set is
    reconciled, a poll landing in that gap would see "discovery ok, zero
    known namespaces" (``all()`` over an empty collection is vacuously
    ``True``) and wrongly treat the cache as warm before any actual
    namespace has even been discovered. The two updates must happen
    together, in the same synchronous tail: the moment
    ``_discovery_ever_ok`` becomes ``True``, ``_entries`` must already
    reflect every currently-registered namespace."""
    resolver, ns = _make_resolver_with_namespace()
    cache = AgentRosterCache(resolver, refresh_interval=60.0)
    assert cache._discovery_ever_ok is False
    assert "ns" not in cache._entries

    await cache._maybe_refresh_discovery(force=True)

    assert cache._discovery_ever_ok is True
    assert "ns" in cache._entries


# -- 503 fail-closed contract (require_complete) -------------------------------


@pytest.mark.asyncio
async def test_snapshot_incomplete_while_discovery_persistently_fails():
    """If discovery itself can never complete cleanly (every scan attempt
    fails), `complete` must stay False -- discovery that never succeeds is
    exactly the "nothing authoritative to serve" case the 503 contract
    exists for, independent of whether any namespace even exists yet."""
    resolver = AgentResolver({}, {})
    resolver._scan_provider_report = lambda *, force=False: DiscoveryResult(
        ok=False, raised=True,
    )
    cache = AgentRosterCache(resolver, refresh_interval=60.0)
    snapshot = await cache.get_snapshot()
    assert snapshot.incomplete_namespaces == []
    assert snapshot.complete is False


@pytest.mark.asyncio
async def test_snapshot_complete_after_clean_discovery_and_namespace_scans():
    resolver, ns = _make_resolver_with_namespace()
    ns.outcomes = [[_agent("a1")]]
    cache = AgentRosterCache(resolver, refresh_interval=60.0)
    cache.start()
    try:
        await cache.wait_until_warm(timeout=5.0)
        snapshot = await cache.get_snapshot()
        assert snapshot.complete is True
    finally:
        await cache.stop()


@pytest.mark.asyncio
async def test_discovery_generation_stale_forces_incomplete_even_with_last_known_good():
    """A discovery generation that cannot refresh cleanly must force
    `complete=False` even while an already-known namespace still has a
    last-known-good value -- it is never conditioned solely on namespace-
    level staleness."""
    resolver, ns = _make_resolver_with_namespace()
    ns.outcomes = [[_agent("a1")]]
    cache = AgentRosterCache(resolver, refresh_interval=60.0, freshness_multiplier=1.0)
    first = await cache.get_snapshot()
    assert first.complete is True

    # Discovery itself starts persistently failing (e.g. a wedged/crashed
    # background scan) without touching the namespace entry at all -- it
    # should still have a fresh, OK last-known-good value.
    real_scan = resolver._scan_provider_report
    resolver._scan_provider_report = lambda *, force=False: DiscoveryResult(
        ok=False, raised=True,
    )
    cache._discovery_ok_at -= 10_000
    second = await cache.get_snapshot()
    assert second.incomplete_namespaces == []  # the namespace itself is fine
    assert second.complete is False  # but discovery itself can't refresh
    resolver._scan_provider_report = real_scan


@pytest.mark.asyncio
async def test_discovery_scan_does_not_block_the_event_loop():
    """Discovery can perform blocking subprocess work
    (`plugin_activation.resolve_active_plugins()`'s `subprocess.run`,
    reached via `_scan_provider_report()`). The cache must run that part
    off-thread (`_run_in_daemon_thread`, a fresh daemon thread per attempt)
    so a concurrent coroutine keeps making progress while a slow scan is in
    flight -- a synchronous in-loop call would freeze all HTTP handling for
    its duration."""
    resolver = AgentResolver({}, {})

    def _slow_scan(*, force: bool = False) -> DiscoveryResult:
        time.sleep(0.3)
        return DiscoveryResult(ok=True)

    resolver._scan_provider_report = _slow_scan
    cache = AgentRosterCache(resolver, refresh_interval=60.0)

    ticks = 0

    async def _tick_loop() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.01)
            ticks += 1

    ticker = asyncio.create_task(_tick_loop())
    try:
        await cache.get_snapshot(force_refresh=True)
    finally:
        ticker.cancel()
        try:
            await ticker
        except asyncio.CancelledError:
            pass

    # The event loop kept ticking throughout the ~0.3s blocking scan --
    # if the scan had run synchronously in-loop, essentially no ticks
    # could have happened during that window.
    assert ticks >= 10


@pytest.mark.asyncio
async def test_concurrent_discovery_triggers_single_flight_one_scan():
    """Two callers that both observe stale/forced discovery at the same
    time must join the same underlying scan rather than each racing a
    concurrent, independent mutation of the resolver's own namespace-
    resolver registry."""
    resolver = AgentResolver({}, {})
    call_count = 0

    def _counted_scan(*, force: bool = False) -> DiscoveryResult:
        nonlocal call_count
        call_count += 1
        time.sleep(0.1)
        return DiscoveryResult(ok=True)

    resolver._scan_provider_report = _counted_scan
    cache = AgentRosterCache(resolver, refresh_interval=60.0)

    await asyncio.gather(
        cache.get_snapshot(force_refresh=True),
        cache.get_snapshot(force_refresh=True),
    )
    assert call_count == 1


def test_direct_synchronous_caller_prevents_stale_rollback_from_cache_scan():
    """Production code outside the cache (the dispatch-task route,
    ``AgentResolver.resolve_async()``) calls ``refresh_provider_resolvers()``
    directly and synchronously -- if the cache's own off-thread scan is
    still running when that direct call applies a newer report, the
    cache's own later-finishing (but earlier-started) scan must not then
    roll that newer state back. The shared ``_apply_generation`` guard
    (not merely the cache's own per-attempt discovery generation) rejects
    it."""
    resolver = AgentResolver({}, {})

    # Capture a report the same way an in-flight `_do_scan_discovery` would
    # -- the generation it captures reflects the state *before* anything
    # else applies.
    outcome = resolver._scan_provider_report(force=True)
    assert not isinstance(outcome, DiscoveryResult)
    report, start_generation = outcome

    # A direct, synchronous caller elsewhere applies a newer report in the
    # meantime (bumping `_apply_generation`) while the cache's own scan
    # (captured above) is still "in flight".
    direct_result = resolver.refresh_provider_resolvers(force=True)
    assert direct_result.ok is True

    # The cache's own, now-stale scan finally tries to apply its earlier
    # report -- rejected, never rolling back the direct caller's newer
    # state.
    stale_result = resolver._apply_provider_report(report, start_generation)
    assert stale_result.ok is False


def test_scan_ttl_not_advanced_until_report_actually_applied():
    """`_scan_provider_report()`'s internal TTL throttle must never let a
    concurrent, non-forced caller treat an *in-progress* (not yet applied)
    scan as a completed, successful one -- that would let it read the
    still-stale pre-scan registry while believing it is current. The
    timestamp only advances once `_apply_provider_report()` actually
    reconciles the registry."""
    resolver = AgentResolver({}, {})
    assert resolver._provider_scan_ts == 0.0

    outcome = resolver._scan_provider_report(force=True)
    assert not isinstance(outcome, DiscoveryResult)
    report, start_generation = outcome

    # The scan has run (mirrors the cache's own off-thread half having
    # finished) but has not been applied yet -- the TTL timestamp must
    # still read "never scanned," not "just scanned."
    assert resolver._provider_scan_ts == 0.0

    applied = resolver._apply_provider_report(report, start_generation)
    assert applied.ok is True

    # Only once actually applied does the timestamp advance.
    assert resolver._provider_scan_ts > 0.0


def test_superseded_raised_scan_does_not_advance_the_scan_ttl(monkeypatch):
    """An abandoned (superseded) generation's own scan raising an
    exception *after* a newer generation has already applied must never
    advance `_provider_scan_ts` -- otherwise a concurrent, non-forced
    caller would see a fresh-looking timestamp and skip its own scan
    based on a failure from a generation that no longer matters, delaying
    a real provider addition/removal."""
    resolver = AgentResolver({}, {})
    from agent_bridge import agent_registry_discovery as discovery_module
    from agent_bridge.provider_sources import scan_provider_registry as real_scan

    call_count = 0
    ts_after_newer_apply = None

    def _scan(*a, **kw):
        nonlocal call_count, ts_after_newer_apply
        call_count += 1
        if call_count == 1:
            # Simulate a newer, separate generation applying successfully
            # while this (outer, now-stale) scan is still "in flight."
            newer_outcome = resolver._scan_provider_report(force=True)
            report, start_generation = newer_outcome
            applied = resolver._apply_provider_report(report, start_generation)
            assert applied.ok is True
            ts_after_newer_apply = resolver._provider_scan_ts
            raise RuntimeError("scan boom")
        return real_scan(*a, **kw)

    monkeypatch.setattr(discovery_module, "scan_provider_registry", _scan)

    result = resolver._scan_provider_report(force=True)
    assert isinstance(result, DiscoveryResult)
    assert result.ok is False
    assert result.raised is True

    # The superseded (outer) attempt's own raise must not have re-stamped
    # the timestamp -- it must still read exactly what the newer,
    # already-applied generation set it to.
    assert resolver._provider_scan_ts == ts_after_newer_apply


@pytest.mark.asyncio
async def test_hung_discovery_scan_is_abandoned_and_its_stale_result_discarded():
    """A discovery scan that runs past the watchdog timeout must not be
    awaited forever -- an `asyncio.to_thread`-style worker thread cannot
    actually be cancelled once started. Even a caller that joined the
    stuck attempt *before* it crossed the watchdog threshold must not be
    stranded on it: its own wait is itself bounded, and on timeout it
    rejoins whichever generation is current -- replacing the stuck one
    itself if nobody else has yet. The abandoned attempt's own eventual
    (even `ok`) result must never overwrite the newer attempt's published
    state."""
    resolver = AgentResolver({}, {})
    release = threading.Event()
    call_count = 0

    def _scan(*, force: bool = False) -> DiscoveryResult:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Blocks until the test explicitly releases it -- simulating a
            # genuinely hung repository check.
            release.wait(timeout=5.0)
        return DiscoveryResult(ok=True)

    resolver._scan_provider_report = _scan
    cache = AgentRosterCache(resolver, refresh_interval=60.0, watchdog_timeout=0.3)

    # This caller joins the very first (soon-to-be-hung) attempt -- its own
    # bounded wait (get_snapshot()'s discovery-join bound) must still let it
    # recover rather than hang forever, but that bound is deliberately
    # finite and may give up before the replacement (gen2)
    # attempt has actually finished. Poll with plain (non-forced) calls
    # after the initial kick -- force_refresh=True would keep re-triggering
    # brand-new scans once gen2 is already done, rather than just rejoining
    # or recognizing it as fresh.
    await asyncio.wait_for(cache.get_snapshot(force_refresh=True), timeout=2.0)
    for _ in range(100):
        if cache._discovery_generation >= 2:
            break
        await asyncio.wait_for(cache.get_snapshot(), timeout=2.0)
        await asyncio.sleep(0.05)
    else:
        pytest.fail("discovery never recovered from the hung first attempt")
    assert cache._discovery_generation == 2
    assert call_count == 2  # the hung first attempt, plus its one replacement

    # A second caller, arriving after recovery, simply joins the already-
    # fresh, current generation -- no further scan needed.
    second = await asyncio.wait_for(cache.get_snapshot(), timeout=5.0)
    assert second is not None
    assert cache._discovery_generation == 2
    assert call_count == 2

    release.set()  # unblock the orphaned first attempt's worker thread


@pytest.mark.asyncio
async def test_get_snapshot_bounds_the_discovery_join_when_persistently_hung():
    """A discovery scan that is persistently hung -- *every* attempt
    blocks past the watchdog timeout and gets abandoned-and-replaced,
    forever, not just the first -- must not keep `get_snapshot()` waiting
    indefinitely either. Bounded at `_watchdog_timeout` (`max_wait`), this
    request gives up and returns (discovery reported incomplete) rather
    than looping on retries forever; background supervision (the periodic
    loop's own unbounded call) is what keeps actually retrying."""
    resolver = AgentResolver({}, {})
    release = threading.Event()

    def _scan(*, force: bool = False) -> DiscoveryResult:
        release.wait(timeout=10.0)  # every attempt hangs until released
        return DiscoveryResult(ok=True)

    resolver._scan_provider_report = _scan
    cache = AgentRosterCache(resolver, refresh_interval=60.0, watchdog_timeout=0.1)

    start = time.monotonic()
    snapshot = await asyncio.wait_for(cache.get_snapshot(force_refresh=True), timeout=3.0)
    elapsed = time.monotonic() - start

    assert elapsed < 1.0  # bounded well under the outer 3.0s test timeout
    assert snapshot.complete is False
    assert cache._discovery_ever_ok is False

    release.set()  # unblock the orphaned worker thread(s)


@pytest.mark.asyncio
async def test_abandoned_scans_report_is_never_applied_to_the_registry():
    """A stale report from an abandoned (watchdog-timed-out) scan attempt
    must never be applied to the live namespace-resolver registry, even
    though `_scan_provider_report()` itself eventually returns a normal
    (non-`DiscoveryResult`) report rather than an early-exit result -- the
    generation guard must gate *application* itself, not just this cache's
    own freshness bookkeeping. Applying a stale report could resurrect a
    provider a newer generation already removed, or roll back a
    replacement it already made."""
    resolver = AgentResolver({}, {})
    release = threading.Event()
    call_count = 0
    applied_reports = []
    sentinel_report = object()

    def _scan(*, force: bool = False):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            release.wait(timeout=5.0)
        return (sentinel_report, 0)

    def _apply(report, start_generation):
        applied_reports.append(report)
        return DiscoveryResult(ok=True)

    resolver._scan_provider_report = _scan
    resolver._apply_provider_report = _apply
    cache = AgentRosterCache(resolver, refresh_interval=60.0, watchdog_timeout=0.3)

    # This caller joins the very first (soon-to-be-hung) attempt -- its own
    # bounded wait (get_snapshot()'s discovery-join bound) may give up
    # before the replacement (gen2) attempt has actually finished, since
    # that bound is deliberately finite. Poll with plain (non-forced) calls
    # after the initial kick -- force_refresh=True would keep re-triggering
    # brand-new scans once gen2 is already applied, inflating
    # applied_reports past 1 rather than just recognizing it as fresh.
    await asyncio.wait_for(cache.get_snapshot(force_refresh=True), timeout=2.0)
    for _ in range(100):
        if applied_reports:
            break
        await asyncio.wait_for(cache.get_snapshot(), timeout=2.0)
        await asyncio.sleep(0.05)
    else:
        pytest.fail("the replacement (gen2) discovery attempt never applied")
    assert len(applied_reports) == 1  # only the fresh, gen2 attempt applied

    release.set()  # unblock the orphaned gen1 attempt's worker thread
    await asyncio.sleep(0.05)  # let its callback run, if it's going to
    # The first, abandoned (gen1) attempt's report must never be applied,
    # even after it finally returns -- the generation guard discards it
    # before it ever reaches _apply_provider_report.
    assert len(applied_reports) == 1


@pytest.mark.asyncio
async def test_stop_does_not_hang_on_a_stuck_discovery_scan():
    """`stop()` must never be held hostage by a discovery scan whose
    worker thread cannot be forcibly killed -- it bounds the wait and
    proceeds, leaving the orphaned thread to finish (or not) on its own."""
    resolver = AgentResolver({}, {})
    release = threading.Event()

    def _scan(*, force: bool = False) -> DiscoveryResult:
        release.wait(timeout=5.0)
        return DiscoveryResult(ok=True)

    resolver._scan_provider_report = _scan
    cache = AgentRosterCache(resolver, refresh_interval=60.0)
    cache._DISCOVERY_SHUTDOWN_GRACE = 0.2

    task = asyncio.create_task(cache._maybe_refresh_discovery(force=True))
    await asyncio.sleep(0.05)  # let the scan actually start on its thread

    started = time.monotonic()
    await asyncio.wait_for(cache.stop(), timeout=2.0)
    assert time.monotonic() - started < 1.0

    release.set()
    try:
        await asyncio.wait_for(task, timeout=2.0)
    except asyncio.CancelledError:
        # stop() cancelling the shared discovery task also cancels anyone
        # still shielded-awaiting it -- expected during shutdown, same as
        # the existing in-flight-namespace-scan cancellation on stop().
        pass


@pytest.mark.asyncio
async def test_watchdog_recovers_even_when_namespace_scan_resists_cancellation():
    """A namespace resolver whose `list()` swallows `CancelledError` and
    keeps running must never be able to hang the watchdog itself --
    `_cancel_and_wait_bounded` bounds the wait, and the watchdog abandons
    the stuck cycle (dropping its in-flight tracking so the replacement
    loop doesn't rejoin it) rather than waiting on it forever."""
    resolver, ns = _make_resolver_with_namespace()
    started = asyncio.Event()
    release = asyncio.Event()

    async def _resists_cancellation():
        started.set()
        while True:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                if release.is_set():
                    raise
                continue  # swallow and keep running -- cancellation-resistant

    ns.outcomes = [_resists_cancellation]
    cache = AgentRosterCache(resolver, refresh_interval=0.05, watchdog_timeout=0.1)
    cache._CANCELLATION_GRACE = 0.1
    cache.start()
    try:
        # Synchronize with the scan actually having entered the resistant
        # coroutine before swapping outcomes out from under it -- without
        # this, `ns.outcomes = []` could run before the scan task has even
        # had its first chance to execute (cache.start() only schedules
        # the task; nothing yields to it until the next `await`), silently
        # skipping the hang scenario this test exists to exercise.
        await asyncio.wait_for(started.wait(), timeout=2.0)
        ns.outcomes = []  # the restarted cycle's scan -- empty list, succeeds
        for _ in range(100):
            await asyncio.sleep(0.05)
            entry = cache._entries.get("ns")
            if entry is not None and entry.state.value == "ok":
                break
        else:
            pytest.fail(
                "watchdog never recovered despite a cancellation-resistant scan"
            )
    finally:
        release.set()
        await cache.stop()


@pytest.mark.asyncio
async def test_cancel_and_wait_bounded_does_not_swallow_the_callers_own_cancellation():
    """`_cancel_and_wait_bounded`'s own `except CancelledError` branch must
    only treat that as the *child* task's own cancellation -- if the
    CALLING coroutine itself is cancelled while awaiting inside this
    helper (e.g. `stop()` cancelling the watchdog's own task while it's in
    here force-restarting a hung loop), the child task is still running
    (not done), and that cancellation must propagate rather than be
    silently swallowed and reported back as a successful cancellation."""
    resolver, ns = _make_resolver_with_namespace()
    cache = AgentRosterCache(resolver, refresh_interval=60.0)
    release = asyncio.Event()

    async def _resists_cancellation_forever():
        while True:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                if release.is_set():
                    raise
                continue  # swallow and keep running -- cancellation-resistant

    child = asyncio.create_task(_resists_cancellation_forever())
    await asyncio.sleep(0)  # let child actually start

    async def _call_helper():
        return await cache._cancel_and_wait_bounded(child, timeout=5.0, label="test")

    outer = asyncio.create_task(_call_helper())
    await asyncio.sleep(0)  # let outer enter the helper and start its own wait

    outer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await outer

    # The child itself was never actually finished -- only the outer
    # caller's own cancellation was in play here.
    assert not child.done()

    release.set()
    child.cancel()
    try:
        await child
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_abandoned_loops_finally_does_not_clear_replacement_loops_timestamp():
    """Mirrors the watchdog's own force-restart sequence: an old loop task
    is replaced by a fresh one (`self._task` now points at the
    replacement, which has set its own `_cycle_started_at`), but the OLD
    task is cancellation-resistant and only reaches its own `finally`
    later. That `finally` must not clear `_cycle_started_at` out from
    under the replacement -- doing so would blind the watchdog's own hang
    detection to a subsequent hang in the replacement itself."""
    resolver, ns = _make_resolver_with_namespace()
    cache = AgentRosterCache(resolver, refresh_interval=60.0)
    release = asyncio.Event()

    async def _stuck_refresh_cycle():
        await release.wait()

    cache._refresh_cycle = _stuck_refresh_cycle
    old_task = asyncio.create_task(cache._loop(), name="old-loop")
    cache._task = old_task
    await asyncio.sleep(0)  # let old_task start, stamp _cycle_started_at, enter the stub

    assert cache._cycle_started_at is not None

    # Simulate the watchdog installing a replacement loop after abandoning
    # this one -- the replacement's own fresh `_cycle_started_at`.
    cache._task = object()  # stand-in for a brand-new loop task
    replacement_ts = time.monotonic() + 1000.0  # distinguishable sentinel
    cache._cycle_started_at = replacement_ts

    # Let the abandoned old task's own stub finally complete (as if it
    # eventually finished despite being abandoned) -- its own `finally`
    # must not clear the replacement's timestamp.
    release.set()
    for _ in range(50):
        await asyncio.sleep(0.01)
        if cache._cycle_started_at != replacement_ts:
            break

    assert cache._cycle_started_at == replacement_ts

    old_task.cancel()
    try:
        await old_task
    except asyncio.CancelledError:
        pass


# -- watchdog recovery from a hung (never-completing) refresh cycle -----------


@pytest.mark.asyncio
async def test_watchdog_force_restarts_a_hung_refresh_cycle():
    """A namespace scan that never completes (no timeout configured) must
    still eventually be recovered -- the crash-only supervisor
    (`_on_loop_done`) can't catch this since the loop task never actually
    finishes; the independent watchdog must force-restart it."""
    resolver, ns = _make_resolver_with_namespace()
    started = asyncio.Event()
    hang_forever = asyncio.Event()

    async def _hang():
        started.set()
        await hang_forever.wait()
        return [_agent("never")]  # pragma: no cover - never reached

    ns.outcomes = [_hang]
    cache = AgentRosterCache(
        resolver, refresh_interval=0.05, watchdog_timeout=0.2,
    )
    cache.start()
    try:
        # Synchronize with the scan actually having entered `_hang()`
        # before swapping outcomes out from under it -- see the identical
        # note in test_watchdog_recovers_even_when_namespace_scan_resists_
        # cancellation.
        await asyncio.wait_for(started.wait(), timeout=2.0)
        # Give the watchdog time to notice the hang and force a restart --
        # once it does, the loop's NEXT cycle gets a fresh (non-hanging)
        # outcome and the namespace eventually resolves to OK.
        ns.outcomes = []  # the restarted cycle's scan -- empty list, succeeds
        for _ in range(100):
            await asyncio.sleep(0.05)
            entry = cache._entries.get("ns")
            if entry is not None and entry.state.value == "ok":
                break
        else:
            pytest.fail("watchdog never recovered the hung refresh cycle")
    finally:
        hang_forever.set()  # unblock the original hung scan so stop() can clean up
        await cache.stop()


@pytest.mark.asyncio
async def test_cancelled_join_does_not_publish_stale_entry_as_complete():
    """If a request's opportunistic join shares an in-flight task with the
    periodic loop, and the watchdog cancels that shared task out from under
    it (a hung cycle), the cancelled scan never updates `entry.state` at
    all -- the response must recheck freshness directly rather than
    trusting a stale `state == OK` left over from an earlier, now-expired
    successful scan."""
    resolver, ns = _make_resolver_with_namespace()
    ns.outcomes = [[_agent("a1")]]
    cache = AgentRosterCache(resolver, refresh_interval=60.0, freshness_multiplier=1.0)
    first = await cache.get_snapshot()
    assert first.complete is True

    # Force the entry stale, then simulate a join whose scan never
    # completes/publishes anything (as a watchdog-cancelled task would
    # leave it) -- `entry.state` stays OK throughout, exactly the shape a
    # cancellation produces.
    entry = cache._entries["ns"]
    entry.last_refreshed_monotonic -= 10_000
    assert entry.state.value == "ok"

    async def _cancelled_join(ns_name):
        return None  # resolves without ever touching entry.state

    original_refresh = cache._refresh_namespace
    cache._refresh_namespace = lambda ns_name: asyncio.ensure_future(_cancelled_join(ns_name))
    try:
        snapshot = await cache.get_snapshot()
    finally:
        cache._refresh_namespace = original_refresh

    assert snapshot.incomplete_namespaces == ["ns"]
    assert not any(r.get("name", "").startswith("ns:") for r in snapshot.rows)


# -- a triggered rescan bypasses the resolver's own inner result cache -------


@pytest.mark.asyncio
async def test_rescan_invalidates_resolver_inner_cache():
    """Any scan this cache triggers (periodic, opportunistic, or via
    `force_refresh`'s forced discovery pass) must bypass a namespace
    resolver's own separate, shorter result cache (e.g.
    `CliNamespaceResolver`'s internal TTL) -- this cache owns the
    advertised refresh cadence, so a rescan must not silently republish
    stale cached rows under a fresh cache timestamp."""
    resolver, ns = _make_resolver_with_namespace()
    ns.outcomes = [[_agent("a1")]]
    cache = AgentRosterCache(resolver, refresh_interval=60.0)
    await cache.get_snapshot()
    assert ns.invalidate_count == 1

    # A namespace that's already fresh is not force-refreshed just because
    # `force_refresh=True` was passed -- only once it's genuinely stale
    # does the opportunistic join (which `force_refresh` never bypasses the
    # scope of) trigger another real scan.
    cache._entries["ns"].last_refreshed_monotonic -= 10_000
    ns.outcomes = [[_agent("a1")]]
    await cache.get_snapshot(force_refresh=True)
    assert ns.invalidate_count == 2


@pytest.mark.asyncio
async def test_force_refresh_does_not_rescan_an_already_fresh_namespace():
    """`force_refresh=true` must not rescan every healthy, fresh namespace --
    only entries that are uninitialized, failed, or stale ever join a
    rescan, matching the plain opportunistic-join path. One broken
    provider must never make every other, healthy namespace's provider
    re-run its own scan on each bounded retry."""
    resolver, ns = _make_resolver_with_namespace()
    ns.outcomes = [[_agent("a1")]]
    cache = AgentRosterCache(resolver, refresh_interval=60.0)
    await cache.get_snapshot()
    assert ns.call_count == 1

    await cache.get_snapshot(force_refresh=True)
    assert ns.call_count == 1  # still fresh -- not rescanned


"""Background-refreshed, supervised cache for ``AgentResolver``'s namespaced
agent scan (pivot-streaming-transport Phase 3b).

``AgentResolver.list_agents_async()`` re-invokes every registered namespace
resolver's ``list()`` on every call (`agent_registry_resolver.py`). Each
resolver has its own short internal result TTL (`CliNamespaceResolver`'s
``_list_cache``), but that only de-dupes a *single* caller's own repeat
calls within the TTL window -- N concurrent callers (N Pickers each running
their own ``--subscribe`` poll tick) arriving before any one of them has
populated that cache still each trigger their own concurrent scan. Moving
the scan into the daemon as a background-refreshed cache means ``GET
/api/v1/agents`` becomes an O(1) read for a healthy, fresh hit, shared by
every caller.

This module deliberately does **not** cache the non-namespaced
(``AgentResolver.list_agents()``) rows -- that listing is a cheap in-memory
dict walk, not a scan, so caching it would only add a staleness class with
no benefit.

See ``efforts/active/pivot-streaming-transport/phase-3-design.md``'s 3b
section for the full design rationale (last-known-good retention,
uninitialized vs. failed vs. fresh per-namespace state, single-flight plus
generation-guarded publication, the ``force_refresh``/``require_complete``
protocol-gated request parameters, and the dynamic namespace set tracking
``refresh_provider_resolvers()``'s own add/remove/replace).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .agent_registry_resolver import AgentResolver
from .agent_roster_discovery import _DiscoveryMixin

log = logging.getLogger("agent-bridge")

#: Multiplier applied to the cache's own refresh interval to derive a
#: namespace entry's freshness deadline (3b design: "a bound meaningfully
#: larger than the normal refresh period, e.g. 3x the refresh interval").
#: Past this deadline a successful-looking entry is treated as stale -- it is
#: never silently served as current without an opportunistic rescan attempt
#: first (see :meth:`AgentRosterCache.get_snapshot`).
DEFAULT_FRESHNESS_MULTIPLIER = 3.0

#: Default seconds between background refresh cycles. Mirrors the per-
#: namespace-resolver result TTL (`agent_registry_common._NAMESPACE_LIST_
#: DEFAULT_TTL`, 12s) this cache supersedes as the authoritative scan cadence.
DEFAULT_REFRESH_INTERVAL = 12.0

#: Upper bound on how long a single `get_snapshot()` call will wait for an
#: incomplete/stale namespace's opportunistic single-flight refresh before
#: falling back to whatever last-known-good (or empty, if uninitialized)
#: data is already cached. Deliberately much shorter than
#: `_watchdog_timeout` (which bounds *background* supervision -- a hung
#: periodic cycle, a stuck discovery scan -- not a live request): a
#: namespace provider that is reliably slower than this cache's own
#: refresh cadence (e.g. a CLI-subprocess-backed provider enumerating a
#: remote service or local container runtime, with no result cache of its
#: own) can never publish a fresh entry within one cycle -- without a
#: short, independent bound here, EVERY caller of such a namespace pays
#: that provider's full scan cost on every single request, the opposite
#: of what a background-refreshed cache is for. The join still kicks off
#: (or joins) the namespace's real single-flight scan regardless -- it
#: keeps running in the background past this timeout -- so a later
#: request (or the periodic refresh loop) still picks up its result once
#: it actually finishes; this bound only governs how long any *one*
#: caller waits for it inline.
DEFAULT_OPPORTUNISTIC_JOIN_BUDGET = 1.5


class _NamespaceState(str, Enum):
    #: Never completed a scan (new namespace, or one whose provider was just
    #: replaced -- distinct from a namespace that scanned successfully at
    #: least once and is merely currently failing/stale, per the 3b design's
    #: "a namespace that has never completed its first scan is not the same
    #: as one with a last-known-good value").
    UNINITIALIZED = "uninitialized"
    #: Most recent scan succeeded; ``rows``/``last_refreshed_monotonic`` are
    #: that scan's last-known-good result.
    OK = "ok"
    #: Most recent scan raised or timed out. ``rows`` (if any) retain
    #: whatever last-known-good value a prior OK scan published -- a FAILED
    #: state never substitutes stale rows into the *default* response (that
    #: stays today's Phase 2 skip-this-namespace shape); last-known-good is
    #: retained purely as internal cache state.
    FAILED = "failed"


@dataclass
class _NamespaceEntry:
    #: A strong reference to the namespace resolver object this entry's
    #: rows were scanned from (``is``-compared, never ``id()``-compared --
    #: once a resolver is unregistered, nothing else may keep it alive, and
    #: a bare address can be reused by its own replacement object).
    provider_ref: object | None
    state: _NamespaceState = _NamespaceState.UNINITIALIZED
    rows: list[dict[str, Any]] = field(default_factory=list)
    last_refreshed_monotonic: float | None = None
    #: Bumped once per scan *attempt* (not per success) -- the generation a
    #: completing scan must still match for its result to be published, so a
    #: slower, earlier attempt can never overwrite a faster, later one's
    #: fresher result.
    generation: int = 0

    def is_fresh(self, *, freshness_deadline: float, now: float) -> bool:
        if self.state != _NamespaceState.OK or self.last_refreshed_monotonic is None:
            return False
        return (now - self.last_refreshed_monotonic) <= freshness_deadline


@dataclass(frozen=True)
class AgentRosterSnapshot:
    """What ``GET /api/v1/agents`` serves for one request."""

    rows: list[dict[str, Any]]
    #: Namespace prefixes with no current authoritative value -- the same
    #: field shape/meaning Phase 2 already defined (unchanged for every
    #: caller, opted into the fail-closed contract or not).
    incomplete_namespaces: list[str]
    #: True only when every known namespace has a current (fresh) OK value
    #: *and* namespace discovery itself has completed cleanly within its own
    #: freshness deadline. A ``require_complete`` caller escalates a
    #: ``False`` here to a ``503`` instead of a normal ``200`` body; every
    #: other caller ignores this field entirely (its own response shape is
    #: governed solely by ``incomplete_namespaces``, exactly as before).
    complete: bool


class AgentRosterCache(_DiscoveryMixin):
    """Supervised, background-refreshed cache fronting one ``AgentResolver``.

    Owns a periodic refresh loop (restarted if it ever crashes -- "a
    process-level watchdog restarts it if it exits or stops making
    progress"), per-namespace single-flight scans with generation-guarded
    publication, and the uninitialized/OK/FAILED per-namespace state the 3b
    design requires so a stalled or crashed refresh never serves a
    stale-but-apparently-complete roster forever.
    """

    #: Bound on how long :meth:`stop` waits for a still-running discovery
    #: scan's worker thread to finish on its own -- that thread cannot be
    #: forcibly killed, so this must be short enough that shutdown is never
    #: effectively held hostage by a hung scan.
    _DISCOVERY_SHUTDOWN_GRACE = 2.0

    #: Bound on how long any single cancelled coroutine-based task (the
    #: periodic loop, a per-namespace scan) is given to actually honor its
    #: own cancellation before being abandoned -- see
    #: :meth:`_cancel_and_wait_bounded`. A resolver whose own ``list()``
    #: implementation is cancellation-resistant (swallows
    #: ``asyncio.CancelledError`` and keeps running) must never be able to
    #: hang the watchdog, daemon shutdown, or the warm-up timeout's own
    #: force-fail escape hatch indefinitely.
    _CANCELLATION_GRACE = 2.0

    def __init__(
        self,
        resolver: AgentResolver,
        *,
        refresh_interval: float = DEFAULT_REFRESH_INTERVAL,
        freshness_multiplier: float = DEFAULT_FRESHNESS_MULTIPLIER,
        watchdog_timeout: float | None = None,
        opportunistic_join_budget: float | None = None,
    ) -> None:
        self._resolver = resolver
        self._refresh_interval = max(1.0, refresh_interval)
        self._freshness_deadline = self._refresh_interval * freshness_multiplier
        self._entries: dict[str, _NamespaceEntry] = {}
        self._inflight: dict[str, asyncio.Task[None]] = {}
        self._inflight_token: dict[str, object | None] = {}
        self._task: asyncio.Task[None] | None = None
        self._watchdog_task: asyncio.Task[None] | None = None
        #: Single-flight task for the in-flight discovery scan (run via
        #: :func:`_run_in_daemon_thread` so a blocking repository check
        #: inside ``_scan_provider_report()`` never stalls the event loop).
        #: Publication is generation-guarded (see ``_discovery_generation``):
        #: once a scan is deemed hung, its result -- even a late,
        #: eventually-successful one -- must never overwrite a newer
        #: attempt's own state.
        self._discovery_task: asyncio.Task[None] | None = None
        self._discovery_generation = 0
        #: Monotonic timestamp of the current ``_discovery_task``'s own
        #: start, or ``None`` while no discovery scan is in flight -- the
        #: hung-scan detector's progress signal (mirrors ``_cycle_started_at``
        #: for the periodic cycle, but tracked independently: an
        #: ``_run_in_daemon_thread``-backed task cannot actually be
        #: cancelled once its worker thread has started, so this is the
        #: *only* way discovery can recover from a hang -- awaiting it
        #: forever is not an option).
        self._discovery_started_at: float | None = None
        self._stopping = False
        #: Monotonic timestamp of the currently in-flight refresh cycle's
        #: own start, or ``None`` while no cycle is running. Unlike the
        #: crash-recovery supervisor (:meth:`_on_loop_done`, which only
        #: fires once the loop *task* actually completes), a cycle that
        #: hangs forever (e.g. a namespace resolver with its timeout
        #: disabled via ``AGENT_BRIDGE_NAMESPACE_LIST_RESOLVER_TIMEOUT=0``)
        #: never completes at all -- this timestamp staying stale is the
        #: independent watchdog's (:meth:`_watchdog`) own progress signal.
        self._cycle_started_at: float | None = None
        self._watchdog_timeout = (
            watchdog_timeout if watchdog_timeout is not None
            else self._default_watchdog_timeout()
        )
        #: Never longer than `_watchdog_timeout` itself (an explicitly
        #: short watchdog -- as several tests configure -- must still
        #: bound the per-request join at least that tightly), and never
        #: longer than `DEFAULT_OPPORTUNISTIC_JOIN_BUDGET` regardless of
        #: how generous `_watchdog_timeout` is configured (production's
        #: default watchdog is minutes-scale background-supervision
        #: headroom, not a live-request budget). This clamp applies
        #: equally to an explicit `opportunistic_join_budget` override --
        #: a caller-requested budget larger than `_watchdog_timeout` would
        #: otherwise let a request outlive the very watchdog bound this
        #: cache's own orphaned-task story depends on.
        self._opportunistic_join_budget = min(
            self._watchdog_timeout,
            (
                opportunistic_join_budget if opportunistic_join_budget is not None
                else DEFAULT_OPPORTUNISTIC_JOIN_BUDGET
            ),
        )
        #: Monotonic timestamp of the most recent *clean* discovery pass
        #: (``refresh_provider_resolvers()`` returning ``ok=True``) -- a
        #: raised scan or a partial per-manifest construction failure does
        #: not advance this, so a stale discovery generation is its own
        #: independent `503` trigger (for a ``require_complete`` caller),
        #: regardless of whether already-known namespaces still have their
        #: own last-known-good values.
        self._discovery_ok_at: float | None = None
        self._discovery_ever_ok = False

    def _default_watchdog_timeout(self) -> float:
        """Derive the default watchdog bound from the refresh cadence
        (``refresh_interval * 5``), floored at the configured per-resolver
        ``list()`` timeout (``AGENT_BRIDGE_NAMESPACE_LIST_RESOLVER_
        TIMEOUT``, default 8s) when that timeout is enabled (not disabled
        via ``0``/``off``).

        A short ``refresh_interval`` (e.g. ``agent_roster_cache_interval:
        1`` -> a 5s derived bound) can otherwise undercut a namespace
        scan's own *legitimate* allowed duration -- a healthy, merely slow
        provider finishing within its own configured timeout would then
        get cancelled by the watchdog on every single cycle before it ever
        completes, and eventually force-failed at the warm-up bound too.
        Changing the rescan interval must never also silently shorten how
        long an individual scan is allowed to take."""
        from . import agent_registry as compat

        cadence_based = self._refresh_interval * 5
        resolver_timeout = compat._namespace_list_resolver_timeout()
        if resolver_timeout <= 0:
            return cadence_based
        # A margin above the resolver's own timeout, not merely equal to
        # it -- a scan finishing right at its own timeout boundary must
        # not race the watchdog waking up at nearly the same instant.
        return max(cadence_based, resolver_timeout + 2.0)

    @property
    def resolver(self) -> AgentResolver:
        """The resolver this cache is bound to -- a route can use this to
        detect (and safely ignore) a stale cache left over from a resolver
        hot-swap it didn't itself perform (e.g. a test replacing
        ``app.state.resolver`` directly without also clearing/recreating
        the cache)."""
        return self._resolver

    @property
    def discovery_ever_ok(self) -> bool:
        """Whether discovery has completed at least one clean pass, ever.

        ``wait_until_warm()`` can time out even with zero namespaces known
        yet -- if discovery itself has never completed a clean pass (e.g.
        a hung initial scan that outlives the warm-up bound), ``_entries``
        can be empty, vacuously satisfying ``wait_until_warm``'s own
        ``all(...)`` check over nothing. A caller gating daemon readiness
        must check this too, not just the warm-up result: proceeding
        without it would publish readiness for a roster that was never
        actually scanned at all, not merely one with some namespaces
        force-failed."""
        return self._discovery_ever_ok

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Start the background refresh loop (idempotent)."""
        if self._task is not None:
            return
        self._stopping = False
        self._spawn_loop()
        self._watchdog_task = asyncio.create_task(
            self._watchdog(), name="agent-roster-cache-watchdog",
        )

    def _spawn_loop(self) -> None:
        task = asyncio.create_task(self._loop(), name="agent-roster-cache-refresh")
        task.add_done_callback(self._on_loop_done)
        self._task = task

    def _on_loop_done(self, task: asyncio.Task[None]) -> None:
        """Supervisor: restart the periodic loop if it ever exits other than
        via an intentional :meth:`stop` cancellation -- "a process-level
        watchdog restarts it if it exits or stops making progress" (3b
        design). A bug that would otherwise silently freeze the cache
        forever instead gets a fresh loop on the very next tick. This only
        catches a loop that actually *exits* (crashes); a loop that hangs
        without ever completing its current cycle is :meth:`_watchdog`'s
        job instead."""
        if task.cancelled() or self._stopping:
            return
        exc = task.exception()
        if exc is not None:
            log.error(
                "Agent roster cache background refresh loop crashed; "
                "restarting",
                exc_info=exc,
            )
            self._spawn_loop()

    async def _cancel_and_wait_bounded(
        self, task: "asyncio.Task[Any]", *, timeout: float, label: str,
    ) -> bool:
        """Cancel ``task`` and wait up to ``timeout`` for it to actually
        finish. Returns ``True`` if it finished (cancelled or otherwise),
        ``False`` if it is being abandoned (still running) once the bound
        elapsed.

        A plain ``task.cancel()`` followed by an unbounded ``await task``
        assumes the task's own code promptly re-raises
        ``asyncio.CancelledError`` wherever it's currently suspended -- a
        namespace resolver whose ``list()`` implementation is
        cancellation-resistant (catches and swallows ``CancelledError``,
        or never actually awaits anything cancellable) can keep running
        indefinitely regardless, which would otherwise hang the watchdog,
        daemon shutdown, or the warm-up timeout's own force-fail escape
        hatch. A caller that gets ``False`` back MUST ensure nothing else
        keeps rejoining the abandoned task (e.g. drop it from whatever
        single-flight tracking led here) -- its eventual, late result (if
        any) is discarded via the normal generation guard regardless, the
        same as an abandoned discovery scan's.

        The ``CancelledError`` branch below only treats it as the *child*
        ``task`` finishing via cancellation if ``task`` is actually done
        by then -- ``asyncio.wait_for(asyncio.shield(task), ...)`` also
        raises ``CancelledError`` here if *this calling coroutine itself*
        is externally cancelled (e.g. ``stop()`` cancelling the watchdog's
        own task while it's inside this helper force-restarting a hung
        loop) -- shielding only protects ``task`` from that, not this
        awaiting frame. Swallowing that case the same way would silently
        consume the caller's own cancellation, letting it keep running one
        more iteration while whoever cancelled it (``stop()``) hangs
        forever awaiting a task that never actually completes."""
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
            return True
        except asyncio.CancelledError:
            if task.done():
                return True
            raise
        except (TimeoutError, asyncio.TimeoutError):
            log.warning(
                "Agent roster cache: %s did not honor cancellation within "
                "%.1fs -- abandoning it (cancellation-resistant code may "
                "keep running orphaned in the background)",
                label, timeout,
            )
            return False
        except Exception:
            log.exception(
                "Agent roster cache: %s raised during cancellation", label,
            )
            return True

    def _drop_unfinished_inflight(self, snapshot: dict[str, "asyncio.Task[None]"]) -> None:
        """Drop exactly the namespace scans captured in ``snapshot`` --
        taken *before* the caller's own bounded cancellation wait, never
        read fresh from ``self._inflight`` afterward -- and only if
        ``self._inflight`` still points at that *same* task object. While
        this caller was awaiting cancellation, a concurrent reconcile or
        opportunistic join could have already untracked the old
        (abandoned) task and installed a brand-new, legitimately in-flight
        scan under the same key; popping unconditionally would orphan
        that newer scan's own tracking instead of merely abandoning the
        one this caller actually gave up on. A dropped scan's own
        eventual, late publication is discarded by its entry's generation
        guard -- bumped right here, not left to whenever a *replacement*
        scan happens to start: a cancellation-resistant resolver can
        still return between this cleanup and that later
        ``_refresh_namespace()`` call, and until the generation actually
        moves, the abandoned task's own captured generation still matches
        the live entry's, so its late result would otherwise pass the
        guard and briefly publish stale rows under a fresh timestamp --
        exactly the bug this method's own docstring claims cannot
        happen."""
        for ns, task in snapshot.items():
            if not task.done() and self._inflight.get(ns) is task:
                self._inflight.pop(ns, None)
                self._inflight_token.pop(ns, None)
                entry = self._entries.get(ns)
                if entry is not None:
                    entry.generation += 1

    async def _watchdog(self) -> None:
        """Force-restart the periodic loop if its current refresh cycle has
        been running for longer than ``watchdog_timeout`` -- the
        independent progress check a loop that merely *hangs* (never
        raises, never returns) needs, since :meth:`_on_loop_done` only ever
        fires once a task actually completes."""
        while True:
            await asyncio.sleep(self._refresh_interval)
            if self._stopping:
                return
            started = self._cycle_started_at
            if started is None or (time.monotonic() - started) <= self._watchdog_timeout:
                continue
            log.error(
                "Agent roster cache refresh cycle has been running for "
                "%.0fs (> %.0fs) -- force-restarting the background loop",
                time.monotonic() - started, self._watchdog_timeout,
            )
            task, self._task = self._task, None
            if task is not None:
                inflight_snapshot = dict(self._inflight)
                finished = await self._cancel_and_wait_bounded(
                    task, timeout=self._watchdog_timeout,
                    label="background refresh loop",
                )
                if not finished:
                    # The abandoned loop's own in-flight namespace scans
                    # (gathered inside its stuck cycle) must not keep
                    # blocking the replacement loop's own joins for the
                    # same namespaces.
                    self._drop_unfinished_inflight(inflight_snapshot)
            self._cycle_started_at = None
            if not self._stopping:
                self._spawn_loop()

    async def stop(self) -> None:
        self._stopping = True
        if self._watchdog_task is not None and not self._watchdog_task.done():
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass
        self._watchdog_task = None
        if self._task is not None and not self._task.done():
            # Bounded: a stuck cycle's own cancellation-resistant namespace
            # scan must never hold daemon shutdown hostage either.
            await self._cancel_and_wait_bounded(
                self._task, timeout=self._CANCELLATION_GRACE,
                label="background refresh loop",
            )
        self._task = None
        pending = [t for t in self._inflight.values() if not t.done()]
        for task in pending:
            task.cancel()
        results = await asyncio.gather(
            *(
                asyncio.wait_for(asyncio.shield(t), timeout=self._CANCELLATION_GRACE)
                for t in pending
            ),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, asyncio.CancelledError):
                continue
            if isinstance(result, (TimeoutError, asyncio.TimeoutError)):
                log.warning(
                    "Agent roster cache: an in-flight namespace scan did "
                    "not honor cancellation within %.1fs at shutdown -- "
                    "abandoning it",
                    self._CANCELLATION_GRACE,
                )
            elif isinstance(result, BaseException):
                log.exception(
                    "Agent roster cache in-flight scan raised during shutdown",
                    exc_info=result,
                )
        self._inflight.clear()
        self._inflight_token.clear()
        if self._discovery_task is not None and not self._discovery_task.done():
            # Unlike the plain-coroutine tasks above, a discovery task's
            # worker thread (a daemon thread via ``_run_in_daemon_thread``)
            # cannot actually be cancelled or interrupted once started --
            # cancelling the task only stops *this* coroutine from waiting
            # on it, it does not stop the thread. Bound the wait so a hung
            # scan can never pin daemon shutdown itself; the orphaned
            # thread is left to finish (or not) on its own, with nothing
            # left awaiting it -- being a daemon thread, it also can never
            # pin the process's own eventual exit.
            self._discovery_task.cancel()
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._discovery_task),
                    timeout=self._DISCOVERY_SHUTDOWN_GRACE,
                )
            except asyncio.CancelledError:
                pass
            except (TimeoutError, asyncio.TimeoutError):
                log.warning(
                    "Agent roster cache: discovery scan still running at "
                    "shutdown after %.1fs; its worker thread cannot be "
                    "forcibly stopped -- proceeding without waiting further",
                    self._DISCOVERY_SHUTDOWN_GRACE,
                )
            except Exception:
                log.exception("Agent roster cache discovery scan raised during shutdown")
        self._discovery_task = None
        # No executor to shut down: _run_in_daemon_thread spawns a
        # single-use daemon thread per attempt rather than a pool, so there
        # is nothing here that could itself pin process exit -- a still-
        # running orphaned scan's thread is simply abandoned when the
        # process eventually exits.

    async def wait_until_warm(self, *, timeout: float | None = None) -> bool:
        """Block until the cache's first discovery pass and every
        currently-known namespace's first scan *attempt* has completed --
        used to gate zero-downtime cutover promotion (3b design: never
        promote/route traffic to a new generation whose cache is still fully
        uninitialized). A namespace only needs to have left UNINITIALIZED
        (OK or FAILED both count -- a namespace that is reachably,
        persistently broken must not block cutover forever); it does not
        need to currently be fresh. Returns whether warm-up actually
        completed (``False`` on timeout, so the caller can decide whether a
        timed-out warm-up is itself fatal -- or, as the daemon startup path
        does via :meth:`force_fail_uninitialized_namespaces`, force every
        still-UNINITIALIZED namespace to a terminal ``FAILED`` state before
        proceeding, so readiness never promotes with a namespace left in a
        genuinely unknown, neither-OK-nor-FAILED state)."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if self._discovery_ever_ok and all(
                e.state != _NamespaceState.UNINITIALIZED
                for e in self._entries.values()
            ):
                return True
            if deadline is not None and time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.05)

    async def force_fail_uninitialized_namespaces(self) -> None:
        """Force every still-UNINITIALIZED namespace to a terminal
        ``FAILED`` state, cancelling its in-flight scan (if any) first.

        Used after a timed-out :meth:`wait_until_warm`: a namespace resolver
        with its own timeout disabled (``AGENT_BRIDGE_NAMESPACE_LIST_
        RESOLVER_TIMEOUT=0``) can hang past the warm-up bound entirely,
        leaving that namespace genuinely UNINITIALIZED forever -- readiness
        must never promote while any namespace is still in that
        neither-OK-nor-FAILED limbo. Cancelling the in-flight task here is
        safe: it is this cache's own internal supervision acting (not a
        caller's request cancellation), the same category of direct
        cancellation :meth:`stop`/the watchdog already perform.

        Every still-uninitialized namespace's scan is cancelled up front
        and awaited *concurrently*, bounded by one single
        ``_CANCELLATION_GRACE`` window total -- not one bound awaited in
        sequence per namespace. With multiple cancellation-resistant
        providers, awaiting each one separately could delay this escape
        hatch by ``N * _CANCELLATION_GRACE`` on top of the advertised
        warm-up bound, which can exceed the cutover health window this
        method exists to protect.
        """
        pending: dict[str, "asyncio.Task[None]"] = {}
        for ns, entry in list(self._entries.items()):
            if entry.state != _NamespaceState.UNINITIALIZED:
                continue
            task = self._inflight.get(ns)
            if task is not None and not task.done():
                pending[ns] = task
        for task in pending.values():
            task.cancel()
        if pending:
            results = await asyncio.gather(
                *(
                    asyncio.wait_for(asyncio.shield(t), timeout=self._CANCELLATION_GRACE)
                    for t in pending.values()
                ),
                return_exceptions=True,
            )
            for ns, task, result in zip(pending, pending.values(), results):
                if isinstance(result, (TimeoutError, asyncio.TimeoutError)):
                    log.warning(
                        "Agent roster cache: namespace '%s:' scan did not "
                        "honor cancellation within %.1fs while force-"
                        "failing an uninitialized namespace -- abandoning it",
                        ns, self._CANCELLATION_GRACE,
                    )
                    # Only drop if still the same task we cancelled -- a
                    # concurrent caller may have already untracked it and
                    # installed a brand-new, legitimately in-flight scan
                    # under the same key while this wait was in progress;
                    # popping unconditionally would orphan that newer
                    # scan's own tracking. Bump the entry's own generation
                    # here too (same as `_drop_unfinished_inflight`) --
                    # about to be force-set to FAILED below, it must never
                    # be silently overwritten back to OK by this abandoned
                    # task's own late, generation-matching publish.
                    if self._inflight.get(ns) is task:
                        self._inflight.pop(ns, None)
                        self._inflight_token.pop(ns, None)
                        live_entry = self._entries.get(ns)
                        if live_entry is not None:
                            live_entry.generation += 1
                elif isinstance(result, Exception):
                    log.exception(
                        "Agent roster cache: namespace '%s:' scan raised "
                        "while force-failing an uninitialized namespace",
                        ns, exc_info=result,
                    )
        # Re-reconcile and re-fetch before the final force-fail pass, rather
        # than mutating the `entry` objects captured in the loop above: a
        # concurrent caller (e.g. an already-served request's own
        # `get_snapshot()`/`_reconcile_namespace_set()`, since topology is
        # marked ready before warm-up completes) can run during any of the
        # `await`s above and replace a namespace's entry object outright (a
        # provider swap) or add a brand-new namespace entirely -- mutating
        # a now-detached, stale `entry` reference would silently leave the
        # *current*, live entry sitting in `self._entries[ns]` still
        # UNINITIALIZED, violating the cutover invariant this method exists
        # to enforce. Reconciling and reading `self._entries` fresh here
        # closes that window.
        self._reconcile_namespace_set()
        for entry in self._entries.values():
            if entry.state == _NamespaceState.UNINITIALIZED:
                entry.state = _NamespaceState.FAILED


    # -- periodic refresh -----------------------------------------------------

    async def _loop(self) -> None:
        first = True
        while True:
            if not first:
                await asyncio.sleep(self._refresh_interval)
            first = False
            self._cycle_started_at = time.monotonic()
            try:
                await self._refresh_cycle()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Agent roster cache periodic refresh cycle failed")
            finally:
                # Only clear this if the current task is still the one
                # tracked as `self._task` -- an abandoned, cancellation-
                # resistant old loop (the watchdog already force-restarted
                # it and installed a replacement) can still reach this
                # `finally` later, after the replacement loop has already
                # set its own fresh `_cycle_started_at`. Clearing
                # unconditionally would wipe out the replacement's own
                # legitimate progress timestamp, blinding the watchdog's
                # own hang detection to a subsequent hang in the
                # replacement itself (`started is None` reads as "no
                # active cycle", never as "hung").
                if self._task is asyncio.current_task():
                    self._cycle_started_at = None

    async def _refresh_cycle(self) -> None:
        await self._maybe_refresh_discovery(force=True)
        self._reconcile_namespace_set()
        namespaces = list(self._entries)
        await asyncio.gather(
            *(self._refresh_namespace(ns) for ns in namespaces),
            return_exceptions=True,
        )

    def _reconcile_namespace_set(self) -> None:
        """Track ``refresh_provider_resolvers()``'s own dynamic namespace
        set -- a namespace it just removed is retired from the cache
        outright (never left serving stale agents for a provider that no
        longer exists); a newly-registered namespace enters as
        UNINITIALIZED, same as any brand-new namespace. A namespace whose
        *provider* changed (the same prefix re-bound to a different
        resolver instance -- a replacement, detected via object identity)
        is invalidated immediately, right here, rather than waiting for the
        next scan attempt to notice -- the old provider's last-known-good
        rows must never keep serving as authoritative across the swap, even
        for the brief window before anything re-triggers a scan. Any
        in-flight scan for a namespace this reconcile drops or invalidates
        is cancelled here rather than merely untracked -- an untracked but
        still-running scan would survive :meth:`stop` entirely (shutdown
        only cancels/awaits tasks still present in ``_inflight``)."""
        current = self._resolver.namespace_resolvers
        for ns in list(self._entries):
            if ns not in current:
                self._entries.pop(ns, None)
                self._cancel_inflight_nowait(ns)
        for ns, resolver_obj in current.items():
            entry = self._entries.get(ns)
            if entry is None or entry.provider_ref is not resolver_obj:
                self._entries[ns] = _NamespaceEntry(provider_ref=resolver_obj)
                self._cancel_inflight_nowait(ns)

    def _cancel_inflight_nowait(self, ns: str) -> None:
        """Cancel (without awaiting) and untrack any in-flight scan for
        ``ns``. Fire-and-forget is safe here: the cancelled task's own
        ``finally`` clause only pops itself from ``_inflight`` if it is
        still the CURRENT entry for ``ns`` (it won't be, since this method
        already popped it), and :meth:`stop` separately awaits every
        task still reachable from any live reference at shutdown -- this
        only needs to ensure a dropped task doesn't keep running
        *untracked* past this reconcile."""
        task = self._inflight.pop(ns, None)
        self._inflight_token.pop(ns, None)
        if task is not None and not task.done():
            task.cancel()

    # -- single-flight, generation-guarded per-namespace scan ------------------

    def _namespace_resolver_ref(self, ns: str) -> object | None:
        return self._resolver.namespace_resolvers.get(ns)

    def _refresh_namespace(self, ns: str) -> asyncio.Task[None]:
        """Single-flight: an already in-flight scan for ``ns`` against the
        *same* provider generation is returned as-is so a concurrent
        caller/trigger joins it rather than starting a duplicate scan. A
        provider replacement invalidates the in-flight task too -- waiting on
        a scan that started against the now-superseded resolver would be
        wrong even if that scan is still technically running.

        The provider identity is tracked via a **strong reference** to the
        resolver object itself (``is`` comparison), not ``id()`` alone --
        once the old resolver is unregistered, nothing else may keep it
        alive, and Python is free to reuse its address for the very
        replacement object being compared against, which would make a
        genuine provider swap look unchanged under an ``id()``-only token.
        """
        current_ref = self._namespace_resolver_ref(ns)
        existing = self._inflight.get(ns)
        if (
            existing is not None
            and not existing.done()
            and self._inflight_token.get(ns) is current_ref
        ):
            return existing

        entry = self._entries.get(ns)
        if entry is None or entry.provider_ref is not current_ref:
            # Brand-new namespace, or the same namespace re-bound to a
            # *different* resolver instance (a provider replacement) --
            # never keep serving the prior provider's last-known-good rows
            # as authoritative across the swap.
            entry = _NamespaceEntry(provider_ref=current_ref)
            self._entries[ns] = entry
        entry.generation += 1
        my_generation = entry.generation

        task = asyncio.create_task(self._do_scan_namespace(ns, current_ref, my_generation))
        self._inflight[ns] = task
        self._inflight_token[ns] = current_ref
        return task

    async def _do_scan_namespace(self, ns: str, provider_ref: object | None, generation: int) -> None:
        resolver = self._resolver.namespace_resolvers.get(ns)
        rows: list[dict[str, Any]] | None = None
        ok = False
        try:
            if resolver is None:
                raise LookupError(f"namespace '{ns}:' no longer registered")
            # This cache owns the advertised refresh cadence -- a resolver's
            # own separate, shorter result TTL (e.g. `CliNamespaceResolver`'s
            # internal `_list_cache`) must never let a scan this cache
            # triggers (periodic, opportunistic, or `force_refresh`) quietly
            # publish stale cached rows under a *fresh* cache timestamp.
            invalidate = getattr(resolver, "invalidate_list_cache", None)
            if callable(invalidate):
                invalidate()
            rows = await self._resolver.scan_namespace_async(ns)
            ok = True
        except asyncio.CancelledError:
            raise
        except Exception:
            if resolver is not None:
                log.warning(
                    "Agent roster cache: namespace '%s:' scan failed",
                    ns,
                    exc_info=True,
                )
        finally:
            if self._inflight.get(ns) is asyncio.current_task():
                self._inflight.pop(ns, None)
                self._inflight_token.pop(ns, None)

        entry = self._entries.get(ns)
        # The cache's own `entry.provider_ref` is updated by
        # `_reconcile_namespace_set()`, which only runs once per periodic
        # cycle -- an out-of-band caller (e.g. the dispatch-task route or
        # `AgentResolver.resolve_async()`) can call
        # `refresh_provider_resolvers()` directly, swapping the live
        # resolver for `ns` *before* this cache's own reconcile has noticed,
        # while this scan was still awaiting the old resolver's `list()`.
        # Rechecking the resolver's *current, live* namespace-resolver
        # identity here (not just the cache's own, possibly-stale `entry`)
        # closes that window: a swap the cache hasn't reconciled yet still
        # discards this now-superseded scan's rows rather than publishing
        # them as fresh.
        live_ref = self._namespace_resolver_ref(ns)
        if (
            entry is None
            or entry.provider_ref is not provider_ref
            or entry.generation != generation
            or live_ref is not provider_ref
        ):
            # Superseded by a provider replacement (caught either by the
            # cache's own reconcile, or -- the live-identity recheck above --
            # an out-of-band swap it hasn't reconciled yet) or a newer scan
            # attempt (single-flight makes this unreachable in practice
            # today, but the generation guard is the explicit, tested
            # belt-and-suspenders the 3b design calls for) -- discard,
            # never overwrite.
            return
        if ok:
            entry.state = _NamespaceState.OK
            entry.rows = rows or []
            entry.last_refreshed_monotonic = time.monotonic()
        else:
            entry.state = _NamespaceState.FAILED
            # last-known-good rows/timestamp (if any) are left untouched.

    # -- read path ------------------------------------------------------------

    async def get_snapshot(self, *, force_refresh: bool = False) -> AgentRosterSnapshot:
        """Build this request's response. Any namespace that is currently
        uninitialized, failed, or past its freshness deadline
        opportunistically joins its single-flight refresh before the
        response is built -- "any GET that observes a namespace as
        incomplete, uninitialized, or past its freshness deadline must
        itself opportunistically join that namespace's single-flight
        refresh" (3b design), so an old CLI's plain, unparameterized retry
        loop still actually triggers a real rescan without needing to know
        about ``force_refresh`` at all. ``force_refresh=True`` additionally
        forces an immediate discovery re-scan (bypassing its own internal
        TTL) so a brand-new/replaced provider is noticed right away --
        namespace scans themselves are not forced for an already-fresh,
        healthy namespace; the join set is always exactly "uninitialized,
        failed, or stale", matching the plain opportunistic-join path, so
        one broken provider never makes every other, healthy namespace's
        provider re-run its scan on each bounded retry.

        Each namespace's join is bounded at ``_opportunistic_join_budget``
        (short, independent of ``_watchdog_timeout``) -- a namespace scan
        is kicked off (or joined) regardless, but this request never waits
        past that short budget for it: past it, the response falls back to
        whatever last-known-good (or empty, if uninitialized) data is
        already cached, with the namespace reported incomplete. The scan
        itself keeps running in the background past this timeout; a later
        request (or the periodic loop) still observes its eventual result.

        The discovery join itself is bounded at ``2 * _watchdog_timeout``
        (``max_wait``) -- a persistently hung discovery (every attempt
        hangs and gets abandoned-and-replaced, forever) must not keep this
        request waiting indefinitely either; background supervision (the
        periodic loop's own unbounded call) keeps retrying regardless. The
        multiplier (rather than exactly one ``_watchdog_timeout``) leaves
        room for one full abandon-and-replace cycle *plus* the
        replacement attempt's own chance to actually finish -- bounding at
        exactly one timeout would give a transient single-hang-then-
        recover case (the common, benign shape) no time at all for its
        recovering second attempt."""
        await self._maybe_refresh_discovery(
            force=force_refresh, max_wait=self._watchdog_timeout * 2,
        )
        self._reconcile_namespace_set()
        now = time.monotonic()
        to_join = [
            ns
            for ns, entry in self._entries.items()
            if not entry.is_fresh(freshness_deadline=self._freshness_deadline, now=now)
        ]
        if to_join:
            # Shielded: these are shared single-flight tasks (the periodic
            # loop or another concurrent request may be awaiting the exact
            # same task) -- awaiting them directly would let this request's
            # own cancellation (e.g. a client disconnect) propagate into
            # and abort the underlying scan for every other waiter too.
            # Cache-internal supervision (stop()/the watchdog) still cancels
            # the real task directly; shielding only protects it from a
            # caller-side cancellation it was never meant to control.
            #
            # Each join is bounded by `_opportunistic_join_budget`, NOT the
            # (far more generous) `_watchdog_timeout` -- a namespace whose
            # own scan is reliably slower than this (a real, observed
            # shape: GitHub Codespaces/Docker enumeration via a CLI
            # subprocess with no cache of its own, 10-16s) must never make
            # every single caller of `get_snapshot()` pay its full scan
            # cost -- that is exactly the per-call cost this cache exists
            # to eliminate. The task itself is NOT cancelled on this
            # timeout (`asyncio.wait_for` only abandons *this* await, and
            # the task is `asyncio.shield`-ed besides) -- it keeps running
            # in the background exactly like any other in-flight scan, so
            # a later request (or the periodic refresh loop) still picks
            # up its result once it actually finishes; this bound only
            # governs how long any *one* caller waits for it inline before
            # falling back to whatever last-known-good (or empty, if
            # uninitialized) data is already cached. It is ALSO the bound
            # the external watchdog story above still applies to (an
            # orphaned, cancellation-resistant task this request joined
            # can still hang past its own abandonment) -- the freshness
            # recheck below already treats a namespace whose scan never
            # progressed as incomplete, so no retry loop is needed here
            # either.
            await asyncio.gather(
                *(
                    asyncio.wait_for(
                        asyncio.shield(self._refresh_namespace(ns)),
                        timeout=self._opportunistic_join_budget,
                    )
                    for ns in to_join
                ),
                return_exceptions=True,
            )

        # Reconcile again after the join: while `gather` was awaiting, an
        # out-of-band `refresh_provider_resolvers()` call (bypassing this
        # cache entirely) can still replace or remove a provider after its
        # *old* scan already published -- without this second reconcile,
        # the assembly below would read `self._entries` as it stood before
        # that swap/removal became visible, serving a provider's last-
        # known-good rows once after it was already gone or replaced.
        self._reconcile_namespace_set()

        # Freshness is rechecked here, after the join, rather than trusting
        # `entry.state == OK` alone: a namespace whose in-flight scan was
        # cancelled out from under this request (e.g. the watchdog
        # force-restarting a hung periodic-loop cycle this request had
        # joined via single-flight) never updates `entry.state` at all --
        # without this recheck, its last-known-good rows would still
        # publish as current/complete even though the refresh this request
        # itself waited on never actually happened.
        now = time.monotonic()
        rows = list(self._resolver.list_agents())
        incomplete_namespaces: list[str] = []
        # Iterate the *live* resolver's own namespace-registration order
        # (not `self._entries`'s own dict order, which a namespace removed
        # and later re-added could leave reordered relative to its
        # original registration position) -- `list_agents_async()`'s own
        # non-cached rows/incomplete_namespaces preserve this exact order,
        # and the cached path's default response must match it byte-for-
        # byte, never silently resorting to alphabetical.
        for ns in self._resolver.namespace_resolvers:
            entry = self._entries.get(ns)
            if entry is None:
                continue
            if entry.is_fresh(freshness_deadline=self._freshness_deadline, now=now):
                rows.extend(entry.rows)
            else:
                incomplete_namespaces.append(ns)

        discovery_fresh = self._discovery_ever_ok and (
            self._discovery_ok_at is not None
            and (now - self._discovery_ok_at) <= self._freshness_deadline
        )
        complete = discovery_fresh and not incomplete_namespaces
        return AgentRosterSnapshot(
            rows=rows,
            incomplete_namespaces=incomplete_namespaces,
            complete=complete,
        )

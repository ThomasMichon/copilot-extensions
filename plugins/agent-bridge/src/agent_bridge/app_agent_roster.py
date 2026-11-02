"""agent-bridge daemon startup/shutdown wiring for the Phase 3b agent-roster
background cache -- split out of ``app.py`` to stay within its module-size
cap (pivot-streaming-transport Phase 3b, mirroring Phase 3a's own
``client_events.py`` extraction for the same reason).
"""

from __future__ import annotations

import asyncio
import inspect
import logging

log = logging.getLogger("agent-bridge")


class _RosterHosting:
    """Keep the cache's original discovery operations beyond cancelled joiners."""

    def __init__(self, cache):
        self.cache = cache
        self.discoveries = set()
        self.stopping = None
        prior = cache._discovery_task
        self.unknown_discovery = (
            cache._discovery_generation > 1
            or (prior is None and cache._discovery_started_at is not None)
        )
        if self.unknown_discovery:
            log.warning("Late roster attachment lacks discovery history; retaining unknown completion")
        if prior is not None:
            if (
                not prior.done()
                and inspect.getcoroutinestate(prior.get_coro()) == inspect.CORO_CREATED
            ):
                prior.cancel()
                self._retain(prior, lambda: False)
            else:
                self._retain(prior, lambda: True)
                if not prior.done():
                    async def join_prior():
                        await asyncio.shield(prior)

                    cache._discovery_task = asyncio.create_task(join_prior())
        original = cache._do_scan_discovery

        async def discover(generation):
            dispatched = False

            async def operation():
                nonlocal dispatched
                if cache._stopping or generation != cache._discovery_generation:
                    return
                dispatched = True
                await original(generation)

            owner = asyncio.create_task(operation())
            self._retain(owner, lambda: dispatched)
            return await asyncio.shield(owner)

        cache._do_scan_discovery = discover

    def _retain(self, owner, dispatched):
        self.discoveries.add(owner)

        def completed(task):
            if task.cancelled() and dispatched():
                log.warning("Discovery owner cancelled; retaining unknown native completion")
                return
            self.discoveries.discard(task)
            if not task.cancelled():
                task.exception()

        owner.add_done_callback(completed)

    async def _stop(self):
        # Retained native results must still pass the cache's original token
        # check; a stopped generation must never publish a belated report.
        if not self.cache._stopping:
            self.cache._discovery_generation += 1
        await self.cache.stop()

    async def stop(self, *, settle):
        if self.stopping is None:
            self.stopping = asyncio.create_task(self._stop())
        cancellation = None
        failure = None
        while True:
            try:
                await asyncio.shield(self.stopping)
                break
            except asyncio.CancelledError as error:
                cancellation = error
                if self.stopping.cancelled():
                    self.stopping = asyncio.create_task(self._stop())
            except Exception as error:
                failure = error
                break
        if settle:
            while self.pending:
                try:
                    await asyncio.sleep(.02)
                except asyncio.CancelledError as error:
                    cancellation = error
        if cancellation is not None:
            raise cancellation
        if failure is not None:
            raise failure

    @property
    def pending(self):
        native = self.cache._provider_work
        return (
            self.unknown_discovery
            or any(task.cancelled() or not task.done() for task in self.discoveries)
            or any(not task.done() for task in self.cache._owned_scans)
            or any(not task.done() for task in native.reads)
            or any(not future.done() for future in native.samples)
        )


async def start_agent_roster_cache(app, resolver, cfg) -> None:
    """Start the Phase 3b agent-roster cache for ``resolver`` and block
    until it warms up -- gates readiness so a zero-downtime cutover never
    promotes a generation whose cache hasn't completed its own first scan.
    A bounded wait: a genuinely unreachable namespace provider must not
    block readiness forever (its entry stays FAILED, never silently OK).
    A no-op for a test double that isn't a real ``AgentResolver`` -- it
    doesn't implement the namespace-resolver surface the cache needs and
    never has any namespaces to cache in the first place.

    Idempotent against ``_initialize_readiness()``'s own background-retry
    loop: a prior attempt's cache (if any) is stopped first, so a retried
    initialization never leaks a duplicate, still-scanning background loop
    that shutdown could then only ever stop the newest of.
    """
    from .agent_registry import AgentResolver
    from .agent_registry_cache import AgentRosterCache

    if not isinstance(resolver, AgentResolver):
        return
    await stop_agent_roster_cache(app, settle=False)
    roster_cache = AgentRosterCache(
        resolver, refresh_interval=cfg.agent_roster_cache_interval,
    )
    roster_cache._hosting_custody = _RosterHosting(roster_cache)
    app.state.agent_roster_cache = roster_cache
    roster_cache.start()
    warmed = await roster_cache.wait_until_warm(timeout=30.0)
    if not warmed:
        log.warning(
            "Agent roster cache did not finish warming up within 30s -- "
            "force-failing any still-uninitialized namespace so readiness "
            "never promotes with one left in a genuinely unknown state"
        )
        await roster_cache.force_fail_uninitialized_namespaces()
        if not roster_cache.discovery_ever_ok:
            # Discovery itself never completed a single clean pass within
            # the warm-up bound (e.g. a hung initial scan) -- `_entries`
            # can still be empty at this point, so the force-fail above
            # had nothing to act on and `wait_until_warm`'s own check
            # vacuously passed over zero known namespaces. Readiness must
            # never publish for a roster that was never actually scanned
            # at all -- raising here (rather than silently returning)
            # routes through `_initialize_readiness()`'s own existing
            # retry/backoff loop, the same recovery path any other
            # startup failure already takes (it stops this cache and
            # starts a fresh one on the next attempt).
            raise RuntimeError(
                "agent roster cache: discovery never completed a clean "
                "pass within the warm-up timeout -- refusing to publish "
                "readiness with an unscanned roster"
            )


async def stop_agent_roster_cache(app, *, settle=True) -> None:
    roster_cache = getattr(app.state, "agent_roster_cache", None)
    if roster_cache is not None:
        custody = getattr(roster_cache, "_hosting_custody", None)
        if custody is None:
            custody = roster_cache._hosting_custody = _RosterHosting(roster_cache)
        retired = getattr(app.state, "retiring_agent_rosters", None)
        if retired is None:
            retired = app.state.retiring_agent_rosters = []
        if custody not in retired:
            retired.append(custody)
    retired = getattr(app.state, "retiring_agent_rosters", [])
    cancellation = None
    failure = None
    try:
        for custody in tuple(retired):
            try:
                await custody.stop(settle=settle)
            except asyncio.CancelledError as error:
                cancellation = error
            except Exception as error:
                failure = error
    finally:
        retired[:] = [custody for custody in retired if custody.pending]
    if cancellation is not None:
        raise cancellation
    if failure is not None:
        raise failure

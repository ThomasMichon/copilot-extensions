"""agent-bridge daemon startup/shutdown wiring for the Phase 3b agent-roster
background cache -- split out of ``app.py`` to stay within its module-size
cap (pivot-streaming-transport Phase 3b, mirroring Phase 3a's own
``client_events.py`` extraction for the same reason).
"""

from __future__ import annotations

import logging

from .agent_registry import AgentResolver
from .agent_registry_cache import AgentRosterCache

log = logging.getLogger("agent-bridge")


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
    if not isinstance(resolver, AgentResolver):
        return
    await stop_agent_roster_cache(app)
    roster_cache = AgentRosterCache(
        resolver, refresh_interval=cfg.agent_roster_cache_interval,
    )
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


async def stop_agent_roster_cache(app) -> None:
    roster_cache = getattr(app.state, "agent_roster_cache", None)
    if roster_cache is not None:
        await roster_cache.stop()

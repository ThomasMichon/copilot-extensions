"""Discovery-scan supervision for :class:`agent_registry_cache.AgentRosterCache`
(pivot-streaming-transport Phase 3b) -- extracted from that module to keep it
under the repository's module-size cap (CONTRIBUTING.md's componentization
convention: split a growing module by responsibility well before the hard
1000-line ceiling).

Owns the daemon-thread execution primitive (:func:`_run_in_daemon_thread`)
and the discovery-specific half of the cache's supervision (:class:`
_DiscoveryMixin`'s ``_maybe_refresh_discovery``/``_do_scan_discovery``):
single-flight plus generation-guarded discovery scans, hung-scan detection
and recovery, and the bounded per-caller retry loop that keeps any joiner
from being stranded on an abandoned generation. Mixed into
``AgentRosterCache`` itself, which owns all the state these methods read and
write (``_discovery_task``, ``_discovery_generation``, etc. -- see that
class's own ``__init__`` for the full field set) and the namespace-scan
machinery this module's own docstrings cross-reference.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any

from .agent_registry_discovery import DiscoveryResult

log = logging.getLogger("agent-bridge")


async def _run_in_daemon_thread(func, *args, **kwargs):
    """Run ``func`` in a brand-new, single-use ``daemon=True`` thread and
    await its result.

    Deliberately *not* a shared/pooled executor (``asyncio.to_thread``'s
    default pool, or a dedicated ``ThreadPoolExecutor``): a pool bounds
    worker *count* but not its internal work *queue* -- once every worker
    is wedged on a genuinely hung call, further submissions just queue
    forever behind them, silently reintroducing the exact "can never
    recover" failure this exists to avoid. A fresh thread per call never
    queues -- it always starts immediately, so a hung prior attempt can
    never block a new one.

    ``daemon=True`` is the other half: Python's ``concurrent.futures.
    thread`` module registers a process-wide ``atexit`` hook that joins
    every live *non-daemon* ``ThreadPoolExecutor`` worker at interpreter
    exit regardless of any pool's own ``shutdown(wait=False)`` -- a hung
    scan would otherwise pin process exit (and therefore ZDD retirement)
    for as long as the underlying blocking call takes, which can be
    unbounded. A daemon thread is never joined at interpreter exit; a
    permanently-hung one is simply abandoned when the process exits.

    The trade-off: under a *persistent*, never-recovering hang, repeated
    watchdog-triggered retries do create one new orphaned OS thread per
    retry rather than reusing a bounded pool -- accepted deliberately,
    since the retry cadence (gated by the cache's own watchdog timeout) is
    slow, and a slowly-growing set of harmlessly-abandoned daemon threads
    is a far better failure mode than either queueing forever or pinning
    shutdown.

    A watchdog-abandoned thread can still be running when the event loop
    itself closes (daemon shutdown) -- its later ``call_soon_threadsafe``
    call would then raise ``RuntimeError: Event loop is closed`` on the
    worker thread (an unhandled exception there, surfaced as a stray
    thread traceback). Caught and ignored here, the same shutdown race
    ``app.py``'s own equivalent readiness helper already guards against."""
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()

    def _set_result(result: Any) -> None:
        if not future.done():
            future.set_result(result)

    def _set_exception(exc: BaseException) -> None:
        if not future.done():
            future.set_exception(exc)

    def _runner() -> None:
        try:
            result = func(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - propagated to the awaiter
            try:
                loop.call_soon_threadsafe(_set_exception, exc)
            except RuntimeError:
                pass  # event loop already closed -- nothing left to deliver to
        else:
            try:
                loop.call_soon_threadsafe(_set_result, result)
            except RuntimeError:
                pass  # event loop already closed -- nothing left to deliver to

    threading.Thread(
        target=_runner, daemon=True, name="agent-roster-discovery",
    ).start()
    return await future


class _DiscoveryMixin:
    """Discovery-scan half of :class:`AgentRosterCache`'s supervision.

    Expects (and is always mixed into a class that provides) the discovery
    fields ``AgentRosterCache.__init__`` sets up (``_discovery_task``,
    ``_discovery_generation``, ``_discovery_started_at``, ``_discovery_
    ok_at``, ``_discovery_ever_ok``, ``_watchdog_timeout``, ``_freshness_
    deadline``, ``_resolver``) and the namespace-side ``_reconcile_
    namespace_set`` method it calls on a clean pass."""

    async def _maybe_refresh_discovery(
        self, *, force: bool, max_wait: float | None = None,
    ) -> None:
        """Run (or opportunistically trigger) one discovery pass. Only a
        genuinely clean pass (``DiscoveryResult.ok``) advances
        ``_discovery_ok_at``/``_discovery_ever_ok`` -- a raised scan or a
        partial per-manifest construction failure leaves the previous
        discovery generation's freshness untouched, so it can go stale on
        its own and trigger the `503` contract independently of any single
        namespace's own last-known-good state. Mirrors the per-namespace
        opportunistic-join design: :meth:`get_snapshot` calls this with
        ``force=False`` so discovery itself is also rescanned once its own
        freshness deadline has passed, not only on the periodic loop's own
        cadence -- the cache is fully self-sufficient even if the periodic
        loop is slow, stalled, or (in a test) never started at all.

        ``max_wait``, if given, bounds this call's *own total* time across
        every retry iteration below -- used by a request-triggered caller
        (:meth:`get_snapshot`) so a *persistently* hung discovery (every
        attempt hangs past ``_watchdog_timeout`` and gets abandoned-and-
        replaced, forever) cannot keep that request waiting indefinitely;
        it gives up and returns once ``max_wait`` elapses, leaving
        ``_discovery_ever_ok`` exactly as it found it (so the caller's own
        downstream freshness/completeness check reports accordingly). Left
        ``None`` (the default, and what the periodic loop's own call uses)
        for unbounded background supervision -- that one is *meant* to
        keep retrying forever regardless of how long discovery takes.

        Runs the scan itself via :func:`_run_in_daemon_thread` (a fresh,
        single-use daemon thread per attempt -- see that helper's
        docstring), which offloads only the slow, subprocess-reaching half
        (:meth:`AgentResolver._scan_provider_report`) off the event loop
        while the fast registry reconciliation
        (:meth:`AgentResolver._apply_provider_report`) stays on the event
        loop -- running that reconciliation synchronously on the daemon's
        event loop would freeze all HTTP handling for however long the
        subprocess-reaching scan takes; running the reconciliation itself
        off-thread would let a concurrent async reader observe a half-
        mutated namespace-resolver registry. Single-flighted (and shielded
        from a cancelled caller) the same way a namespace scan is:
        concurrent triggers (the periodic loop, an opportunistic GET, a
        `force_refresh` retry) join the one in-flight discovery scan rather
        than racing two concurrent mutations of the resolver's own
        namespace-resolver registry.

        A scan that has run past ``_watchdog_timeout`` is treated as hung:
        an ``asyncio.to_thread``-style task's worker thread cannot actually
        be cancelled once started, so awaiting it forever is not an
        option. Rather than waiting, this abandons the stuck attempt (its
        orphaned thread keeps running in the background and its eventual
        result, if any, is discarded via the generation guard in
        :meth:`_do_scan_discovery`) and starts a fresh, next-generation
        attempt immediately, so discovery can always recover even if the
        resolver's own scan hangs indefinitely.

        This whole hung-check-and-replace is itself a bounded retry loop,
        not just a one-shot check: a caller that joined the current task
        *before* it crossed the watchdog threshold is still only ever
        shielded-awaiting it with a bound of ``_watchdog_timeout`` (via
        ``asyncio.wait_for``) -- replacing ``self._discovery_task`` on its
        own only helps *new* callers find the replacement; it does nothing
        for a request already shielded-awaiting the now-abandoned task.
        On its own timeout, this loops back, re-evaluates (the task may
        already have been replaced by another caller, or this call now
        detects the hang itself and replaces it), and rejoins whichever
        task is current -- so no caller can be stranded indefinitely on an
        abandoned generation after a watchdog-triggered recovery."""
        if not force:
            now = time.monotonic()
            fresh = self._discovery_ever_ok and (
                self._discovery_ok_at is not None
                and (now - self._discovery_ok_at) <= self._freshness_deadline
            )
            if fresh:
                return
        deadline = None if max_wait is None else time.monotonic() + max_wait
        while True:
            task = self._discovery_task
            hung = (
                task is not None
                and not task.done()
                and self._discovery_started_at is not None
                and (time.monotonic() - self._discovery_started_at) > self._watchdog_timeout
            )
            if hung:
                log.warning(
                    "Agent roster cache: discovery scan has exceeded %.1fs; "
                    "abandoning it (its worker thread may continue running "
                    "orphaned in the background) and starting a new attempt",
                    self._watchdog_timeout,
                )
            if task is None or task.done() or hung:
                self._discovery_generation += 1
                my_generation = self._discovery_generation
                self._discovery_started_at = time.monotonic()
                task = asyncio.create_task(self._do_scan_discovery(my_generation))
                self._discovery_task = task
            wait_timeout = self._watchdog_timeout
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    # This caller's own overall budget is exhausted --
                    # give up and return (discovery keeps running/being
                    # retried for everyone else exactly as before; this
                    # call just stops waiting on it). Leaves
                    # `_discovery_ever_ok` untouched, so the caller's own
                    # downstream freshness/completeness check reports
                    # accordingly rather than ever hanging indefinitely.
                    return
                wait_timeout = min(wait_timeout, remaining)
            try:
                # Shielded: a hung scan's own stuck task is never awaited
                # past its bound here (above, a fresh task replaces it
                # instead) -- this only ever joins a task that is either
                # already done or genuinely still within its own hang
                # bound. Bounded by `wait_for` so *this* caller -- even one
                # that joined before the task it's awaiting crossed the
                # watchdog threshold -- can never be stranded on it either;
                # on timeout, loop back and rejoin whatever is current.
                await asyncio.wait_for(asyncio.shield(task), timeout=wait_timeout)
                return
            except (TimeoutError, asyncio.TimeoutError):
                continue

    async def _do_scan_discovery(self, generation: int) -> None:
        """Run one discovery scan and publish its outcome -- but only if
        ``generation`` is still the current one.

        The slow, subprocess-reaching half (:meth:`AgentResolver.
        _scan_provider_report`) runs via :func:`_run_in_daemon_thread`: a
        fresh, single-use daemon thread per attempt, never a shared/pooled
        executor -- see that helper's docstring for why a pool (bounded
        workers but an unbounded, non-daemon-backed queue) would
        reintroduce exactly the "can never recover" and "pins process
        exit" failures this design exists to avoid.

        The generation is rechecked *before* applying the scan's report
        (:meth:`AgentResolver._apply_provider_report`), not only before
        publishing this cache's own freshness timestamps: a scan that
        finally finishes after :meth:`_maybe_refresh_discovery` already
        gave up on it (its generation has since been bumped by a newer
        attempt) must never mutate the live namespace-resolver registry
        with its now-stale report -- that could resurrect a provider the
        newer, already-applied generation removed, or roll back a
        replacement it already made. A stale report is discarded whole,
        never applied, regardless of whether it would itself have been
        ``ok``.

        A clean pass also reconciles the namespace set (see
        :meth:`_reconcile_namespace_set`) in this exact same synchronous
        tail, with no ``await`` in between -- :meth:`wait_until_warm`
        polls ``_discovery_ever_ok and all(... for e in self._entries.
        values())``, and an empty ``_entries`` vacuously satisfies
        ``all()`` over nothing. Without this, a poll landing in the gap
        between this method setting ``_discovery_ever_ok = True`` and
        some *other* caller's own later ``_reconcile_namespace_set()``
        call (the periodic cycle's, or an opportunistic
        :meth:`get_snapshot`'s) could observe "discovery ok, zero known
        namespaces" and wrongly treat readiness as warm before any actual
        namespace has even been discovered yet."""
        outcome = await _run_in_daemon_thread(self._resolver._scan_provider_report, force=True)
        if generation != self._discovery_generation:
            return
        if isinstance(outcome, DiscoveryResult):
            discovery = outcome
        else:
            report, start_generation = outcome
            discovery = self._resolver._apply_provider_report(report, start_generation)
        if discovery.ok:
            self._discovery_ok_at = time.monotonic()
            self._discovery_ever_ok = True
            self._reconcile_namespace_set()

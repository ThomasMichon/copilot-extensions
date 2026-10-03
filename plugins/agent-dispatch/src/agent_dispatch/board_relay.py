"""Phase 3a -- the agent-dispatch CLI-relayed daemon fast path.

Lazily imported from ``board_cli.py``'s ``--subscribe`` branch only (never
from the plain one-shot path or the delegated/cross-machine path), so that
``board_cli.py``'s own "fast stdlib-only" footprint for every other
invocation is unchanged -- this module is the only place in the package that
needs ``httpx`` (via :class:`DispatchClient`).

See ``efforts/active/pivot-streaming-transport/phase-3-design.md`` for the
full design this implements. Summary of the architecture actually used here,
which intentionally differs from that document's own multi-writer framing
(every requirement it lists is still satisfied, just by a simpler
mechanism): a **single** control loop drives the whole relay for a CLI
process's lifetime (:func:`run_relay`, via the iterative :func:`_drive`, a
plain ``while`` loop -- not two functions calling each other, which is
still unbounded recursion in Python even split across functions). A
dedicated reader thread does nothing but
turn ``DispatchClient.stream_events()`` SSE frames into items on a queue;
every actual board mutation -- the event-woken re-fetch, the long
reconcile, the local recompute tick, and the transient-failure poll
fallback -- runs one-at-a-time inside that same control loop. A
single-threaded control loop needs no explicit lock to serialize those
writers against each other (there is only ever one of them running at a
time, by construction), and an event that arrives while a fetch is in
flight is simply not read off the queue until the fetch finishes -- so it
is picked up on the very next loop iteration, which is exactly the
"trailing dirty flag" behavior the design document describes, without a
separate flag to get wrong. Reconnects are iterative, not recursive
(:func:`_event_loop`/:func:`_reconnect_loop` each return a plain result
object instead of calling each other directly), so a channel that
reconnects many times over a long process lifetime never grows the Python
call stack.
"""

from __future__ import annotations

import os
import queue
import threading
import time

from .client import DispatchClient

#: Bounded wait for the ready frame after a daemon that advertises support
#: has had its subscription opened. Not advertised at all -> no wait, no
#: barrier, immediate permanent fallback (see :func:`run_relay`).
READY_FRAME_TIMEOUT_SECONDS = 5.0

#: "Trust but verify": a full reconcile re-fetch on this cadence catches
#: anything the (non-replay, in-memory) event stream missed -- a dropped
#: connection's gap, or a bug in event coverage.
LONG_RECONCILE_SECONDS = 45.0

#: Local, zero-network recompute cadence for purely clock-driven fields
#: (activity TTL, stalled-text, recent-mins cutoff, relay-staleness) that no
#: mutation event will ever announce.
RECOMPUTE_INTERVAL_SECONDS = 1.5

#: How long to coalesce a burst of events into a single pending re-fetch
#: before actually running it.
DEBOUNCE_WINDOW_SECONDS = 0.3

#: The trailing (post-debounce) re-fetch is itself still rate-limited to no
#: more than once per this many seconds, so sustained per-task event traffic
#: (e.g. activity/heartbeat events firing for every live task) can't turn the
#: relay into back-to-back full-board fetches -- a worse load than the fixed
#: poll this phase exists to reduce.
MIN_REFETCH_INTERVAL_SECONDS = 2.0

INITIAL_RECONNECT_BACKOFF_SECONDS = 1.0
MAX_RECONNECT_BACKOFF_SECONDS = 30.0
MAX_RECONNECT_ATTEMPTS = 6


class RelayUnavailable(Exception):
    """The daemon doesn't advertise ready-frame support at all -- there is no
    observable subscription barrier to reconcile against (``stream_events()``
    is a lazy generator: calling it proves nothing was even attempted).
    Raised only by :func:`run_relay`'s own first connection attempt, before
    any frame has been emitted for this relay invocation, so the caller can
    fall back to the unmodified poll loop for the whole connection's
    lifetime without risking a double-emission. A daemon that stops
    advertising support *after* the relay is already running (mid-reconnect)
    is treated as an ordinary connection failure instead -- see
    :func:`_reconnect_loop` -- since frames have already been emitted and
    the caller's own "fall back to its stale initial snapshot" contract no
    longer applies at that point."""


class HealthCheckFailed(Exception):
    """``client.health()`` itself failed (network hiccup, timeout, transient
    5xx) -- distinct from a successful response that simply lacks the
    ``events_ready_frame`` capability flag. A caller must not collapse this
    into "daemon doesn't support the fast path": that would turn one
    transient health-check blip into a permanent fallback for the whole
    connection's lifetime instead of a bounded, retryable failure."""


def _daemon_supports_ready_frame(client: DispatchClient) -> bool:
    """Raises :class:`HealthCheckFailed` if ``health()`` itself fails, so the
    caller can route a transient failure through its own reconnect/retry
    path rather than treating it as "capability not advertised" (which would
    permanently disable the relay instead of retrying)."""
    try:
        health = client.health()
    except Exception as exc:
        raise HealthCheckFailed(str(exc)) from exc
    return bool(isinstance(health, dict) and health.get("events_ready_frame"))


def _build_client(args) -> DispatchClient:
    """Construct a fresh client against the currently-resolved endpoint.

    Re-reads ``active.json``/``AGENT_DISPATCH_URL`` via ``board_cli``'s own
    ``_endpoint()`` on every call (never memoized) so a reconnect after a
    zero-downtime coordinator-generation cutover follows the new bind/port
    instead of retrying a client built against the retired one -- the same
    pattern ``ResolvingDispatchClient`` already exists to solve for
    long-running supervisors. May raise (e.g. ``_endpoint()`` finds no
    routing info at all) -- every caller treats that as a transient,
    retryable failure, never an uncaught crash.
    """
    from . import board_cli

    endpoint = board_cli._endpoint()
    token = os.environ.get("AGENT_DISPATCH_TOKEN")
    return DispatchClient(endpoint, token=token)


class _Reader:
    """Background thread turning one ``stream_events()`` SSE connection into
    queue items: ``("ready", None)``, ``("event", payload)``, or
    ``("disconnected", exc_or_None)`` (the last one terminates the thread)."""

    def __init__(self, client: DispatchClient):
        self._client = client
        self.queue: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        try:
            for payload in self._client.stream_events(ready_frame=True):
                if payload.get("type") == "ready":
                    self.queue.put(("ready", None))
                else:
                    self.queue.put(("event", payload))
            self.queue.put(("disconnected", None))
        except Exception as exc:  # any failure here means "reconnect"
            self.queue.put(("disconnected", exc))


def _wait_for_ready(reader: _Reader, timeout: float) -> bool:
    """Drain the reader's queue until the ready sentinel or the bounded
    timeout. Returns ``False`` on timeout (treated as a stream failure --
    never waits forever) or if the connection fails before ready arrives."""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        try:
            kind, _payload = reader.queue.get(timeout=remaining)
        except queue.Empty:
            return False
        if kind == "ready":
            return True
        if kind == "disconnected":
            return False
        # An ordinary event arriving before the ready frame is unexpected
        # (the server always emits ready first when requested) but not
        # fatal -- ignore it and keep waiting; the long reconcile will catch
        # anything this drops.


class _Snapshot:
    """Owns the raw-tasks/relay-cache state a single relay connection reads
    and refreshes. Not shared across reconnects -- each reconnect gets a
    fresh one, since a replacement connection's first reconcile is itself a
    full re-fetch."""

    def __init__(self, args):
        self._args = args
        self.raw_tasks: list[dict] = []
        self.relay_cache: dict = {}

    def full_refetch(self) -> list[dict] | None:
        """One full network re-fetch + relay lookup, refreshing the cached
        raw tasks/relay entries :meth:`recompute_only` reads. Returns the
        new rows, or ``None`` on a transient failure (caller keeps the
        previous snapshot and tries again later)."""
        from . import board_cli

        try:
            tasks = board_cli._fetch_raw_tasks_direct(self._args)
        except Exception:
            return None
        cache: dict = {}

        def _tracking_fetch_many(refs):
            fetched = board_cli._relay_fetch_many(refs)
            cache.update(fetched)
            return fetched

        try:
            rows = board_cli._build(
                tasks,
                machine=self._args.machine,
                recent_mins=self._args.recent_mins,
                relay_fetch_many=_tracking_fetch_many,
            )
        except Exception:
            return None
        self.raw_tasks = tasks
        self.relay_cache = cache
        return rows

    def recompute_only(self) -> list[dict]:
        """Zero-network: rebuild from the already-cached raw tasks + relay
        entries, recomputing every clock-only transition (``_build()``'s
        ``now = time.time()`` does this for free)."""
        from . import board_cli

        cache = self.relay_cache

        def _cached_fetch_many(refs):
            return {ref: cache.get(ref) for ref in refs}

        return board_cli._build(
            self.raw_tasks,
            machine=self._args.machine,
            recent_mins=self._args.recent_mins,
            relay_fetch_many=_cached_fetch_many,
        )


def _emit_diff(out, prev: list[dict], curr: list[dict]) -> tuple[bool, list[dict]]:
    """Diff ``curr`` against ``prev`` and emit delta/removed frames. Returns
    ``(ok, new_prev)`` -- ``ok`` is False once the reader has closed the
    pipe, matching every other emit path's contract."""
    from . import board_cli

    deltas, removed = board_cli._diff_rows(prev, curr)
    for entry in deltas:
        if not board_cli._emit_frame({"type": "delta", "entry": entry}, out):
            return False, prev
    for rid in removed:
        if not board_cli._emit_frame({"type": "removed", "id": rid}, out):
            return False, prev
    return True, curr


class _Connected:
    """A live, ready relay connection -- the state :func:`_event_loop` needs
    to keep driving it, and what :func:`_reconnect_loop` hands back on a
    successful reconnect so the top-level driver can resume the event loop
    without recursing into it."""

    __slots__ = ("client", "prev", "reader", "snapshot")

    def __init__(self, client, reader, snapshot, prev):
        self.client = client
        self.reader = reader
        self.snapshot = snapshot
        self.prev = prev


class _Disconnected:
    """A connection attempt or a live connection dropped -- hand ``prev`` to
    the reconnect loop without recursing into it."""

    __slots__ = ("prev",)

    def __init__(self, prev):
        self.prev = prev


def _connect(
    args, out, prev: list[dict], *, allow_relay_unavailable: bool
) -> _Connected | _Disconnected | int:
    """Establish one fresh relay connection: build a client, confirm
    ready-frame support, wait for the ready frame, then run the startup
    reconcile and diff it against ``prev`` *after* the subscription is
    confirmed live -- never before, and never silently substituted as the
    new baseline without emitting the diff. Returns a :class:`_Connected` on
    success, a :class:`_Disconnected` to hand to :func:`_reconnect_loop`, or
    a plain ``int`` to return immediately (the reader closed the pipe
    mid-reconcile).

    ``allow_relay_unavailable`` gates whether "daemon doesn't advertise
    support" raises :class:`RelayUnavailable` (only correct for the very
    first connection attempt, before any frame has been emitted) or is
    folded into an ordinary :class:`_Disconnected` (every reconnect attempt
    after that, where frames have already gone out and the caller's own
    permanent-poll-loop-with-the-original-snapshot contract no longer
    applies).
    """
    try:
        client = _build_client(args)
    except Exception:
        return _Disconnected(prev)

    try:
        supported = _daemon_supports_ready_frame(client)
    except HealthCheckFailed:
        client.close()
        return _Disconnected(prev)
    if not supported:
        client.close()
        if allow_relay_unavailable:
            raise RelayUnavailable("daemon does not advertise events_ready_frame")
        return _Disconnected(prev)

    reader = _Reader(client)
    reader.start()
    # Wait for the ready frame FIRST -- before any reconcile fetch runs --
    # so a mutation landing between "subscription registered" and "our own
    # reconcile fetch" is guaranteed to still arrive as a queued event
    # afterward, rather than being silently missed by both. Only once that
    # barrier is confirmed does the startup reconcile fetch run.
    if not _wait_for_ready(reader, READY_FRAME_TIMEOUT_SECONDS):
        client.close()
        return _Disconnected(prev)

    snapshot = _Snapshot(args)
    reconciled = snapshot.full_refetch()
    if reconciled is None:
        # Priming failed -- treat as transient (never substitute an empty
        # snapshot, which would make the next recompute tick emit every
        # row in `prev` as `removed`).
        client.close()
        return _Disconnected(prev)

    ok, new_prev = _emit_diff(out, prev, reconciled)
    if not ok:
        client.close()
        return 0
    return _Connected(client, reader, snapshot, new_prev)


def run_relay(args, out, *, initial_rows: list, interval: float) -> int:
    """Run Phase 3a's event-woken relay for the direct (local) path.

    ``args``/``out``/``initial_rows`` mirror ``board_cli._run_stream``'s own
    locals (the caller has already emitted the initial ``begin``/``row``/
    ``done`` envelope using ``initial_rows``). Returns 0 the same way
    ``board_cli.poll_loop`` does (clean ``KeyboardInterrupt`` or a closed
    pipe); raises :class:`RelayUnavailable` instead of returning if the
    daemon never advertised ready-frame support in the first place, so the
    caller can fall back to its own unmodified poll loop for this
    connection's entire remaining lifetime. Every other failure (a
    transient ``/health`` blip, the coordinator endpoint being briefly
    unresolvable, the ready frame not arriving in time) routes through the
    bounded reconnect path instead of raising or crashing.
    """
    outcome = _connect(args, out, initial_rows, allow_relay_unavailable=True)
    return _drive(args, out, outcome, interval)


def _drive(
    args, out, outcome: _Connected | _Disconnected | int, interval: float
) -> int:
    """Iteratively alternate between the event loop and the reconnect loop
    for as long as the channel keeps reconnecting, via a plain ``while``
    loop -- never by one of those two calling the other, which (even split
    across two functions) is still unbounded recursion in Python (no tail-
    call optimization): a long-lived channel that reconnects many times
    over its life must never grow the call stack. Always closes the client
    being superseded before moving on."""
    while True:
        if isinstance(outcome, int):
            return outcome
        if isinstance(outcome, _Disconnected):
            outcome = _reconnect_loop(args, out, outcome.prev, interval)
            continue
        # outcome is a _Connected
        client = outcome.client
        outcome = _event_loop(
            args, out, outcome.reader, outcome.snapshot, outcome.prev, interval
        )
        client.close()


def _event_loop(
    args, out, reader: _Reader, snapshot: _Snapshot, prev: list[dict], interval: float
) -> int | _Disconnected:
    """The single control loop driving every writer for one live SSE
    connection: event-woken re-fetches (debounced + rate-limited), the long
    reconcile, and the local recompute tick. Returns ``0`` on a clean exit,
    or a :class:`_Disconnected` the moment the connection drops (never
    calls the reconnect loop directly -- see :func:`_drive`)."""
    last_fetch_at = time.monotonic()
    next_recompute_at = last_fetch_at + RECOMPUTE_INTERVAL_SECONDS
    next_reconcile_at = last_fetch_at + LONG_RECONCILE_SECONDS
    refetch_floor = max(MIN_REFETCH_INTERVAL_SECONDS, interval)
    try:
        while True:
            now = time.monotonic()
            timeout = max(0.0, min(next_recompute_at, next_reconcile_at) - now)
            try:
                kind, _payload = reader.queue.get(timeout=timeout)
            except queue.Empty:
                kind = "timer"

            if kind == "disconnected":
                return _Disconnected(prev)

            if kind == "ready":
                continue  # a second ready frame would be a protocol bug; ignore

            if kind == "event":
                # Debounce: coalesce any further events already queued (or
                # arriving within the debounce window) into this same wake.
                # An event arriving mid-fetch (not observable here, since
                # this loop is single-threaded) is instead simply left on
                # the queue and picked up next iteration -- the "trailing
                # fetch" the design document describes, by construction.
                deadline = time.monotonic() + DEBOUNCE_WINDOW_SECONDS
                disconnected_during_debounce = False
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    try:
                        kind2, _payload2 = reader.queue.get(timeout=remaining)
                    except queue.Empty:
                        break
                    if kind2 == "disconnected":
                        disconnected_during_debounce = True
                        break
                    # another coalesced "event"/stray "ready": nothing to do,
                    # the upcoming full re-fetch already covers it
                if disconnected_during_debounce:
                    return _Disconnected(prev)

                wait_for = refetch_floor - (time.monotonic() - last_fetch_at)
                if wait_for > 0:
                    time.sleep(wait_for)
                curr = snapshot.full_refetch()
                last_fetch_at = time.monotonic()
                if curr is not None:
                    ok, prev = _emit_diff(out, prev, curr)
                    if not ok:
                        return 0
                continue

            # kind == "timer"
            if now >= next_recompute_at:
                curr = snapshot.recompute_only()
                ok, prev = _emit_diff(out, prev, curr)
                if not ok:
                    return 0
                next_recompute_at = now + RECOMPUTE_INTERVAL_SECONDS
            if now >= next_reconcile_at:
                curr = snapshot.full_refetch()
                last_fetch_at = time.monotonic()
                if curr is not None:
                    ok, prev = _emit_diff(out, prev, curr)
                    if not ok:
                        return 0
                next_reconcile_at = time.monotonic() + LONG_RECONCILE_SECONDS
    except KeyboardInterrupt:
        return 0


def _reconnect_loop(args, out, prev: list[dict], interval: float) -> int | _Connected:
    """On an SSE disconnect: fall back to poll-and-diff immediately
    (correctness first), while retrying the relay connection with bounded
    exponential backoff. Each attempt re-resolves the endpoint and builds a
    fresh client (see :func:`_build_client`) rather than retrying a stale
    one. The fallback poll tick keeps running on its own ``interval``
    cadence throughout the *entire* backoff wait (never just once per
    attempt) -- a configured 2s ``--subscribe`` must not silently degrade to
    an 8s/16s/30s refresh just because reconnection is struggling. Once
    reconnected, a full reconcile pass (a fresh :class:`_Snapshot`) becomes
    ``prev``, returned as a :class:`_Connected` for the top-level driver to
    resume the event loop with -- never recurses into it directly.
    Exhausting the bounded retry cap settles into permanent polling for the
    rest of this process's life."""
    from . import board_cli

    def _poll_tick(current_prev: list[dict]) -> tuple[bool, list[dict]]:
        try:
            curr = board_cli._fetch_rows(args)
        except Exception:
            # A transient re-fetch failure must not kill the fallback
            # channel -- skip this tick and try again next time.
            return True, current_prev
        return _emit_diff(out, current_prev, curr)

    backoff = INITIAL_RECONNECT_BACKOFF_SECONDS
    for _attempt in range(MAX_RECONNECT_ATTEMPTS):
        # Correctness first: an immediate poll tick before each reconnect
        # attempt.
        ok, prev = _poll_tick(prev)
        if not ok:
            return 0

        # Wait out this attempt's backoff, but keep polling on --interval's
        # own cadence throughout rather than blocking for the whole backoff
        # window in one sleep.
        deadline = time.monotonic() + backoff
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(interval, remaining))
            if time.monotonic() >= deadline:
                break
            ok, prev = _poll_tick(prev)
            if not ok:
                return 0
        backoff = min(MAX_RECONNECT_BACKOFF_SECONDS, backoff * 2)

        # A daemon that stops advertising support mid-reconnect is just
        # another connection failure here, not RelayUnavailable -- frames
        # have already been emitted, so the caller's own
        # fall-back-to-its-stale-initial-snapshot contract no longer
        # applies (see `_connect`'s own docstring).
        outcome = _connect(args, out, prev, allow_relay_unavailable=False)
        if isinstance(outcome, _Connected):
            return outcome
        if isinstance(outcome, int):
            return outcome
        prev = outcome.prev

    # Retries exhausted: settle into permanent polling for the rest of this
    # process's life, reusing the exact same poll-and-diff implementation.
    return board_cli.poll_loop(args, out, prev, interval)

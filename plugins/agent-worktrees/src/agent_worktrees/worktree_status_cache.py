"""Durable, SQLite-backed status cache for the worktree-status accelerator.

Per-worktree bundles (see :mod:`worktree_status_daemon`) are read from an
**in-memory dict** -- the true, current authority, never blocked on disk I/O
on the request path -- and best-effort persisted to a single WAL-mode SQLite
file so a restarted daemon warm-restores instead of starting cold. This is
deliberately the same durable-store technology agent-dispatch already uses
(single-writer, WAL-mode SQLite) rather than ``list_cache.py``'s file-
sidecar-per-args-shape pattern, per the operator's explicit steer.

Freshness model (see the effort README's Phase 1 design for the full
rationale):

- A read for a worktree already in the in-memory cache and not yet past its
  TTL returns instantly, no recompute.
- A read past its TTL, or never before demanded (cold), or carrying
  ``force=True`` recomputes via the caller-supplied ``compute`` callback,
  then write-through updates both the in-memory dict and the SQLite row.
- Every read registers **demand** (a `demanded_at` timestamp) so a
  background sweep (:meth:`WorktreeStatusCache.sweep_due`) knows which
  worktrees are worth proactively refreshing -- mirroring
  ``list_cache.py``'s own demand-registration concept, applied here to an
  in-memory store instead of file sidecars.

Never raises past :meth:`WorktreeStatusCache.get_or_refresh`: any SQLite
failure (corruption, permissions, disk full) degrades to in-memory-only
operation -- durability is a nice-to-have across restarts, never a
requirement for the cache to function this session.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path

#: How long a cached bundle is trusted before an ordinary (non-forced) read
#: triggers a recompute. Originally set assuming a cheap ~5 git-call bundle;
#: live measurement (2026-09-22, machine CPU-saturation investigation) found
#: the daemon's status-monitor process sustaining ~83-99% CPU continuously
#: for hours with as few as 7 demanded worktrees. Root cause: a real
#: compute() call costs ~5-6s (the unavoidable `git fetch` in
#: `classify_worktree`, not a cheap local read), so the prior 20s TTL asked
#: the single-threaded sweep loop to refresh every demanded worktree
#: roughly every 20-30s regardless of that ~5-6s real cost -- with N
#: demanded worktrees the sweep needs N * ~6s of CPU-bound work per TTL
#: window, which already exceeds one CPU core's capacity at N >= ~4,
#: guaranteeing the sweep thread runs almost continuously back-to-back
#: rather than mostly idling between ticks. An initial raise to 90s (live-
#: verified) only brought steady-state utilization down to ~60% for a
#: real N=7 -- the theoretical floor (N * ~6s / TTL) at that TTL is still
#: ~47%, too high for a background accelerator. Raised further to 180s
#: (theoretical floor ~23% for the same N=7), live-verified over a 10+
#: minute settled window. Safe to raise on its own: `worktree_status_audit
#: .py`'s own freshness bound is computed as `DEFAULT_TTL_SECONDS +
#: SWEEP_INTERVAL_SECONDS + FRESHNESS_SLACK_SECONDS` (see that module), so
#: it moves with this constant rather than being a fixed ceiling this
#: change could violate.
DEFAULT_TTL_SECONDS = 180.0

#: A demanded worktree not read again within this window is no longer swept
#: -- an operator who closed the card / moved on stops paying the sweep cost
#: for it. Generous relative to a single render session.
DEMAND_TTL_SECONDS = 300.0

#: Defense-in-depth cap on how many entries one `sweep_due` call will
#: recompute, regardless of how many are due. Even after raising
#: DEFAULT_TTL_SECONDS to reflect real compute cost, an operator whose
#: demanded-worktree count grows well past what one CPU core can refresh
#: within a TTL window would otherwise reproduce the same continuous-CPU
#: symptom at a higher N. Capping bounds worst-case sweep cost to
#: `max_refresh_per_sweep * (per-entry compute cost)` every sweep tick,
#: independent of N -- excess due entries are simply picked up on a later
#: tick (prioritizing the stalest first, see `sweep_due`), trading a wider
#: worst-case staleness for a hard CPU ceiling rather than the reverse.
DEFAULT_MAX_REFRESH_PER_SWEEP = 4

_SCHEMA = """
CREATE TABLE IF NOT EXISTS worktree_status_cache (
    project TEXT NOT NULL,
    worktree_id TEXT NOT NULL,
    bundle_json TEXT NOT NULL,
    computed_at REAL NOT NULL,
    demanded_at REAL NOT NULL,
    PRIMARY KEY (project, worktree_id)
);
"""


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # `check_same_thread=False`: the connection is created once (daemon boot
    # or first construction) but read/written from whichever thread handles
    # a given request (`CoalescingServer`'s per-request thread) or the
    # background sweep thread. Safe here because every access to `self._conn`
    # already goes through `WorktreeStatusCache`'s own single `self._lock`
    # (see `get_or_refresh`/`sweep_due`) -- sqlite3's own same-thread check
    # would otherwise raise on the very first cross-thread write.
    conn = sqlite3.connect(
        str(db_path), timeout=5, isolation_level=None, check_same_thread=False
    )
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error:
        pass
    conn.execute("PRAGMA busy_timeout=2000")
    conn.execute(_SCHEMA)
    return conn


class WorktreeStatusCache:
    """One process's in-memory + SQLite-durable worktree-status cache.

    Thread-safe: guarded by a single lock, since a request's own compute
    (~5 git calls, a lineage read, a liveness probe) already dominates any
    lock-hold cost -- no benefit to finer-grained locking for this shape.
    """

    def __init__(
        self,
        db_path: Path,
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        demand_ttl_seconds: float = DEMAND_TTL_SECONDS,
        max_refresh_per_sweep: int = DEFAULT_MAX_REFRESH_PER_SWEEP,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._db_path = db_path
        self._ttl = ttl_seconds
        self._demand_ttl = demand_ttl_seconds
        self._max_refresh_per_sweep = max_refresh_per_sweep
        self._now = now
        self._lock = threading.Lock()
        # key -> (bundle, computed_at, demanded_at)
        self._entries: dict[tuple[str, str], tuple[dict, float, float]] = {}
        # key -> last sweep-attempt timestamp (success OR failure). Separate
        # from `computed_at` (which only advances on success) so a
        # persistently-failing entry still rotates out of "stalest first"
        # priority after being attempted, instead of monopolizing every
        # `max_refresh_per_sweep` slot forever (Copilot review, PR #3348).
        self._last_attempted: dict[tuple[str, str], float] = {}
        self._conn: sqlite3.Connection | None = None
        # Set by `close()`. Checked by `_open()` so a request-handler thread
        # still in flight when the monitor shuts down (`CoalescingServer
        # .close()` stops its own accept/reaper threads but does not wait
        # for an already-dispatched request handler) can never reopen or
        # write through a connection this cache has already torn down --
        # closing races a successor monitor's own SQLite writer otherwise.
        self._closed = False
        self._warm_restore()

    # -- durability --------------------------------------------------------

    def _open(self) -> sqlite3.Connection | None:
        if self._closed:
            return None
        if self._conn is not None:
            return self._conn
        try:
            self._conn = _connect(self._db_path)
        except (sqlite3.Error, OSError):
            # OSError covers `Path.mkdir` failing (a read-only or otherwise
            # unavailable runtime home) -- `_connect` itself can raise this
            # before ever reaching sqlite3, not just a `sqlite3.Error`.
            self._conn = None
        return self._conn

    def _warm_restore(self) -> None:
        """Load every durable row into memory on construction (daemon boot).

        Best-effort: any failure leaves the in-memory cache empty (every
        worktree computes cold on first demand this session) rather than
        raising -- durability across a restart is a nice-to-have, never a
        requirement for a fresh cache to function.

        A row whose demand has already aged out (``DEMAND_TTL_SECONDS``) by
        restart time is pruned here rather than rehydrated -- otherwise the
        demand TTL would bound nothing: every worktree ever viewed keeps a
        durable row forever, and every restart pays to reload all of them.
        """
        conn = self._open()
        if conn is None:
            return
        try:
            rows = conn.execute(
                "SELECT project, worktree_id, bundle_json, computed_at, demanded_at"
                " FROM worktree_status_cache"
            ).fetchall()
        except sqlite3.Error:
            return
        now = self._now()
        for row in rows:
            if now - row["demanded_at"] > self._demand_ttl:
                self._delete_row(
                    row["project"], row["worktree_id"], if_demanded_at=row["demanded_at"]
                )
                continue
            try:
                bundle = json.loads(row["bundle_json"])
            except (ValueError, TypeError):
                continue
            # A corrupt/older row could parse to a non-dict JSON value (a
            # list, a scalar) -- treating that as a real bundle would let a
            # bad durable row silently suppress recomputation forever (the
            # server converts a non-dict compute result to `{}`, so a
            # caller would never see a real answer, nor would this ever
            # self-heal since the fresh-looking entry blocks a refresh).
            # Skip it -- the entry simply stays cold, a normal compute path.
            if not isinstance(bundle, dict):
                continue
            key = (row["project"], row["worktree_id"])
            self._entries[key] = (bundle, row["computed_at"], row["demanded_at"])

    def _delete_row(
        self, project: str, worktree_id: str, *, if_demanded_at: float | None = None
    ) -> None:
        """Remove a durable row entirely (a demand-expired or corrupt entry).

        ``if_demanded_at``, when given, conditions the delete on the row's
        ``demanded_at`` still matching exactly what was sampled before
        deciding to prune -- a concurrent write between that sample and
        this call (e.g. a fresh request re-demanding and re-persisting the
        same key) would have advanced `demanded_at`, and an unconditional
        delete would otherwise remove that newer row instead of the stale
        one this call actually means to prune. Best-effort, mirrors
        ``_persist``'s own degrade-on-failure contract.
        """
        conn = self._open()
        if conn is None:
            return
        try:
            if if_demanded_at is None:
                conn.execute(
                    "DELETE FROM worktree_status_cache"
                    " WHERE project = ? AND worktree_id = ?",
                    (project, worktree_id),
                )
            else:
                conn.execute(
                    "DELETE FROM worktree_status_cache"
                    " WHERE project = ? AND worktree_id = ? AND demanded_at = ?",
                    (project, worktree_id, if_demanded_at),
                )
        except sqlite3.Error:
            pass

    def _persist(self, project: str, worktree_id: str, bundle: dict, computed_at: float, demanded_at: float) -> None:
        conn = self._open()
        if conn is None:
            return
        try:
            conn.execute(
                "INSERT INTO worktree_status_cache"
                " (project, worktree_id, bundle_json, computed_at, demanded_at)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(project, worktree_id) DO UPDATE SET"
                " bundle_json=excluded.bundle_json,"
                " computed_at=excluded.computed_at,"
                " demanded_at=excluded.demanded_at"
                # Cross-process monotonic guard: during a status-monitor
                # cutover, an outgoing monitor's still-in-flight handler
                # (CoalescingServer.close() does not wait for one -- see the
                # `_closed` guard above, which only protects THIS process'
                # own connection, not a separate successor process opening
                # the same durable file) could otherwise persist a stale
                # write after the successor already wrote a newer one. Never
                # let an older `computed_at` overwrite a row that already
                # reflects a more recent computation, regardless of which
                # process/generation performed either write.
                " WHERE excluded.computed_at > worktree_status_cache.computed_at",
                (project, worktree_id, json.dumps(bundle), computed_at, demanded_at),
            )
        except sqlite3.Error:
            # Durability is best-effort; the in-memory entry above is already
            # updated regardless, so this session's reads stay correct.
            pass

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._conn is not None:
                try:
                    self._conn.close()
                except sqlite3.Error:
                    pass
                self._conn = None

    def has_active_demand(self) -> bool:
        """True while at least one worktree is currently demanded (i.e. this
        cache has answered a request recently enough that the sweep is still
        keeping it warm). Lets a resident owner (the status-monitor's own
        idle-exit decision) count a direct worktree-status consumer as a
        reason to stay alive, the same way it already counts a live mux
        session or Picker/list-cache demand -- otherwise a consumer that
        only ever asks this cache (never touching those other signals)
        would have the monitor tear itself, and this cache's own background
        refresh, down out from under it."""
        with self._lock:
            return bool(self._entries)

    # -- reads / refresh -----------------------------------------------------

    def get_or_refresh(
        self,
        project: str,
        worktree_id: str,
        *,
        force: bool,
        compute: Callable[[], dict],
    ) -> dict:
        """Return a fresh-enough bundle for ``(project, worktree_id)``.

        Registers demand on every call (fresh or not) so the sweep keeps this
        entry warm going forward. ``compute`` runs only when the cached entry
        is absent, past its TTL, or ``force`` is set -- never on an ordinary
        fresh-cache hit.

        A fresh-cache hit updates the in-memory ``demanded_at`` stamp only --
        it deliberately does **not** synchronously write through to SQLite
        (durability of the demand timestamp alone isn't worth blocking an
        otherwise memory-only read on the SQLite busy-timeout, up to 2s,
        which would otherwise serialize independent worktrees' requests
        behind one slow disk write). The bundle itself is only persisted
        when it's actually recomputed (below), which is the value durability
        exists for in the first place.
        """
        key = (project, worktree_id)
        now = self._now()
        with self._lock:
            entry = self._entries.get(key)
            fresh = entry is not None and (now - entry[1]) < self._ttl
            if fresh and not force:
                bundle, computed_at, demanded_at = entry
                # Defense in depth against a stale/tampered entry that
                # somehow predates a compute()-side identity guard (a
                # warm-restored SQLite row is never re-validated on load --
                # see `_warm_restore` -- and an older compute() build could
                # have persisted a bundle before this check existed): a
                # fresh-cache hit must still describe the exact key it's
                # keyed under, or this cache would keep serving the wrong
                # worktree's facts under this key indefinitely, never
                # recomputing until the next TTL expiry/force-refresh
                # happens to coincide with the corruption being noticed.
                # A bundle shape without these keys at all (this cache is
                # generic -- see its own tests) is never treated as a
                # mismatch, only one that actively disagrees.
                bundle_project = bundle.get("project")
                bundle_worktree_id = bundle.get("worktree_id")
                identity_ok = (
                    bundle_project is None or bundle_project == project
                ) and (bundle_worktree_id is None or bundle_worktree_id == worktree_id)
                if identity_ok:
                    self._entries[key] = (bundle, computed_at, now)
                    return bundle
                self._entries.pop(key, None)
                self._last_attempted.pop(key, None)
                # Delete the durable row while still holding the lock --
                # this is an infrequent, exceptional path (not the hot
                # cache-hit path this cache otherwise deliberately avoids
                # blocking on SQLite for), same as `sweep_due`'s own
                # identical eviction. Conditioned on the exact `demanded_at`
                # just evicted: a concurrent recompute (the sweep, or a
                # successor monitor mid-cutover) could publish a fresh,
                # correct row for this same key between the pop above and
                # this delete if it ran unsynchronized -- an unconditional,
                # out-of-lock delete would remove that newer row instead of
                # the stale one this call actually means to prune.
                self._delete_row(project, worktree_id, if_demanded_at=demanded_at)
        # Compute outside the lock: a single-worktree bundle is real work
        # (~5 git calls) and must never hold the cache lock for its duration
        # -- callers for OTHER worktrees must not wait on it. Concurrent
        # callers for the SAME worktree are protected instead by
        # `CoalescingServer`'s own per-(kind, key) coalescing one layer up
        # (see `worktree_status_daemon.py`), not by this cache.
        bundle = compute()
        # Stamp `computed_at`/`demanded_at` from completion time, not the
        # `now` sampled before `compute()` ran -- a slow git refresh can
        # exceed the TTL itself (each classification subprocess is allowed
        # several seconds), so stamping from the pre-compute time could
        # store a result already expired, immediately triggering another
        # refresh on the very next read.
        completed_at = self._now()
        with self._lock:
            self._entries[key] = (bundle, completed_at, completed_at)
            self._persist(project, worktree_id, bundle, completed_at, completed_at)
        return bundle

    def sweep_due(self, *, refresh: Callable[[str, str], dict]) -> int:
        """Refresh up to ``max_refresh_per_sweep`` demanded, TTL-expired
        entries (stalest-attempted first); drop entries whose demand has
        aged out (``DEMAND_TTL_SECONDS``). Returns the count refreshed.
        Never raises: a per-entry refresh failure is skipped, leaving that
        entry's last-known value in place rather than losing it.

        The cap is defense-in-depth against the exact CPU-saturation bug
        found live (2026-09-22): a real per-entry recompute costs several
        seconds (an unavoidable git fetch), so refreshing an unbounded
        number of due entries every sweep tick can demand more CPU than one
        core provides, leaving the sweep thread running back-to-back with
        no idle gaps. Prioritizing the stalest-attempted entries first means
        an operator with more demanded worktrees than the cap can serve per
        tick sees some entries age further past their nominal TTL rather
        than the daemon's CPU cost growing unbounded with demand -- a
        graceful, self-limiting degradation instead of the reverse.

        Priority is by ``_last_attempted`` (updated on every attempt,
        success OR failure), not bare ``computed_at`` (Copilot review,
        PR #3348): ``computed_at`` only advances on a successful refresh, so
        a persistently-failing entry would otherwise remain "the stalest"
        forever and monopolize every cap slot, starving every other due
        entry from ever being attempted. Recording the attempt time
        regardless of outcome rotates a failing entry to the back of the
        queue exactly like a succeeding one, so demand beyond capacity is
        served round-robin across attempts rather than jamming on one
        chronically-broken worktree.

        Each entry is sampled, then recomputed **outside** the lock (the same
        reason ``get_or_refresh`` computes outside it) -- so a concurrent
        request-driven refresh (an ordinary miss, or an explicit force) for
        the SAME key can complete first. Publishing this sweep's own result
        unconditionally in that case would silently overwrite the newer
        value with a stale one for a full TTL window. Guarded by checking
        the entry's ``computed_at`` is still exactly what was sampled before
        writing -- if it advanced, something else already refreshed this key
        more recently, so this sweep's answer is simply discarded here
        (never written to memory or SQLite).
        """
        now = self._now()
        with self._lock:
            sampled = {
                key: computed_at
                for key, (_, computed_at, demanded_at) in self._entries.items()
                if now - demanded_at <= self._demand_ttl and now - computed_at >= self._ttl
            }
            expired = [
                key
                for key, (_, _computed_at, demanded_at) in self._entries.items()
                if now - demanded_at > self._demand_ttl
            ]
            last_attempted_snapshot = dict(self._last_attempted)
        if len(sampled) > self._max_refresh_per_sweep:
            # Stalest-ATTEMPTED (smallest last_attempted, falling back to
            # computed_at for an entry never yet attempted by a sweep) first
            # -- see the cap's own rationale in the docstring above.
            stalest = sorted(
                sampled.items(),
                key=lambda item: last_attempted_snapshot.get(item[0], item[1]),
            )
            sampled = dict(stalest[: self._max_refresh_per_sweep])
        for project, worktree_id in expired:
            with self._lock:
                # Re-check at pop time, not just at sampling time: a
                # concurrent request between the sample above and this pop
                # could have refreshed (and re-demanded) this exact entry,
                # extending its life -- an unconditional pop here would
                # delete that newly refreshed entry and its demand
                # registration right out from under it.
                current = self._entries.get((project, worktree_id))
                if current is None or self._now() - current[2] <= self._demand_ttl:
                    continue
                stale_demanded_at = current[2]
                self._entries.pop((project, worktree_id), None)
                self._last_attempted.pop((project, worktree_id), None)
                # Delete the durable row while still holding the lock (this
                # is an infrequent prune, not the hot read path
                # `get_or_refresh` deliberately avoids blocking on SQLite
                # for), conditioned on the exact `demanded_at` just
                # confirmed -- otherwise a concurrent re-persist landing
                # between this pop and an out-of-lock delete could have its
                # brand-new durable row removed by a delete meant for the
                # stale one.
                self._delete_row(project, worktree_id, if_demanded_at=stale_demanded_at)
        refreshed = 0
        for (project, worktree_id), sampled_computed_at in sampled.items():
            try:
                bundle = refresh(project, worktree_id)
            except Exception:
                # Record the attempt even on failure -- see the docstring's
                # `_last_attempted` rationale: this is what lets a
                # persistently-failing entry rotate out of "stalest first"
                # rather than monopolizing every cap slot forever.
                with self._lock:
                    self._last_attempted[(project, worktree_id)] = self._now()
                continue
            ts = self._now()
            with self._lock:
                self._last_attempted[(project, worktree_id)] = ts
                existing = self._entries.get((project, worktree_id))
                if existing is not None and existing[1] != sampled_computed_at:
                    # Someone else (a request-driven refresh) already
                    # produced a newer answer while this sweep's own
                    # recompute was in flight -- discard this stale result
                    # rather than clobber it.
                    continue
                demanded_at = existing[2] if existing is not None else ts
                self._entries[(project, worktree_id)] = (bundle, ts, demanded_at)
                self._persist(project, worktree_id, bundle, ts, demanded_at)
            refreshed += 1
        return refreshed

"""Resident worktree-status accelerator (agent-worktrees-external-status-
accelerator effort).

Thin wrappers around the vendored ``work_coalescing_singleton`` library,
scoped to a **per-worktree status bundle** request -- the second concrete
instance of the ``work-coalescing-singleton`` pattern after `#2323`'s
classify/list accelerator (:mod:`classify_daemon`), which this module
deliberately mirrors structurally. Where that daemon coalesces a per-project
*batch* classification pass, this one coalesces a per-**worktree** status
read: a Tasks-board card (or any other external-status consumer, per the
vision's ``external-status-consumer-contract``) asks about exactly one
worktree at a time, so batching unrelated worktrees into one compute call
would make one caller wait on -- or receive -- another worktree's answer.

**Wired into `cmd_status_monitor`.** This module owns the wire layer (start
the daemon side, make one coalesced client request) via
:class:`InProcessRuntime`; the actual git/session/liveness/claims assembly
lives in :mod:`worktree_status_compute` (kept out of ``__main__.py`` to stay
under its shrink-only size baseline). Unlike `#2323`'s classify daemon, a request here also
passes through :mod:`worktree_status_cache` (see :func:`build_cached_compute`)
-- `CoalescingServer` alone only deduplicates truly concurrent requests, so
without that cache layer every call would recompute from scratch and
"force-refresh" would have nothing meaningful to bypass. There is no
direct-compute *lease-guarded* fallback the way ``_classify_records`` has one
for classify -- a miss here degrades to the in-process reference consumer
computing the bundle itself, uncoalesced and cache-free (see the effort's
Phase 4), which is already cheap enough (one worktree, not a fleet) to run
inline.
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from pathlib import Path

from work_coalescing_singleton import CoalescingServer
from work_coalescing_singleton import client as wcs_client

from .worktree_status_cache import WorktreeStatusCache

#: Mirrors `lineage_surfaces._safe_identity_token` exactly (a single path
#: component, no traversal) -- applied to both `project` and `worktree_id`
#: here since both are used to build filesystem paths in
#: `_worktree_status_compute`. A lone ``"."`` must be rejected explicitly,
#: not just ``".."`` and separators: `cfg.project_dir(".")` builds
#: ``f".{project}"`` = ``".."``, so ``project="."`` alone already escapes
#: the intended directory even though it contains no ``/``/``\\``/``..`` of
#: its own. A NUL byte must be rejected too -- `Path.exists()`/`open()`
#: raise a `ValueError` on an embedded NUL rather than the documented
#: fallback/JSON error, mirroring `lineage_surfaces._safe_identity_token`.
_UNSAFE_TOKEN = re.compile(r"[/\\]|\.\.|\x00")
_UNSAFE_EXACT = {".", ".."}


def _is_safe_identity_token(value: str) -> bool:
    return bool(value) and value not in _UNSAFE_EXACT and not _UNSAFE_TOKEN.search(value)


def coalescing_key(project: str, worktree_id: str) -> str:
    """Build an injective ``CoalescingServer`` dedup key for one
    ``(project, worktree_id)`` request.

    A plain ``f"{project}|{worktree_id}"`` is not injective: neither
    component is restricted to reject ``|`` (unlike ``/``, ``\\``, ``..``,
    and NUL, which matter for the *filesystem* path each is later used to
    build), so ``("a", "b|c")`` and ``("a|b", "c")`` would coalesce onto the
    identical key -- letting two different worktrees' concurrent requests
    join the same in-flight computation and each receive the *other's*
    bundle. Length-prefixing ``project`` makes the split point unambiguous
    regardless of either component's own content, with no new character
    restriction needed.
    """
    return f"{len(project)}:{project}:{worktree_id}"


#: A single-worktree status bundle (~5 git calls + a lineage read + a live
#: mux/lock probe + a claims read) is comparable in cost to a *single*
#: worktree's slice of the classify daemon's fleet pass -- fast, but real
#: subprocess/filesystem work, not free. Kept short since a render/click
#: caller has its own bounded wait and must never stall behind a boot.
BOOT_WAIT_S = 4.0
#: Live-measured (2026-09-22, `worktree-status-audit` investigation): a real
#: bundle's dominant cost is `worktree_status_compute.compute`'s own
#: `fetch=True` git classify call, ~3-5s depending on remote/host load, for a
#: total compute time of ~5.4s after fixing the unrelated
#: `_control_plane_related_pr_map` tax (see that module's history). The prior
#: 3.0s value was tighter than the real compute path could ever meet even in
#: the healthy case, so every on-demand daemon request reliably missed this
#: deadline and fell back to the (now equally slow) uncoalesced path --
#: defeating the coalescing/caching this daemon exists for. Set with headroom
#: above the observed worst case, mirroring `classify_daemon.REQUEST_DEADLINE_S`
#: (5.0s for a whole-fleet batch pass) scaled up for this call's own slower
#: single-fetch cost plus request/response wire overhead.
REQUEST_DEADLINE_S = 8.0
KIND = "worktree_status"

#: Shorter than classify's linger/TTL: a status-bundle caller is typically a
#: one-shot render/click, not a batch pass, so there is less benefit to
#: keeping the daemon warm across a long idle gap -- but still generous
#: relative to a single request's own lifetime.
LINGER_SECONDS = 8.0
SUBSCRIBER_TTL_SECONDS = 30.0


def validated_refresh(assemble: Callable[[str, str], dict]) -> Callable[[str, str], dict]:
    """Wrap a raw ``assemble(project, worktree_id) -> dict`` so every call
    validates both identities against path-traversal first, raising
    ``ValueError`` instead of ever reaching ``assemble``.

    Shared by both the request path (:func:`build_cached_compute`, whose
    ``payload`` came from a network caller) and the background sweep (whose
    keys instead come from :meth:`WorktreeStatusCache.sweep_due`'s own
    in-memory/warm-restored entries) -- a warm-restored SQLite row is
    **not** re-validated on load (see ``worktree_status_cache
    ._warm_restore``, which only checks the row parses as a JSON object,
    not that its key is a safe identity), so a corrupt or tampered durable
    row could otherwise let the sweep bypass the request path's own check
    and read outside the selected project's tracking directory.
    """

    def _refresh(project: str, worktree_id: str) -> dict:
        if not _is_safe_identity_token(project) or not _is_safe_identity_token(worktree_id):
            raise ValueError("worktree_status refresh carries an unsafe project/worktree_id")
        return assemble(project, worktree_id)

    return _refresh


def build_cached_compute(
    cache: WorktreeStatusCache,
    assemble: Callable[[str, str], dict],
) -> Callable[[str, dict], dict]:
    """Wrap a raw fact-assembly function into a ``CoalescingServer``-shaped
    ``compute(kind, payload)`` callback that reads/refreshes through
    ``cache`` first.

    ``payload`` must carry ``project`` and ``worktree_id`` (mirroring
    ``classify_daemon``'s own payload contract: the daemon resolves
    everything itself from these identifiers, never trusts a caller-
    serialized bundle) and may carry ``force: true`` to bypass the cache's
    TTL. Raises ``ValueError`` for a malformed payload -- surfaced to every
    joined caller by ``CoalescingServer``, exactly like a
    ``_classify_daemon_compute`` error would be. Both identities are
    validated against path-traversal (a lone-component token, no ``/``,
    ``\\``, or ``..``) before ever reaching ``assemble``, which uses them to
    build filesystem paths (``cfg.project_dir(project) / "worktrees" /
    f"{worktree_id}.yaml"`` in ``_worktree_status_compute``) -- a client
    that can read the daemon's rendezvous token could otherwise submit a
    crafted id to make the monitor load a YAML outside the selected
    project's own tracking directory.
    """
    assemble = validated_refresh(assemble)

    def _compute(kind: str, payload: dict) -> dict:
        project = str(payload.get("project") or "")
        worktree_id = str(payload.get("worktree_id") or "")
        if not project or not worktree_id:
            raise ValueError("worktree_status request missing project/worktree_id")
        force = bool(payload.get("force"))
        return cache.get_or_refresh(
            project,
            worktree_id,
            force=force,
            compute=lambda: assemble(project, worktree_id),
        )

    return _compute


def start_server(
    compute: Callable[[str, dict], dict],
    *,
    on_idle: Callable[[], None] | None = None,
) -> CoalescingServer:
    """Start the worktree-status-serving daemon side (not yet published anywhere)."""
    return CoalescingServer(
        compute,
        linger_seconds=LINGER_SECONDS,
        subscriber_ttl=SUBSCRIBER_TTL_SECONDS,
        on_idle=on_idle,
    )


def rendezvous_fields(server: CoalescingServer) -> dict:
    """Rendezvous fields namespaced so they never collide with the resident
    status-monitor's existing ``HookIpcServer``/``classify_daemon`` fields in
    the same lock file (see ``hook_ipc.HookIpcServer.rendezvous`` and
    ``classify_daemon.rendezvous_fields``)."""
    rv = server.rendezvous()
    return {
        "worktree_status_transport": rv["transport"],
        "worktree_status_endpoint": rv["endpoint"],
        "worktree_status_token": rv["token"],
        "worktree_status_generation": rv["generation"],
    }


#: How often the background sweep walks demanded worktrees looking for
#: TTL-expired entries to proactively refresh (see
#: ``worktree_status_cache.WorktreeStatusCache.sweep_due``). Independent of
#: the cache's own TTL -- this only needs to run often enough that a
#: demanded worktree rarely goes stale between sweeps, not on every request.
SWEEP_INTERVAL_SECONDS = 10.0


def start_sweep_thread(
    cache: WorktreeStatusCache,
    refresh: Callable[[str, str], dict],
    *,
    interval: float = SWEEP_INTERVAL_SECONDS,
    stop_event: threading.Event | None = None,
) -> threading.Thread:
    """Start a daemon thread that periodically calls
    ``cache.sweep_due(refresh=refresh)`` until ``stop_event`` is set.

    This is what makes an ordinary (non-forced) read fast for an *already-
    demanded* worktree: the sweep keeps it warm in the background so a
    render/click never pays the recompute cost itself, per the vision's
    ``continuously-revalidated-freshness``/``freshness-is-pursued-not-
    assumed``. A sweep failure for one worktree is already handled inside
    ``sweep_due`` itself (skipped, never raised) -- this loop only needs to
    survive an unexpected exception from ``sweep_due`` as a whole, which it
    does by catching and continuing rather than letting the thread die
    silently.
    """
    stop = stop_event if stop_event is not None else threading.Event()

    def _loop() -> None:
        while not stop.wait(interval):
            try:
                cache.sweep_due(refresh=refresh)
            except Exception:
                continue

    thread = threading.Thread(
        target=_loop, name="worktree-status-sweep", daemon=True
    )
    thread.start()
    return thread


def endpoint_from_rendezvous(data: dict | None) -> tuple[str, int, str] | None:
    """Parse this module's rendezvous fields out of an already-read lock dict.

    Returns ``None`` for anything malformed or absent -- the caller's own
    correct fallback path, never an exception. This is the same parsing a
    cross-venv consumer's own client implements against the documented wire
    contract (see the effort README's Phase 1 design and
    ``docs/patterns/work-coalescing-singleton.md``): the fields below, the
    lock file itself, and the ``work_coalescing_singleton`` wire protocol are
    the full contract -- nothing else is required to reach this daemon.
    """
    if not isinstance(data, dict):
        return None
    endpoint = data.get("worktree_status_endpoint")
    token = data.get("worktree_status_token")
    if not isinstance(endpoint, str) or not isinstance(token, str) or not token:
        return None
    host, _, port_s = endpoint.partition(":")
    if not host or not port_s:
        return None
    try:
        port = int(port_s)
    except ValueError:
        return None
    return host, port, token


def status_with_boot(
    *,
    read_lock_data: Callable[[], dict | None],
    ensure_monitor: Callable[[], bool] | None,
    key: str,
    payload: dict,
    fallback: Callable[[], dict],
    request_deadline_s: float = REQUEST_DEADLINE_S,
    boot_wait_s: float = BOOT_WAIT_S,
    poll_interval_s: float = 0.1,
) -> dict:
    """The full production sequence: dial, boot-and-wait if no resident
    monitor is currently publishing a worktree-status endpoint, then send one
    coalesced request carrying a fresh per-call client id (so the daemon's
    own ref-counted idle-exit sees this caller as a live, if brief,
    subscriber), releasing that same client id in a ``finally`` right after
    -- mirrors ``classify_daemon.classify_with_boot`` exactly (see that
    function's own docstring for the full rationale of each step). Never
    raises past this call: any miss at any stage runs ``fallback()``.
    """
    started = time.time()

    def _dial() -> tuple[str, int, str] | None:
        return endpoint_from_rendezvous(read_lock_data())

    endpoint = _dial()
    if endpoint is None and ensure_monitor is not None:
        ensure_monitor()
        while endpoint is None and time.time() - started < boot_wait_s:
            time.sleep(poll_interval_s)
            endpoint = _dial()
    if endpoint is None:
        return fallback()

    host, port, token = endpoint
    client_id = wcs_client.new_client_id()
    try:
        return wcs_client.request(
            host,
            port,
            token,
            kind=KIND,
            key=key,
            payload=payload,
            request_deadline_s=request_deadline_s,
            client_id=client_id,
        )
    except wcs_client.DaemonUnavailable:
        return fallback()
    finally:
        wcs_client.release(host, port, token, client_id, timeout=request_deadline_s)


def status_via_daemon(
    lock_data: dict | None,
    *,
    key: str,
    payload: dict,
    fallback: Callable[[], dict],
    request_deadline_s: float = REQUEST_DEADLINE_S,
) -> dict:
    """Try a live daemon (per ``lock_data``'s rendezvous fields) first, else
    run ``fallback`` immediately. Never boots a daemon itself.

    Unlike ``classify_daemon.classify_via_daemon`` (which this otherwise
    mirrors), this call always carries a per-call ``client_id`` and releases
    it in a ``finally`` right after -- same as :func:`status_with_boot`.
    Without one, a cold/slow compute this request triggers has no live
    ``CoalescingServer`` subscriber and no completed cache entry yet, so
    :meth:`InProcessRuntime.has_active_demand` sees no activity at all and
    the monitor's own empty-strike idle-shutdown could close the runtime
    (and its cache) while this request is still in flight.
    """
    endpoint = endpoint_from_rendezvous(lock_data)
    if endpoint is None:
        return fallback()
    host, port, token = endpoint
    client_id = wcs_client.new_client_id()
    try:
        return wcs_client.request(
            host,
            port,
            token,
            kind=KIND,
            key=key,
            payload=payload,
            request_deadline_s=request_deadline_s,
            client_id=client_id,
        )
    except wcs_client.DaemonUnavailable:
        return fallback()
    finally:
        wcs_client.release(host, port, token, client_id, timeout=request_deadline_s)


class InProcessRuntime:
    """Owns the in-process worktree-status accelerator's whole lifecycle for
    `cmd_status_monitor`: the coalescing server, its durable cache, and the
    background sweep thread -- factored out of ``__main__.py`` so
    `cmd_status_monitor` only has a handful of call sites, not the full
    start/shutdown wiring (keeps that module under its shrink-only size
    baseline; see the effort README's Phase 2/3 journal).

    Independent of the monitor's other daemons (`HookIpcServer`,
    `classify_daemon`) -- a failure anywhere in :meth:`start` degrades to an
    all-``None`` runtime (never fatal to the monitor itself), mirroring the
    ``try/except`` guard `cmd_status_monitor` already applies to those.
    """

    def __init__(self) -> None:
        self.server: CoalescingServer | None = None
        self.cache: WorktreeStatusCache | None = None
        self._sweep_stop = threading.Event()
        self._sweep_thread: threading.Thread | None = None

    def start(self, cache_path: Path, assemble: Callable[[str, str], dict]) -> None:
        try:
            self.cache = WorktreeStatusCache(cache_path)
            self.server = start_server(build_cached_compute(self.cache, assemble))
            self.server.start()
            self._sweep_thread = start_sweep_thread(
                self.cache, validated_refresh(assemble), stop_event=self._sweep_stop
            )
        except Exception:
            # A partial failure (e.g. the sweep thread fails to start after
            # the cache/server already came up) must not leak an open SQLite
            # connection or a still-running server/threads -- degrade to a
            # fully torn-down, all-``None`` runtime via the same shutdown
            # path a normal stop uses, not just clearing `self.server`.
            self.shutdown()
            self.server = None
            self.cache = None
            self._sweep_thread = None
            self._sweep_stop = threading.Event()

    def lock_extra(self) -> dict:
        """Rendezvous fields to merge into the monitor's lock file, or an
        empty dict when the server never came up."""
        if self.server is None:
            return {}
        return rendezvous_fields(self.server)

    def has_active_demand(self) -> bool:
        # A cache-level `has_active_demand()` alone only becomes true once a
        # request has actually completed and published an entry -- a fresh
        # cold request has a live `CoalescingServer` subscriber the whole
        # time its compute is in flight, but nothing in the cache yet. Miss
        # that, and the monitor's own idle-shutdown strikes (no mux/Picker/
        # list activity) could close this runtime -- and the cache along
        # with it -- while that first compute is still running, dropping
        # its result on the floor. Count either signal as "still busy".
        if self.server is not None and self.server.subscriber_count() > 0:
            return True
        return self.cache is not None and self.cache.has_active_demand()

    def shutdown(self) -> None:
        self._sweep_stop.set()
        if self._sweep_thread is not None:
            # Join before closing the server/cache: `sweep_due` (running on
            # this thread) may be mid-`_persist` right now, and closing the
            # cache's SQLite connection out from under it would race a
            # successor monitor's own writer -- see the effort's Phase 2
            # review fix. Bounded so a wedged sweep can never hang shutdown.
            self._sweep_thread.join(timeout=5)
        if self.server is not None:
            # `CoalescingServer.close()` stops its own accept/reaper threads
            # but does not wait for an already-dispatched request-handler
            # thread to finish (no drain primitive exists in the vendored
            # library for this). A handler still mid-compute when we reach
            # here could therefore still call into the cache after the next
            # line closes it -- `WorktreeStatusCache.close()`'s own
            # `_closed` guard (see its Phase 2 review fix) is what actually
            # makes that race safe: a late call finds the cache closed and
            # skips SQLite entirely rather than reopening/writing through a
            # connection this shutdown already tore down.
            self.server.close()
        if self.cache is not None:
            self.cache.close()

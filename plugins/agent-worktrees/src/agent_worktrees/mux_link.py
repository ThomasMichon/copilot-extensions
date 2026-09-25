"""Resident manager-observation IPC seam (Phase 3b Slice 2 Sub-slice 3, Step 1
-- ``worktree-manager-control-plane`` effort).

Additive-only groundwork for the two-daemon mux-status split: the resident
``agent-worktrees`` status-monitor gains a dedicated wire surface that lets an
external Worktree Manager mux-companion daemon **push** live mux-mapping
observations for the sessions *it* owns, plus an in-process cache that stores
them. Nothing in this module changes who writes a session's status bar or
which sessions get served -- ``status_monitor_cli.cmd_status_monitor`` only
starts this endpoint and merges its (currently always-empty, since no
Worktree Manager daemon exists yet to push into it) observations into the
resident monitor's existing catalog-observation call, per this step's own
scope in
``efforts/active/worktree-manager-control-plane/phase-3b-substatus-monitor-relocation.md``.

Mirrors ``classify_daemon.py``/``worktree_status_daemon.py`` structurally: a
``work_coalescing_singleton.CoalescingServer`` wraps a ``compute(kind,
payload)`` callback, rendezvous fields are namespaced so they never collide
with the other daemons already published in the same ``status-monitor.lock``
file, and the client-side ``mux_live_via_daemon``/``mux_live_with_boot``
helpers mirror ``worktree_status_daemon.status_via_daemon``/
``status_with_boot`` exactly -- kept here (not yet called by anything) so the
full wire contract is pinned in one step, ready for the Worktree Manager
mux-companion daemon (a later sub-slice) to call.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from work_coalescing_singleton import CoalescingServer
from work_coalescing_singleton import client as wcs_client

#: The one request kind this daemon-link surface accepts.
KIND = "mux_live"

#: A push is a single in-memory dict update -- cheap and synchronous, unlike
#: the classify/worktree-status daemons' real git/filesystem work. Kept
#: short so a caller never stalls waiting for this cache to answer.
REQUEST_DEADLINE_S = 2.0
#: How long a boot-wait for the resident monitor itself may take (mirrors
#: ``classify_daemon.BOOT_WAIT_S``/``worktree_status_daemon.BOOT_WAIT_S``).
BOOT_WAIT_S = 6.0

#: Shorter linger than the read-side daemons: a push caller (the Worktree
#: Manager mux-companion daemon) is expected to reconnect frequently as
#: mappings change, not hold one long-lived subscription.
LINGER_SECONDS = 5.0
SUBSCRIBER_TTL_SECONDS = 30.0

_REQUIRED_STR_FIELDS = ("worktree_id", "session")


class ManagedMuxCache:
    """Thread-safe, in-memory store of Manager-reported ``worktree_id`` ⇄
    mux session/pane mapping observations.

    Keyed by ``worktree_id``. Each observation carries a ``mapping_revision``
    (a non-negative integer the *reporting* Manager mux-companion daemon
    owns and increments); an incoming observation whose revision is lower
    than the currently stored one is rejected rather than applied, so a
    stale/out-of-order ``live: false`` (or stale-pane) event can never
    clobber a newer live mapping that already arrived out of send order over
    a connectionless, potentially-concurrent transport. Revisions equal to
    the stored one are accepted (idempotent replay -- e.g. a caller retrying
    after a response it never saw).

    Deliberately in-memory only, matching the resident status-monitor's own
    existing registries (``status-monitor.d``, the ``published``/
    ``incarnations`` sweep state) -- a monitor restart naturally starts this
    cache empty again; the Manager mux-companion daemon (a later sub-slice)
    re-publishes its live mappings on the next observation it sends, exactly
    the same "recovers on next report" shape ``session_catalog`` already
    relies on for ordinary mux liveness.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, dict] = {}

    def apply_observation(self, payload: dict) -> dict:
        """Validate and apply one observation.

        Raises ``ValueError`` for a structurally malformed payload (missing
        ``worktree_id``/``session``, or a non-integer ``mapping_revision``)
        -- the same "never silently accept garbage" contract
        ``worktree_status_daemon.build_cached_compute`` uses for its own
        request validation. Returns ``{"applied": True, "revision": <int>}``
        on success, or ``{"applied": False, "reason": "stale_revision",
        "current_revision": <int>}`` when a newer revision is already on
        file.
        """
        for field in _REQUIRED_STR_FIELDS:
            value = payload.get(field)
            if not isinstance(value, str) or not value:
                raise ValueError(f"mux_live observation missing '{field}'")
        worktree_id = payload["worktree_id"]
        session = payload["session"]
        revision_raw = payload.get("mapping_revision")
        if isinstance(revision_raw, bool) or not isinstance(revision_raw, int):
            raise ValueError("mux_live observation requires an integer mapping_revision")
        revision = revision_raw
        if revision < 0:
            raise ValueError("mux_live observation mapping_revision must be non-negative")
        live = bool(payload.get("live", True))
        path = payload.get("path")
        panes = payload.get("panes")
        if not isinstance(panes, list) or not all(isinstance(p, str) for p in panes):
            panes = []
        incarnation = payload.get("incarnation")
        if not isinstance(incarnation, str):
            incarnation = ""
        attached_clients = payload.get("attached_clients")
        if isinstance(attached_clients, bool) or not isinstance(attached_clients, int):
            attached_clients = 0

        with self._lock:
            current = self._entries.get(worktree_id)
            if current is not None and revision < current["mapping_revision"]:
                return {
                    "applied": False,
                    "reason": "stale_revision",
                    "current_revision": current["mapping_revision"],
                }
            self._entries[worktree_id] = {
                "worktree_id": worktree_id,
                "session": session,
                "path": path if isinstance(path, str) else None,
                "panes": panes,
                "incarnation": incarnation,
                "attached_clients": attached_clients,
                "mapping_revision": revision,
                "live": live,
                "updated_at": time.time(),
            }
        return {"applied": True, "revision": revision}

    def get(self, worktree_id: str) -> dict | None:
        with self._lock:
            entry = self._entries.get(worktree_id)
            return dict(entry) if entry is not None else None

    def live_session_names(self) -> set[str]:
        """Session names for every currently-live managed mapping -- the
        seam ``_monitor_sweep`` merges into its own direct mux-scan
        observation, per this step's scope (observation only, no writes)."""
        with self._lock:
            return {e["session"] for e in self._entries.values() if e["live"]}

    def snapshot(self) -> dict[str, dict]:
        """A defensive copy of every entry, keyed by ``worktree_id``."""
        with self._lock:
            return {wid: dict(entry) for wid, entry in self._entries.items()}

    def has_any_live(self) -> bool:
        with self._lock:
            return any(e["live"] for e in self._entries.values())


def build_compute(cache: ManagedMuxCache) -> Callable[[str, dict], dict]:
    """Wrap ``cache.apply_observation`` into a ``CoalescingServer``-shaped
    ``compute(kind, payload)`` callback, rejecting any request whose ``kind``
    is not :data:`KIND`."""

    def _compute(kind: str, payload: dict) -> dict:
        if kind != KIND:
            raise ValueError(f"mux_link daemon does not serve kind={kind!r}")
        return cache.apply_observation(payload)

    return _compute


def start_server(
    compute: Callable[[str, dict], dict],
    *,
    on_idle: Callable[[], None] | None = None,
) -> CoalescingServer:
    """Start the manager-observation-serving daemon side (not yet published
    anywhere)."""
    return CoalescingServer(
        compute,
        linger_seconds=LINGER_SECONDS,
        subscriber_ttl=SUBSCRIBER_TTL_SECONDS,
        on_idle=on_idle,
    )


def rendezvous_fields(server: CoalescingServer) -> dict:
    """Rendezvous fields namespaced so they never collide with the resident
    status-monitor's existing ``HookIpcServer``/``classify_daemon``/
    ``worktree_status_daemon`` fields in the same lock file."""
    rv = server.rendezvous()
    return {
        "managed_mux_transport": rv["transport"],
        "managed_mux_endpoint": rv["endpoint"],
        "managed_mux_token": rv["token"],
        "managed_mux_generation": rv["generation"],
    }


def endpoint_from_rendezvous(data: dict | None) -> tuple[str, int, str] | None:
    """Parse this module's rendezvous fields out of an already-read lock
    dict. Returns ``None`` for anything malformed or absent -- the caller's
    own correct fallback path, never an exception."""
    if not isinstance(data, dict):
        return None
    endpoint = data.get("managed_mux_endpoint")
    token = data.get("managed_mux_token")
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


def mux_live_via_daemon(
    lock_data: dict | None,
    *,
    key: str,
    payload: dict,
    fallback: Callable[[], dict],
    request_deadline_s: float = REQUEST_DEADLINE_S,
) -> dict:
    """Try a live daemon (per ``lock_data``'s rendezvous fields) first, else
    run ``fallback`` immediately. Never boots a daemon itself.

    Not yet called by anything in this step -- pinned here so the future
    Worktree Manager mux-companion daemon caller has an exact, already-
    tested wire contract to build on, mirroring
    ``worktree_status_daemon.status_via_daemon``.
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


def mux_live_with_boot(
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
    """The full boot-and-wait push sequence, mirroring
    ``worktree_status_daemon.status_with_boot``/``classify_daemon
    .classify_with_boot`` exactly: dial, boot-and-wait for the resident
    monitor's observation endpoint to appear if none is currently published,
    then push one observation with a fresh per-call client id (released in a
    ``finally`` right after). Never raises past this call."""
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


class InProcessRuntime:
    """Owns the in-process manager-observation seam's whole lifecycle for
    ``cmd_status_monitor``: the coalescing server plus its
    :class:`ManagedMuxCache` -- factored out the same way
    ``worktree_status_daemon.InProcessRuntime`` is, so ``cmd_status_monitor``
    only has a handful of call sites, not the full start/shutdown wiring.

    Independent of the monitor's other daemons -- a failure in :meth:`start`
    degrades to an all-``None`` runtime (never fatal to the monitor itself),
    mirroring the ``try/except`` guard already applied to
    ``HookIpcServer``/``classify_daemon``/``worktree_status_daemon``.
    """

    def __init__(self) -> None:
        self.server: CoalescingServer | None = None
        self.cache: ManagedMuxCache | None = None

    def start(self) -> None:
        try:
            cache = ManagedMuxCache()
            server = start_server(build_compute(cache))
            server.start()
        except Exception:
            self.server = None
            self.cache = None
            return
        self.cache = cache
        self.server = server

    def lock_extra(self) -> dict:
        """Rendezvous fields to merge into the monitor's lock file, or an
        empty dict when the server never came up."""
        if self.server is None:
            return {}
        return rendezvous_fields(self.server)

    def live_session_names(self) -> set[str]:
        """Session names this step's caller (``_monitor_sweep``) merges into
        its own direct mux-scan observation. Empty until a Manager
        mux-companion daemon (a later sub-slice) actually pushes anything."""
        if self.cache is None:
            return set()
        return self.cache.live_session_names()

    def has_active_demand(self) -> bool:
        if self.server is not None and self.server.subscriber_count() > 0:
            return True
        return self.cache is not None and self.cache.has_any_live()

    def shutdown(self) -> None:
        if self.server is not None:
            self.server.close()
        self.server = None
        self.cache = None

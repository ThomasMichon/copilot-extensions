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

import contextlib
import json
import os
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

from work_coalescing_singleton import CoalescingServer
from work_coalescing_singleton import client as wcs_client

#: The one request kind this daemon-link surface accepts. Matches the
#: ``mux-live-v1`` event name used throughout
#: ``phase-3b-substatus-monitor-relocation.md`` -- a Step 2+ Manager caller
#: following that documented contract must be able to reach this seam.
KIND = "mux-live-v1"

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

#: A live mapping with no fresh observation in this long is treated as
#: stale/unconfirmed (excluded from :meth:`ManagedMuxCache.live_session_names`
#: / :meth:`has_any_live`) rather than permanently live -- a crashed or
#: partitioned Manager mux-companion daemon before it ever reports
#: ``live: false`` must not pin the resident monitor's observation
#: indefinitely. The future Manager daemon is expected to re-push a live
#: mapping on a cadence well under this window (a heartbeat, or any real
#: mapping-changed event) to keep it fresh; a one-shot push alone will go
#: stale after this long with no follow-up.
MAPPING_STALE_AFTER_SECONDS = 45.0

_REQUIRED_STR_FIELDS = ("worktree_id", "session")


class ManagedMuxCache:
    """Thread-safe store of Manager-reported ``worktree_id`` ⇄ mux
    session/pane mapping observations, with a best-effort on-disk snapshot
    so a mapping survives one resident-monitor restart.

    Keyed by ``worktree_id``. Each observation carries a ``mapping_revision``
    (a non-negative integer the *reporting* Manager mux-companion daemon
    owns and increments); an incoming observation whose revision is lower
    than the currently stored one is rejected rather than applied, so a
    stale/out-of-order ``live: false`` (or stale-pane) event can never
    clobber a newer live mapping that already arrived out of send order over
    a connectionless, potentially-concurrent transport. Revisions equal to
    the stored one are accepted (idempotent replay -- e.g. a caller retrying
    after a response it never saw).

    A live entry with no fresh observation for
    :data:`MAPPING_STALE_AFTER_SECONDS` is treated as stale/unconfirmed by
    :meth:`live_session_names`/:meth:`has_any_live` (excluded from both)
    without being deleted -- :meth:`get`/:meth:`snapshot` still return it
    (with its true ``live`` bit) for callers that want the raw record.

    When ``persist_path`` is given, every successful :meth:`apply_observation`
    writes the full snapshot to that path (atomic replace, best-effort: a
    write failure is swallowed rather than raised, matching the resident
    monitor's own tolerance for a non-fatal persistence hiccup), and the
    constructor warm-loads any existing snapshot from it. This lets the
    Step 1 seam's own contract-level requirement -- a mapping survives one
    daemon restart -- hold even though ``InProcessRuntime`` itself is
    otherwise stateless across restarts, matching
    ``worktree_status_daemon.WorktreeStatusCache``'s own durable-across-
    restart precedent.
    """

    def __init__(self, persist_path: Path | str | None = None) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, dict] = {}
        self._persist_path = Path(persist_path) if persist_path is not None else None
        self._warm_load()

    def _warm_load(self) -> None:
        if self._persist_path is None:
            return
        try:
            raw = self._persist_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        for worktree_id, entry in data.items():
            if not isinstance(worktree_id, str) or not isinstance(entry, dict):
                continue
            try:
                self._entries[worktree_id] = _normalize_entry(entry)
            except ValueError:
                continue

    def _persist_locked(self) -> None:
        """Best-effort atomic snapshot write. Caller already holds ``_lock``."""
        if self._persist_path is None:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(
                dir=str(self._persist_path.parent), prefix=".mux-link-cache-"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(self._entries, fh, separators=(",", ":"))
                os.replace(tmp_name, self._persist_path)
            finally:
                with contextlib.suppress(OSError):
                    os.remove(tmp_name)
        except OSError:
            pass

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
        entry = _normalize_entry(payload)
        worktree_id = entry["worktree_id"]
        revision = entry["mapping_revision"]

        with self._lock:
            current = self._entries.get(worktree_id)
            if current is not None and revision < current["mapping_revision"]:
                return {
                    "applied": False,
                    "reason": "stale_revision",
                    "current_revision": current["mapping_revision"],
                }
            self._entries[worktree_id] = entry
            self._persist_locked()
        return {"applied": True, "revision": revision}

    def get(self, worktree_id: str) -> dict | None:
        with self._lock:
            entry = self._entries.get(worktree_id)
            return _deep_copy_entry(entry) if entry is not None else None

    def live_session_names(self) -> set[str]:
        """Session names for every currently-live, **fresh** (not yet stale
        per :data:`MAPPING_STALE_AFTER_SECONDS`) managed mapping -- the seam
        ``_monitor_sweep`` merges into its own direct mux-scan observation,
        per this step's scope (observation only, no writes)."""
        now = time.time()
        with self._lock:
            return {
                e["session"]
                for e in self._entries.values()
                if e["live"] and now - e["updated_at"] <= MAPPING_STALE_AFTER_SECONDS
            }

    def snapshot(self) -> dict[str, dict]:
        """A defensive (deep) copy of every entry, keyed by ``worktree_id``."""
        with self._lock:
            return {wid: _deep_copy_entry(entry) for wid, entry in self._entries.items()}

    def has_any_live(self) -> bool:
        now = time.time()
        with self._lock:
            return any(
                e["live"] and now - e["updated_at"] <= MAPPING_STALE_AFTER_SECONDS
                for e in self._entries.values()
            )


def _normalize_entry(payload: dict) -> dict:
    """Validate + normalize one observation payload into a stored entry
    shape. Raises ``ValueError`` for anything structurally malformed.
    Shared by :meth:`ManagedMuxCache.apply_observation` (an untrusted wire
    payload) and :meth:`ManagedMuxCache._warm_load` (a previously-persisted,
    already-normalized record -- re-validated anyway since a corrupt/
    tampered on-disk snapshot must not be trusted blindly, mirroring
    ``worktree_status_daemon.validated_refresh``'s own precedent)."""
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
    live_raw = payload.get("live", True)
    if not isinstance(live_raw, bool):
        raise ValueError("mux_live observation 'live' must be a boolean")
    live = live_raw
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
    updated_at = payload.get("updated_at")
    if not isinstance(updated_at, (int, float)) or isinstance(updated_at, bool):
        updated_at = time.time()

    return {
        "worktree_id": worktree_id,
        "session": session,
        "path": path if isinstance(path, str) else None,
        "panes": list(panes),
        "incarnation": incarnation,
        "attached_clients": attached_clients,
        "mapping_revision": revision,
        "live": live,
        "updated_at": updated_at,
    }


def _deep_copy_entry(entry: dict) -> dict:
    """Copy of ``entry`` whose ``panes`` list is independent of the stored
    one -- ``dict(entry)`` alone leaves ``panes`` shared, so a caller
    mutating the returned list would silently corrupt the live cache
    without holding its lock."""
    copied = dict(entry)
    copied["panes"] = list(entry["panes"])
    return copied


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

    def start(self, persist_path: Path | str | None = None) -> None:
        try:
            cache = ManagedMuxCache(persist_path=persist_path)
            server = start_server(build_compute(cache))
            # Assign before `server.start()`: `CoalescingServer.__init__`
            # already binds+listens its loopback socket, so a failure in
            # `.start()` (thread spawn) after construction must still be
            # reachable from the `except` branch below to close that
            # already-bound server -- never leave it running unadvertised.
            self.cache = cache
            self.server = server
            server.start()
        except Exception:
            self.shutdown()

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

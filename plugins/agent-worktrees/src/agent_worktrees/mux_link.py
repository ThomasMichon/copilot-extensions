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
import math
import os
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
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
#: stale after this long with no follow-up. This freshness window is a
#: local implementation detail of this cache, not part of the documented
#: wire contract below -- it is tracked against local receipt time
#: (``time.time()`` at the moment this process accepted the observation),
#: never the caller-supplied ``observed_at``, so a skewed sender clock can
#: never make a mapping look artificially fresh or stale.
MAPPING_STALE_AFTER_SECONDS = 45.0

#: Required string fields per the documented ``mux-live-v1`` payload
#: (``phase-3b-substatus-monitor-relocation.md``'s "Message contracts"
#: section): ``project``, ``worktree_id``, and ``mux_session``.
_REQUIRED_STR_FIELDS = ("project", "worktree_id", "mux_session")


class ManagedMuxCache:
    """Thread-safe store of Manager-reported ``(project, worktree_id)`` ⇄
    mux session/pane mapping observations (the documented ``mux-live-v1``
    payload shape), with a best-effort on-disk snapshot so a mapping
    survives one resident-monitor restart.

    Keyed by ``(project, worktree_id)`` -- ``worktree_id`` alone is not
    guaranteed unique across two different projects (Copilot review
    finding: keying on ``worktree_id`` alone let two projects' same-ID
    worktrees overwrite each other's mapping, or wrongly reject a valid
    lower revision as stale for an unrelated project, and could make
    :meth:`live_session_names` silently lose a live session). Each
    observation carries a ``mapping_revision`` (a non-negative integer the
    *reporting* Manager mux-companion daemon owns and increments, scoped per
    ``(project, worktree_id)``); an incoming observation whose revision is
    lower than the currently stored one for that same key is rejected
    rather than applied, so a stale/out-of-order ``live: false`` (or
    stale-pane) event can never clobber a newer live mapping that already
    arrived out of send order over a connectionless, potentially-concurrent
    transport. Revisions equal to the stored one are accepted (idempotent
    replay -- e.g. a caller retrying after a response it never saw).

    A live entry with no fresh observation for
    :data:`MAPPING_STALE_AFTER_SECONDS` is treated as stale/unconfirmed by
    :meth:`live_session_names`/:meth:`has_any_live` (excluded from both)
    without being deleted -- :meth:`get`/:meth:`snapshot` still return it
    (with its true ``live`` bit) for callers that want the raw record.

    When ``persist_path`` is given, every successful :meth:`apply_observation`
    writes the full snapshot -- a JSON **array** of already-self-describing
    entries (never a JSON object keyed by a separately-trusted string,
    which is exactly the shape that let an earlier revision of this cache
    accept an entry under a JSON key that disagreed with its own
    ``worktree_id`` field) -- to that path (atomic replace, best-effort: a
    write failure is swallowed rather than raised, matching the resident
    monitor's own tolerance for a non-fatal persistence hiccup), and the
    constructor warm-loads any existing snapshot from it, re-deriving each
    entry's storage key from its own validated ``project``/``worktree_id``
    fields. This lets the Step 1 seam's own contract-level requirement -- a
    mapping survives one daemon restart -- hold even though
    ``InProcessRuntime`` itself is otherwise stateless across restarts,
    matching ``worktree_status_daemon.WorktreeStatusCache``'s own
    durable-across-restart precedent.

    :meth:`close` marks this cache permanently closed: every subsequent
    :meth:`apply_observation` call becomes a safe no-op instead of writing.
    ``CoalescingServer.close()`` stops *accepting* new work but does not
    wait for an already-dispatched request-handler thread to finish (see
    its own docstring) -- without this guard, such a late handler could
    still call into a cache whose owning ``InProcessRuntime`` has already
    been shut down and atomically persist an *older* snapshot after a
    replacement runtime has already persisted a newer revision to the same
    ``persist_path``, regressing the on-disk monotonicity guard across a
    restart (Copilot review finding). ``InProcessRuntime.shutdown()`` calls
    this after closing the server, serialized through the same lock every
    ``apply_observation`` call takes, so a handler already past this check
    when ``close()`` runs still completes normally, but any handler that
    reaches the lock afterward safely aborts instead of writing.
    """

    def __init__(self, persist_path: Path | str | None = None) -> None:
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, str], dict] = {}
        self._persist_path = Path(persist_path) if persist_path is not None else None
        self._closed = False
        self._warm_load()

    def _warm_load(self) -> None:
        if self._persist_path is None:
            return
        try:
            raw = self._persist_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, ValueError):
            return
        if not isinstance(data, list):
            return
        for entry in data:
            if not isinstance(entry, dict):
                continue
            try:
                normalized = _normalize_entry(entry, trust_received_at=True)
            except ValueError:
                continue
            key = (normalized["project"], normalized["worktree_id"])
            self._entries[key] = normalized

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
                    json.dump(list(self._entries.values()), fh, separators=(",", ":"))
                os.replace(tmp_name, self._persist_path)
            finally:
                with contextlib.suppress(OSError):
                    os.remove(tmp_name)
        except OSError:
            pass

    def close(self) -> None:
        """Permanently close this cache: every subsequent
        :meth:`apply_observation` becomes a no-op (see this class's own
        docstring for why this exists)."""
        with self._lock:
            self._closed = True

    def apply_observation(self, payload: dict) -> dict:
        """Validate and apply one ``mux-live-v1`` observation.

        Raises ``ValueError`` for a structurally malformed payload (missing
        ``project``/``worktree_id``/``mux_session``, or a non-integer
        ``mapping_revision``) -- the same "never silently accept garbage"
        contract ``worktree_status_daemon.build_cached_compute`` uses for
        its own request validation. Returns ``{"applied": True, "revision":
        <int>}`` on success, ``{"applied": False, "reason":
        "stale_revision", "current_revision": <int>}`` when a newer revision
        is already on file **for the same ``(project, worktree_id)``**, or
        ``{"applied": False, "reason": "closed"}`` when this cache has
        already been closed (see :meth:`close`).
        """
        entry = _normalize_entry(payload)
        key = (entry["project"], entry["worktree_id"])
        revision = entry["mapping_revision"]

        with self._lock:
            if self._closed:
                return {"applied": False, "reason": "closed"}
            current = self._entries.get(key)
            if current is not None and revision < current["mapping_revision"]:
                return {
                    "applied": False,
                    "reason": "stale_revision",
                    "current_revision": current["mapping_revision"],
                }
            self._entries[key] = entry
            self._persist_locked()
        return {"applied": True, "revision": revision}

    def get(self, project: str, worktree_id: str) -> dict | None:
        with self._lock:
            entry = self._entries.get((project, worktree_id))
            return _deep_copy_entry(entry) if entry is not None else None

    def live_session_names(self) -> set[str]:
        """Mux session names (``mux_session``) for every currently-live,
        **fresh** (not yet stale per :data:`MAPPING_STALE_AFTER_SECONDS`)
        managed mapping -- the seam ``_monitor_sweep`` merges into its own
        direct mux-scan observation, per this step's scope (observation
        only, no writes)."""
        now = time.time()
        with self._lock:
            return {
                e["mux_session"]
                for e in self._entries.values()
                if e["live"] and now - e["received_at"] <= MAPPING_STALE_AFTER_SECONDS
            }

    def snapshot(self) -> dict[tuple[str, str], dict]:
        """A defensive (deep) copy of every entry, keyed by
        ``(project, worktree_id)``."""
        with self._lock:
            return {key: _deep_copy_entry(entry) for key, entry in self._entries.items()}

    def has_any_live(self) -> bool:
        now = time.time()
        with self._lock:
            return any(
                e["live"] and now - e["received_at"] <= MAPPING_STALE_AFTER_SECONDS
                for e in self._entries.values()
            )


def _normalize_entry(payload: dict, *, trust_received_at: bool = False) -> dict:
    """Validate + normalize one observation payload into a stored entry
    shape, matching the documented ``mux-live-v1`` payload in
    ``phase-3b-substatus-monitor-relocation.md``'s "Message contracts"
    section (``project``, ``worktree_id``, ``worktree_path``,
    ``mux_session``, ``session_incarnation``, ``panes`` -- a list of
    ``{pane_id, role, live}`` objects --, ``attached_clients``, ``live``,
    ``mapping_revision``, ``observed_at``). Raises ``ValueError`` for
    anything structurally malformed. Shared by
    :meth:`ManagedMuxCache.apply_observation` (an untrusted wire payload --
    ``trust_received_at=False``, the default: any caller-supplied
    ``received_at`` is ignored and replaced with this process's own receipt
    time, since trusting it would let a future timestamp keep a mapping
    artificially fresh forever, or a past one make it immediately stale,
    defeating the local-receipt freshness contract) and
    :meth:`ManagedMuxCache._warm_load` (a previously-persisted,
    already-normalized record -- passes ``trust_received_at=True`` so a
    warm-loaded mapping's original receipt time carries forward instead of
    being reset to "now" on every restart; still re-validated for structure
    since a corrupt/tampered on-disk snapshot must not be trusted blindly,
    mirroring ``worktree_status_daemon.validated_refresh``'s own
    precedent)."""
    for field in _REQUIRED_STR_FIELDS:
        value = payload.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(f"mux-live-v1 observation missing '{field}'")
    project = payload["project"]
    worktree_id = payload["worktree_id"]
    mux_session = payload["mux_session"]
    revision_raw = payload.get("mapping_revision")
    if isinstance(revision_raw, bool) or not isinstance(revision_raw, int):
        raise ValueError("mux-live-v1 observation requires an integer mapping_revision")
    revision = revision_raw
    if revision < 0:
        raise ValueError("mux-live-v1 observation mapping_revision must be non-negative")
    live_raw = payload.get("live", True)
    if not isinstance(live_raw, bool):
        raise ValueError("mux-live-v1 observation 'live' must be a boolean")
    live = live_raw
    worktree_path = payload.get("worktree_path")
    panes = _normalize_panes(payload.get("panes"))
    session_incarnation = payload.get("session_incarnation")
    if not isinstance(session_incarnation, str):
        session_incarnation = ""
    attached_clients = payload.get("attached_clients")
    if isinstance(attached_clients, bool) or not isinstance(attached_clients, int):
        attached_clients = 0
    observed_at = payload.get("observed_at")
    if not isinstance(observed_at, str) or not observed_at:
        observed_at = datetime.now(timezone.utc).isoformat()
    # Local-receipt timestamp for this cache's own freshness/staleness
    # window -- deliberately independent of the caller-supplied
    # `observed_at`. Never trusted from an untrusted wire payload (see this
    # function's own docstring); only a warm-loaded, already-normalized
    # snapshot record carries its original value forward.
    if trust_received_at:
        received_at = payload.get("received_at")
        # `json.loads` permits non-finite numbers (`Infinity`/`-Infinity`/
        # `NaN`), and `isinstance(x, (int, float))` accepts them too -- an
        # `Infinity` value would make `now - received_at <=
        # MAPPING_STALE_AFTER_SECONDS` true forever, letting a corrupt/
        # tampered snapshot defeat the stale-mapping safeguard permanently
        # (Copilot review finding). Require a genuinely finite number.
        if (
            not isinstance(received_at, (int, float))
            or isinstance(received_at, bool)
            or not math.isfinite(received_at)
        ):
            received_at = time.time()
    else:
        received_at = time.time()

    return {
        "project": project,
        "worktree_id": worktree_id,
        "worktree_path": worktree_path if isinstance(worktree_path, str) else None,
        "mux_session": mux_session,
        "session_incarnation": session_incarnation,
        "panes": panes,
        "attached_clients": attached_clients,
        "mapping_revision": revision,
        "live": live,
        "observed_at": observed_at,
        "received_at": received_at,
    }


def _normalize_panes(raw) -> list[dict]:
    """Sanitize the documented ``panes`` list (``[{pane_id, role, live}]``).
    A malformed list, or one containing a non-dict/missing-``pane_id``
    element, drops just that element rather than the whole list -- panes are
    informational (never gate ``live_session_names``/``has_any_live``, which
    key off the top-level ``live`` bit only)."""
    if not isinstance(raw, list):
        return []
    normalized = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        pane_id = item.get("pane_id")
        if not isinstance(pane_id, str) or not pane_id:
            continue
        role = item.get("role")
        if not isinstance(role, str):
            role = ""
        pane_live = item.get("live")
        if not isinstance(pane_live, bool):
            pane_live = True
        normalized.append({"pane_id": pane_id, "role": role, "live": pane_live})
    return normalized


def _deep_copy_entry(entry: dict) -> dict:
    """Copy of ``entry`` whose ``panes`` list (and each pane dict within it)
    is independent of the stored one -- ``dict(entry)`` alone leaves
    ``panes`` shared, so a caller mutating the returned list/dicts would
    silently corrupt the live cache without holding its lock."""
    copied = dict(entry)
    copied["panes"] = [dict(pane) for pane in entry["panes"]]
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


def _push_key(payload: dict) -> str:
    """Derive the ``CoalescingServer`` coalescing key for one push from the
    payload itself, rather than trust an arbitrary caller-supplied key.

    ``CoalescingServer`` coalesces concurrent requests sharing the same
    ``(kind, key)`` onto a single execution, joining late callers onto the
    first one's in-flight result rather than starting a second (a contract
    designed for idempotent *reads*, where any concurrent caller asking the
    identical question is happy with the identical answer). A **push** is
    not idempotent that way: if a caller used a key that stayed constant
    across different observations for the same worktree (e.g. bare
    ``worktree_id``), two concurrent pushes carrying two different
    ``mapping_revision``s could coalesce onto one execution -- the second,
    real observation would never reach :meth:`ManagedMuxCache
    .apply_observation` at all, and that caller would receive the first
    push's response as if its own had succeeded, silently dropping the
    update with no way for the monotonic-revision guard to ever recover it
    (Copilot review finding). Keying on ``(project, worktree_id,
    mapping_revision)`` instead makes every distinct observation its own
    coalescing slot -- ``project`` matters too, since ``worktree_id`` alone
    is not guaranteed unique across two different projects' concurrent
    pushes at the same revision number (Copilot review finding) -- while
    still deduplicating truly-concurrent identical retries the way the
    coalescing contract intends.

    Raises ``ValueError`` for a payload missing any of the three fields,
    mirroring :func:`_normalize_entry`'s own required-field validation so a
    caller finds out before ever reaching the wire.
    """
    project = payload.get("project")
    worktree_id = payload.get("worktree_id")
    revision = payload.get("mapping_revision")
    if not isinstance(project, str) or not project:
        raise ValueError("mux-live-v1 push payload missing 'project'")
    if not isinstance(worktree_id, str) or not worktree_id:
        raise ValueError("mux-live-v1 push payload missing 'worktree_id'")
    if isinstance(revision, bool) or not isinstance(revision, int):
        raise ValueError("mux-live-v1 push payload requires an integer mapping_revision")
    # Length-prefix both string components so the split points are
    # unambiguous regardless of their own content (mirrors
    # `worktree_status_daemon.coalescing_key`'s own injective-key
    # rationale) -- neither `project` nor `worktree_id` is restricted
    # against a literal `:`.
    return f"{len(project)}:{project}:{len(worktree_id)}:{worktree_id}:{revision}"


def mux_live_via_daemon(
    lock_data: dict | None,
    *,
    payload: dict,
    fallback: Callable[[], dict],
    request_deadline_s: float = REQUEST_DEADLINE_S,
) -> dict:
    """Try a live daemon (per ``lock_data``'s rendezvous fields) first, else
    run ``fallback`` immediately. Never boots a daemon itself.

    The coalescing key is derived internally from ``payload`` (see
    :func:`_push_key`) rather than accepted from the caller, so a future
    caller cannot accidentally choose an unsafe key that coalesces two
    different observations together.

    Not yet called by anything in this step -- pinned here so the future
    Worktree Manager mux-companion daemon caller has an exact, already-
    tested wire contract to build on, mirroring
    ``worktree_status_daemon.status_via_daemon``.
    """
    endpoint = endpoint_from_rendezvous(lock_data)
    if endpoint is None:
        return fallback()
    key = _push_key(payload)
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
    ``finally`` right after). Never raises past this call except for a
    malformed ``payload`` (see :func:`_push_key`) -- the coalescing key is
    derived internally, never accepted from the caller."""
    started = time.time()
    key = _push_key(payload)

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
            # Stop accepting new work first. `CoalescingServer.close()`
            # does not wait for an already-dispatched handler thread to
            # finish, so `cache.close()` below is what actually makes a
            # late handler's write safe (see `ManagedMuxCache`'s own
            # docstring) -- ordering `server.close()` first here is belt-
            # and-suspenders (no new handler can be dispatched after this),
            # not itself the safety guarantee.
            self.server.close()
        if self.cache is not None:
            self.cache.close()
        self.server = None
        self.cache = None

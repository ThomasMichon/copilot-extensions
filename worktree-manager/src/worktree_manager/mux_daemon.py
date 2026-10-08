"""Worktree Manager mux-companion daemon (Phase 3b Slice 2 Sub-slice 3
-- ``worktree-manager-control-plane`` effort).

Implements the Manager-side resident daemon and its wire server, plus the Step
3 launch/join/cleanup helpers that keep the Manager-owned
``worktree_id ⇄ mux session`` mapping current and publish ``mux-live-v1``
observations into ``agent-worktrees``. The mapping registry itself
(:class:`~worktree_manager.mux_mapping_registry.MuxMappingRegistry`) lives in
the sibling ``mux_mapping_registry`` module -- split out purely to stay under
this repo's per-module line cap; see that module's own docstring for the
registry's own design rationale.

Mirrors ``agent_worktrees.mux_link`` structurally (same
``work_coalescing_singleton.CoalescingServer`` shape, same lockfile-rendezvous
pattern) but is intentionally NOT an import of that module --
``mux_companion.py``'s own docstring documents the process-boundary rule this
package follows: Worktree Manager never imports ``agent_worktrees``
in-process, only reaches it through a subprocess/wire boundary. The two
directions of this slice's IPC contract are therefore implemented
independently on each side:

* **Manager -> agent-worktrees** (``mux-live-v1``, Step 3's job): pushed via
  this module's own lockfile-rendezvous + loopback client, now wired from the
  real launch/join/cleanup path and refreshed opportunistically while the
  daemon applies routed status.
* **agent-worktrees -> Manager** (``mux-status-v1``, this module): THIS
  daemon is the wire *server* for that kind. :func:`build_compute` is the
  ``compute(kind, payload)`` callback a ``CoalescingServer`` wraps, mirroring
  ``mux_link.build_compute`` exactly.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from work_coalescing_singleton import CoalescingServer
from zdd.diagnostics import process_start_time

from . import mux_daemon_cutover, mux_daemon_live, mux_daemon_process
from .mux_attached_clients import refresh_attached_clients
from .mux_mapping_registry import (
    MuxMappingRegistry,
    LIVE_MAPPING_BACKSTOP_INTERVAL_S,
    get_mapping,
    live_mapping_republish_due,
    registry_path,
    register_mapping,
    register_next_mapping,
    remove_mapping,
)
from .self_install import current_version, default_root

__all__ = [
    "get_mapping",
    "register_mapping",
    "remove_mapping",
    "registry_path",
    "MuxMappingRegistry",
    "read_lock_data",
    "register_managed_mapping",
    "remove_managed_mapping",
    "_daemon_is_live",
    "_acquire_daemon_lease",
    "_release_daemon_lease",
    "_scrub_session_credentials",
    "_spawn_detached",
]

#: The one request kind this daemon serves. Matches the ``mux-status-v1``
#: event name in ``phase-3b-substatus-monitor-relocation.md``'s "Message
#: contracts" section.
KIND = "mux-status-v1"

#: A status apply is a handful of bounded subprocess calls -- generous
#: relative to ``mux_link``'s own push deadline (a pure in-memory update),
#: but still short enough that a caller never stalls meaningfully.
REQUEST_DEADLINE_S = 5.0
#: How long a caller may boot-wait for this daemon (mirrors
#: ``mux_link.BOOT_WAIT_S``).
BOOT_WAIT_S = 6.0
LIVE_KIND = "mux-live-v1"

LINGER_SECONDS = 5.0
SUBSCRIBER_TTL_SECONDS = 20.0
#: How long the resident daemon lingers with no live mapping and no active
#: subscriber before idle-exiting (mirrors the resident status-monitor's own
#: idle-strike discipline, scaled for a much lower-traffic daemon).
IDLE_LINGER_S = 60.0


# ---------------------------------------------------------------------------
# Runtime paths
# ---------------------------------------------------------------------------


def lock_path(root: Path | None = None) -> Path:
    """The Manager mux-daemon's own lock/rendezvous file, mirroring
    ``agent-worktrees``' ``status-monitor.lock`` convention: sits directly
    under the runtime root, one instance per machine/installation cell."""
    return (root if root is not None else default_root()) / "mux-daemon.lock"


# ---------------------------------------------------------------------------
# Lock file read/write + rendezvous
# ---------------------------------------------------------------------------


def write_lock_data(path: Path, extra: dict) -> bool:
    """Atomically write the lock file's JSON payload. Best-effort; never
    raises. Mirrors ``agent_worktrees.locks.write_lock``'s own atomic-replace
    shape (temp file in the same directory, then ``os.replace``)."""
    payload = {
        "pid": os.getpid(),
        "start_time": process_start_time(os.getpid()),
        "created_at": time.time(),
    }
    payload.update(extra)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.replace(tmp, str(path))
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return False
        return True
    except OSError:
        return False


def read_lock_data(path: Path) -> dict | None:
    """Parse the lock file's JSON payload, or ``None`` when absent/torn."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


live_push_key = mux_daemon_live.live_push_key
mux_live_via_daemon = mux_daemon_live.mux_live_via_daemon
mux_live_with_boot = mux_daemon_live.mux_live_with_boot
_status_monitor_lock_path = mux_daemon_live.status_monitor_lock_path
_status_monitor_generation = mux_daemon_live.status_monitor_generation
_ensure_status_monitor_running = mux_daemon_live.ensure_status_monitor_running
_acquire_daemon_lease = mux_daemon_process.acquire_daemon_lease
_release_daemon_lease = mux_daemon_process.release_daemon_lease
_scrub_session_credentials = mux_daemon_process.scrub_session_credentials
_spawn_detached = mux_daemon_process.spawn_detached


def _daemon_is_live(data: dict | None) -> bool:
    return mux_daemon_process.daemon_is_live(
        data, endpoint_from_rendezvous=endpoint_from_rendezvous
    )


def _mapping_to_observation(entry: dict) -> dict:
    return {
        "project": entry["project"],
        "worktree_id": entry["worktree_id"],
        "worktree_path": entry.get("worktree_path"),
        "mux_session": entry["mux_session"],
        "session_incarnation": entry.get("session_incarnation") or "",
        "panes": list(entry.get("panes") or []),
        "attached_clients": int(entry.get("attached_clients") or 0),
        "live": bool(entry.get("live", True)),
        "mapping_revision": int(entry["mapping_revision"]),
        "observed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def _monitor_unavailable() -> dict:
    return {"applied": False, "reason": "monitor-unavailable"}


def publish_live_observation(
    entry: dict,
    *,
    ensure_monitor: bool = True,
    request_deadline_s: float = mux_daemon_live.LIVE_REQUEST_DEADLINE_S,
    boot_wait_s: float = mux_daemon_live.LIVE_BOOT_WAIT_S,
) -> dict:
    lock = _status_monitor_lock_path()
    if lock is None:
        return _monitor_unavailable()
    return mux_live_with_boot(
        read_lock_data=lambda: mux_daemon_live.read_lock_data(lock),
        ensure_monitor=_ensure_status_monitor_running if ensure_monitor else None,
        payload=_mapping_to_observation(entry),
        fallback=_monitor_unavailable,
        request_deadline_s=request_deadline_s,
        boot_wait_s=boot_wait_s,
    )


def register_managed_mapping(payload: dict, root: Path | None = None) -> dict:
    ensure_daemon_running(root)
    result = register_next_mapping(payload, root=root)
    project = payload.get("project")
    worktree_id = payload.get("worktree_id")
    if result.get("applied") and isinstance(project, str) and isinstance(worktree_id, str):
        entry = get_mapping(project, worktree_id, root=root)
        if entry is not None:
            publish_live_observation(entry, ensure_monitor=True)
    return result


def remove_managed_mapping(
    project: str,
    worktree_id: str,
    *,
    mapping_revision: int | None = None,
    mux_session: str | None = None,
    session_incarnation: str | None = None,
    root: Path | None = None,
) -> dict:
    result = remove_mapping(
        project,
        worktree_id,
        mapping_revision=mapping_revision,
        mux_session=mux_session,
        session_incarnation=session_incarnation,
        root=root,
    )
    if result.get("applied"):
        entry = get_mapping(project, worktree_id, root=root)
        if entry is not None:
            publish_live_observation(entry, ensure_monitor=True)
    return result


def _republish_live_mappings(
    registry: MuxMappingRegistry,
    *,
    ensure_monitor: bool,
) -> bool:
    published_any = False
    for entry in registry.snapshot().values():
        if not entry.get("live"):
            continue
        result = publish_live_observation(entry, ensure_monitor=ensure_monitor)
        if result.get("applied"):
            published_any = True
        else:
            return False
    return published_any or not registry.has_any_live()


def rendezvous_fields(server: CoalescingServer) -> dict:
    """Rendezvous fields namespaced so a lock file that ever gains other
    Manager-owned daemon endpoints has room to add them without collision."""
    rv = server.rendezvous()
    return {
        "manager_mux_transport": rv["transport"],
        "manager_mux_endpoint": rv["endpoint"],
        "manager_mux_token": rv["token"],
        "manager_mux_generation": rv["generation"],
    }


def endpoint_from_rendezvous(data: dict | None) -> tuple[str, int, str] | None:
    """Parse this daemon's rendezvous fields out of an already-read lock
    dict. Returns ``None`` for anything malformed or absent -- the caller's
    own correct fallback path, never an exception. Rejects a port outside
    the valid 1-65535 TCP range (a stale/malformed lock file could carry
    one), mirroring ``mux_link.endpoint_from_rendezvous``'s own round-19
    fix -- learned once, applied here from the start rather than
    rediscovered."""
    if not isinstance(data, dict):
        return None
    endpoint = data.get("manager_mux_endpoint")
    token = data.get("manager_mux_token")
    if not isinstance(endpoint, str) or not isinstance(token, str) or not token:
        return None
    host, _, port_s = endpoint.partition(":")
    if not host or not port_s:
        return None
    try:
        port = int(port_s)
    except ValueError:
        return None
    if not 0 < port < 65536:
        return None
    return host, port, token


# ---------------------------------------------------------------------------
# Status-apply (agent-worktrees -> Manager)
# ---------------------------------------------------------------------------


def apply_status_options(entry: dict, values: dict) -> bool:
    """Apply a rendered status payload's ``values`` to ``entry``'s mux
    session via ``set-option``, mirroring
    ``agent_worktrees.status_monitor_runtime._monitor_mux_set``'s own
    subprocess shape exactly (bounded timeout, best-effort). Applies every
    key regardless of an earlier failure (a caller cares about the overall
    outcome, but a single option write failing should not skip the rest);
    returns ``True`` only if every write succeeded."""
    mux_bin = entry["mux_bin"]
    session = entry["mux_session"]
    all_ok = True
    for option, value in values.items():
        try:
            result = subprocess.run(
                [mux_bin, "set-option", "-t", session, str(option), str(value)],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode != 0:
                all_ok = False
        except Exception:
            all_ok = False
    return all_ok


def _mux_session_alive(mux_bin: str, session: str) -> bool:
    """Bounded, best-effort probe of whether ``session`` genuinely still
    exists on the real mux server (``has-session``, supported by both tmux
    and psmux). Never raises; a probe failure reads as "not alive" -- the
    safe direction, since the caller's only use of this is to avoid writing
    into a session that may no longer exist."""
    try:
        result = subprocess.run(
            [mux_bin, "has-session", "-t", session],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def status_push_key(payload: dict) -> str:
    """Derive the ``CoalescingServer`` coalescing key a ``mux-status-v1``
    caller must use, from the payload itself -- mirroring
    ``agent_worktrees.mux_link._push_key``'s own rationale exactly.

    ``CoalescingServer`` coalesces concurrent requests sharing the same
    ``(kind, key)`` onto a single execution, joining a later caller onto
    the first one's in-flight result. A **status push** is not idempotent
    that way: two different renders of the same worktree's status carry two
    different ``values`` -- if a caller keyed only on ``(project,
    worktree_id)``, a newer render arriving while an older one is still
    being applied would coalesce onto that older execution and never
    actually reach ``apply_status_options`` with its own (newer) values,
    silently violating the documented last-write-wins contract (Copilot
    review finding).

    ``rendered_at`` alone is not enough (a further Copilot review finding):
    it is a timestamp, not a guaranteed-unique render identity -- two
    genuinely different payloads for the same worktree could share one
    (coarse clock resolution, or a caller bug), and would then still
    coalesce. The key therefore also folds in a canonical hash of
    ``values`` itself: two payloads only ever share a key when both
    ``rendered_at`` AND every value are identical, in which case joining
    them onto one execution is exactly the safe, intended coalescing of a
    genuine retry -- not a silent drop.

    Raises ``ValueError`` for a payload missing any of the fields this key
    depends on, so a caller finds out immediately rather than silently
    deriving a key that can still collide.
    """
    project = payload.get("project")
    worktree_id = payload.get("worktree_id")
    rendered_at = payload.get("rendered_at")
    values = payload.get("values")
    if not isinstance(project, str) or not project:
        raise ValueError("mux-status-v1 push payload missing 'project'")
    if not isinstance(worktree_id, str) or not worktree_id:
        raise ValueError("mux-status-v1 push payload missing 'worktree_id'")
    if not isinstance(rendered_at, str) or not rendered_at:
        raise ValueError("mux-status-v1 push payload missing 'rendered_at'")
    if not isinstance(values, dict):
        raise ValueError("mux-status-v1 push payload missing 'values'")
    values_digest = hashlib.sha256(
        json.dumps(values, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return (
        f"{len(project)}:{project}:{len(worktree_id)}:{worktree_id}:"
        f"{rendered_at}:{values_digest}"
    )


def _parse_rendered_at(value: str) -> float | None:
    """Best-effort ISO-8601 -> epoch-seconds parse for ordering renders.
    Returns ``None`` (never raises) for anything unparseable -- an
    unorderable render is simply always treated as "not older" (applied),
    matching this ordering check's fail-open contract: it exists to catch
    an out-of-order write, not to add a NEW way to drop a render."""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def build_compute(
    registry: MuxMappingRegistry,
    handler_tracker: "_ActiveHandlerTracker | None" = None,
    runtime: "MuxDaemonRuntime | None" = None,
) -> Callable[[str, dict], dict]:
    """Wrap the registry lookup + apply into a ``CoalescingServer``-shaped
    ``compute(kind, payload)`` callback, rejecting any request whose
    ``kind`` is not :data:`KIND`.

    ``handler_tracker``, when given, is entered immediately before
    ``apply_status_options`` and exited right after -- see
    :class:`_ActiveHandlerTracker`'s own docstring for why shutdown needs
    this (Copilot review finding).

    Callers MUST derive their coalescing ``key`` via :func:`status_push_key`
    -- see that function's own docstring for why (Copilot review finding).

    Giving distinct renders distinct coalescing keys (per
    :func:`status_push_key`) closes one race but opens another (Copilot
    review finding): ``CoalescingServer`` can now run two different
    renders for the SAME worktree fully concurrently (they no longer share
    a key), with no guarantee the older one's ``set-option`` calls finish
    first -- an older render completing AFTER a newer one would overwrite
    the mux options with stale values. This closure keeps one piece of
    **per-``build_compute``-instance** (i.e. per resident daemon process)
    in-memory state to close that: a lock per ``(project, worktree_id)`` so
    concurrent renders for the SAME worktree serialize (renders for
    DIFFERENT worktrees still proceed in parallel). The ordering fence
    itself (the last-applied ``rendered_at``) is NOT kept only in memory,
    though (a further Copilot review finding): it is persisted on the
    mapping entry via :meth:`MuxMappingRegistry.record_applied_render`, so
    it survives a daemon restart -- an in-memory-only fence would reset on
    restart and let a genuinely stale delayed render through.
    """
    worktree_locks: dict[tuple[str, str], threading.Lock] = {}
    worktree_locks_guard = threading.Lock()

    def _lock_for(key: tuple[str, str]) -> threading.Lock:
        with worktree_locks_guard:
            lock = worktree_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                worktree_locks[key] = lock
            return lock

    _control_dispatch: dict[str, Callable] = {
        mux_daemon_cutover.health_kind(): lambda rt, _p: rt.health(exclude_current_request=True),
        mux_daemon_cutover.drain_kind(): lambda rt, p: rt.drain(p),
        mux_daemon_cutover.undrain_kind(): lambda rt, _p: rt.undrain(),
        mux_daemon_cutover.shutdown_kind(): lambda rt, _p: rt.request_shutdown(),
        mux_daemon_cutover.adopt_kind(): lambda rt, _p: rt.promote(),
    }

    def _compute(kind: str, payload: dict) -> dict:
        control_fn = _control_dispatch.get(kind)
        if control_fn is not None:
            if runtime is None:
                raise ValueError("mux_daemon cutover control is unavailable")
            runtime._begin_control_request()
            try:
                return control_fn(runtime, payload)
            finally:
                runtime._end_control_request()
        if kind != KIND:
            raise ValueError(f"mux_daemon does not serve kind={kind!r}")
        if runtime is not None and not runtime.accepting_requests():
            return {"applied": False, "reason": "draining"}
        project = payload.get("project")
        worktree_id = payload.get("worktree_id")
        values = payload.get("values")
        rendered_at = payload.get("rendered_at")
        if not isinstance(project, str) or not project:
            raise ValueError("mux-status-v1 payload missing 'project'")
        if not isinstance(worktree_id, str) or not worktree_id:
            raise ValueError("mux-status-v1 payload missing 'worktree_id'")
        if not isinstance(values, dict):
            raise ValueError("mux-status-v1 payload missing 'values'")
        if not isinstance(rendered_at, str) or not rendered_at:
            # A wire caller MUST derive its coalescing key (and this
            # payload) via status_push_key, which itself requires
            # rendered_at (Copilot review finding): a caller that bypassed
            # that helper and submitted an arbitrary key/payload sits
            # outside the mux-status-v1 contract and cannot participate in
            # the ordering fence at all -- reject it here, before ever
            # looking up the mapping, rather than silently treating it as
            # unorderable (rendered_ts=None) and applying it anyway.
            raise ValueError("mux-status-v1 payload missing 'rendered_at'")
        key = (project, worktree_id)
        rendered_ts = _parse_rendered_at(rendered_at)

        with _lock_for(key):
            # Revalidate the mapping fresh, immediately before applying,
            # rather than trusting a snapshot taken earlier (Copilot review
            # finding): a concurrent remove/re-register (a separate CLI
            # process) could otherwise change or remove the mapping in the
            # window between an earlier lookup and this write, letting a
            # status push paint a removed or superseded session.
            entry = registry.get(project, worktree_id)
            if entry is None or not entry["live"]:
                return {"applied": False, "reason": "not-live"}

            # Discard a render older than the last one actually applied for
            # this mapping incarnation (Copilot review finding) -- fenced
            # by mapping_revision too, so a NEWER mapping incarnation
            # (re-registered at a higher revision, e.g. a different mux
            # session after a cutover) never inherits a stale fence from a
            # prior incarnation's last render.
            previous_raw = entry.get("last_status_rendered_at")
            previous_ts = _parse_rendered_at(previous_raw) if previous_raw else None
            if rendered_ts is not None and previous_ts is not None and rendered_ts < previous_ts:
                return {"applied": False, "reason": "stale-render"}

            # Revalidate a recovered/persisted mapping against the REAL mux
            # server rather than trusting its stored `live` bit alone
            # (Copilot review finding, Step 2's own validation
            # requirement): a mux session that was torn down while the
            # daemon was down (or since this entry was last observed) must
            # not still be treated as a valid write target. A dead session
            # invalidates the mapping so a later lookup does not repeat the
            # same probe forever.
            if not _mux_session_alive(entry["mux_bin"], entry["mux_session"]):
                # Guard with session identity too: a concurrent register()
                # can replace this mapping at the same revision between the
                # snapshot above and this call.
                registry.remove(
                    project, worktree_id,
                    mapping_revision=entry["mapping_revision"], mux_session=entry["mux_session"],
                    session_incarnation=entry.get("session_incarnation"),
                )
                tombstone = registry.get(project, worktree_id)
                if tombstone is not None:
                    mux_daemon_live.publish_live_observation(tombstone, ensure_monitor=False)
                return {"applied": False, "reason": "not-live"}

            # Recheck immediately before the actual write (Copilot review
            # finding): a concurrent CLI register/remove could still have
            # superseded this mapping in the gap between the fetch above and
            # now -- compare identity too (not just revision), since
            # register() permits an equal-revision live refresh.
            current = registry.get(project, worktree_id)
            if (
                current is None
                or not current["live"]
                or current["mapping_revision"] != entry["mapping_revision"]
                or current["mux_session"] != entry["mux_session"]
                or current.get("session_incarnation") != entry.get("session_incarnation")
            ):
                return {"applied": False, "reason": "not-live"}

            if handler_tracker is not None:
                handler_tracker.enter()
            try:
                refresh_attached_clients(registry, current)
                ok = apply_status_options(entry, values)
            finally:
                if handler_tracker is not None:
                    handler_tracker.exit()
            if ok and rendered_ts is not None:
                registry.record_applied_render(
                    project, worktree_id, rendered_at, mapping_revision=entry["mapping_revision"]
                )
            if ok:
                refreshed = registry.get(project, worktree_id)
                if refreshed is not None:
                    mux_daemon_live.publish_live_observation(refreshed, ensure_monitor=False)
            return {"applied": ok} if ok else {"applied": False, "reason": "apply-failed"}

    return _compute


def start_server(
    compute: Callable[[str, dict], dict],
    *,
    port: int = 0,
    token: str | None = None,
) -> CoalescingServer:
    return CoalescingServer(
        compute,
        linger_seconds=LINGER_SECONDS,
        subscriber_ttl=SUBSCRIBER_TTL_SECONDS,
        bind_port=port,
        token=token,
    )


# ---------------------------------------------------------------------------
# Resident daemon lifecycle
# ---------------------------------------------------------------------------


class _ActiveHandlerTracker:
    """Tracks ``mux-status-v1`` handlers currently past the point of no
    return (i.e. actually running ``apply_status_options``), so shutdown
    can fence them before releasing this daemon's single-instance lease
    (Copilot review finding): ``CoalescingServer.close()`` stops accepting
    new connections but does not join already-running handler threads -- a
    handler that had already passed its liveness/revision checks could
    still be mid-apply when ``close()`` returns. Without fencing,
    ``run_daemon_foreground`` would then release the lease and let a
    replacement daemon start while that stale handler is still writing,
    letting it overwrite the replacement's own newer status."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._count = 0
        self._drained = threading.Event()
        self._drained.set()

    def enter(self) -> None:
        with self._lock:
            self._count += 1
            self._drained.clear()

    def exit(self) -> None:
        with self._lock:
            self._count -= 1
            if self._count <= 0:
                self._drained.set()

    def wait_for_drain(self, timeout: float) -> bool:
        return self._drained.wait(timeout=timeout)


class MuxDaemonRuntime:
    """Owns the mux-companion daemon's whole in-process lifecycle: the
    coalescing server plus its registry handle -- mirrors
    ``agent_worktrees.mux_link.InProcessRuntime`` structurally."""

    #: Bound on how long shutdown waits for an in-flight handler to finish
    #: its apply before closing the server anyway (never hang shutdown
    #: forever on a stuck subprocess call).
    SHUTDOWN_DRAIN_TIMEOUT_S = 20.0

    def __init__(
        self,
        registry_path_: Path,
        *,
        root: Path | None = None,
        passive: bool = False,
        listen_port: int | None = None,
    ) -> None:
        self.server: CoalescingServer | None = None
        self.registry = MuxMappingRegistry(registry_path_)
        self.handler_tracker = _ActiveHandlerTracker()
        self.root = root if root is not None else default_root()
        self.passive = passive
        self.listen_port = listen_port or 0
        self.admissions_open = not passive
        self.draining = False
        self.republish_enabled = True
        self.shutdown_requested = False
        self.retire_requested = False
        self._loop_mutation_active = False
        self._self_retire_generation: int | None = None
        self._self_retire_confirms = 0
        self._self_retire_confirmations = 2
        # Concurrently in-flight cutover-control requests; see health()'s
        # docstring.
        self._control_requests_in_flight = 0
        self._control_requests_lock = threading.Lock()

    def start(self) -> None:
        try:
            server = start_server(
                build_compute(self.registry, self.handler_tracker, self),
                port=self.listen_port,
                token=mux_daemon_cutover.load_or_create_control_token(self.root),
            )
            self.server = server
            server.start()
        except Exception:
            self.shutdown()

    @property
    def port(self) -> int | None:
        if self.server is None:
            return None
        rendezvous = self.server.rendezvous()
        endpoint = str(rendezvous["endpoint"])
        _, _, port_s = endpoint.rpartition(":")
        try:
            return int(port_s)
        except ValueError:
            return None

    def lock_extra(self) -> dict:
        if self.server is None:
            return {}
        return rendezvous_fields(self.server)

    def accepting_requests(self) -> bool:
        return self.admissions_open and not self.draining

    def loop_mutation(self):
        class _LoopMutation:
            def __init__(self, owner: MuxDaemonRuntime) -> None:
                self._owner = owner

            def __enter__(self):
                self._owner._loop_mutation_active = True

            def __exit__(self, exc_type, exc, tb):
                self._owner._loop_mutation_active = False
                return False

        return _LoopMutation(self)

    def _is_drained(self, *, exclude_current_request: bool = False) -> bool:
        if self.server is None:
            return True
        active_handlers = self.server.active_handler_count()
        if exclude_current_request and active_handlers > 0:
            active_handlers -= 1
        return (
            active_handlers == 0
            and self.handler_tracker.wait_for_drain(0.0)
            and not self._loop_mutation_active
        )

    def begin_drain(self) -> None:
        self.draining = True
        self.republish_enabled = False

    def begin_retire(self) -> None:
        self.begin_drain()
        self.retire_requested = True

    def _begin_control_request(self) -> None:
        with self._control_requests_lock:
            self._control_requests_in_flight += 1

    def _end_control_request(self) -> None:
        with self._control_requests_lock:
            self._control_requests_in_flight -= 1

    def health(self, *, exclude_current_request: bool = False) -> dict:
        """Report this daemon's own idle/load state for ``daemons status``.

        Over the real wire, every cutover-control request (this probe
        included) is itself an accepted handler/touched subscriber for its
        own duration, so a naive read of ``active_handler_count()``/
        ``subscriber_count()`` always includes every in-flight one. Set
        only by the wire dispatch (``_control_requests_in_flight``),
        ``exclude_current_request`` excludes that whole count, not a flat
        one; a direct in-process call leaves it ``False`` (already accurate).
        """
        from . import __version__

        attached_clients = self.server.subscriber_count() if self.server is not None else 0
        active_handlers = self.server.active_handler_count() if self.server is not None else 0
        if exclude_current_request:
            in_flight = self._control_requests_in_flight
            attached_clients = max(0, attached_clients - in_flight)
            active_handlers = max(0, active_handlers - in_flight)
        return {
            "status": "draining" if self.draining else "ready",
            "version": __version__,
            "attached_clients": attached_clients,
            "busy": active_handlers > 0,
        }

    def promote(self) -> dict:
        self.admissions_open = True
        return {"adopted": True}

    def drain(self, payload: dict) -> dict:
        self.begin_drain()
        timeout = float(payload.get("timeout", 0.0) or 0.0)
        poll = max(0.01, float(payload.get("poll", 0.05) or 0.05))
        force = bool(payload.get("force"))
        deadline = time.time() + timeout
        while not self._is_drained(exclude_current_request=True) and time.time() < deadline:
            time.sleep(poll)
        drained = self._is_drained(exclude_current_request=True)
        if not drained and force:
            self.shutdown_requested = True
            return {"drained": True, "clean": False, "forced": True, "busy_sessions": ["busy"]}
        return {
            "drained": drained,
            "clean": drained,
            "forced": False,
            "busy_sessions": [] if drained else ["busy"],
        }

    def undrain(self) -> dict:
        self.draining = False
        self.republish_enabled = True
        self.admissions_open = True
        self.retire_requested = False
        self.shutdown_requested = False
        self._self_retire_confirms = 0
        return {"draining": False}

    def request_shutdown(self) -> dict:
        self.shutdown_requested = True
        return {"shutdown": True}

    def sync_self_retire(self) -> None:
        if self._self_retire_generation is None:
            generation = mux_daemon_cutover.active_generation_for_pid(os.getpid(), root=self.root)
            if generation is not None:
                self._self_retire_generation = generation
        if self._self_retire_generation is None:
            return
        superseded = mux_daemon_cutover.is_superseded(
            self.root, os.getpid(), self._self_retire_generation
        )
        if superseded:
            self._self_retire_confirms += 1
            if self._self_retire_confirms >= self._self_retire_confirmations:
                self.begin_retire()
        else:
            self._self_retire_confirms = 0

    def should_exit(self) -> bool:
        return self.shutdown_requested or (self.retire_requested and self._is_drained())

    def has_active_demand(self) -> bool:
        if self.server is not None and self.server.subscriber_count() > 0:
            return True
        if self.server is not None and self.server.active_handler_count() > 0:
            return True
        return self.registry.has_any_live()

    def shutdown(self) -> None:
        if self.server is not None:
            self.handler_tracker.wait_for_drain(self.SHUTDOWN_DRAIN_TIMEOUT_S)
            self.server.close()
        self.server = None


def run_daemon_foreground(
    root: Path | None = None,
    *,
    passive: bool = False,
    listen_port: int | None = None,
    idle_after_s: float = IDLE_LINGER_S,
    poll_interval_s: float = 1.0,
    max_iterations: int | None = None,
    backstop_interval_s: float = LIVE_MAPPING_BACKSTOP_INTERVAL_S,
) -> int:
    """The resident daemon's own loop: acquire this daemon's single-
    instance lease, start the server, publish the lock file, then
    idle-exit after ``idle_after_s`` seconds with no live mapping and no
    active subscriber. ``max_iterations`` is test-only (bounds the loop
    instead of relying on the idle timer alone).

    A *passive* generation (spawned only by the cutover orchestrator) is the one
    sanctioned exception to the single-active lease: it starts beside the active
    daemon without taking the lease or publishing the lock, becomes routed active
    through the ZDD routing table, then self-promotes by claiming the lease once
    the old active retires.
    """
    resolved_root = root if root is not None else default_root()
    lock = lock_path(resolved_root)
    lease = None if passive else _acquire_daemon_lease(resolved_root)
    if not passive and lease is None:
        # Another instance already holds the single-instance lease --
        # stand down rather than starting a second daemon.
        return 0
    try:
        runtime = MuxDaemonRuntime(
            registry_path(resolved_root),
            root=resolved_root,
            passive=passive,
            listen_port=listen_port,
        )
        runtime.start()
        if runtime.server is None or runtime.port is None:
            return 1
        active_version = current_version(resolved_root)
        if lease is not None:
            active_endpoint = mux_daemon_cutover.publish_route(
                runtime.port,
                root=resolved_root,
                pid=os.getpid(),
                version=active_version,
            )
            runtime._self_retire_generation = int(active_endpoint.generation)
        idle_since: float | None = None
        iterations = 0
        published_monitor_generation: str | None = None
        last_live_republish_at: float | None = None
        try:
            while True:
                if lease is None:
                    generation = mux_daemon_cutover.active_generation_for_pid(
                        os.getpid(), root=resolved_root
                    )
                    if generation is not None:
                        promoted = _acquire_daemon_lease(resolved_root)
                        if promoted is not None:
                            lease = promoted
                            runtime.passive = False
                            runtime._self_retire_generation = generation
                if lease is not None:
                    write_lock_data(lock, runtime.lock_extra())
                runtime.sync_self_retire()
                status_monitor_lock = _status_monitor_lock_path()
                status_monitor_data = (
                    mux_daemon_live.read_lock_data(status_monitor_lock)
                    if status_monitor_lock is not None
                    else None
                )
                status_monitor_generation = _status_monitor_generation(status_monitor_data)
                now = time.monotonic()  # immune to clock steps; diffed only against itself
                if (
                    runtime.republish_enabled
                    and
                    live_mapping_republish_due(
                        status_monitor_generation, published_monitor_generation,
                        last_live_republish_at, now, backstop_interval_s,
                    )
                    and runtime.registry.has_any_live()
                ):
                    with runtime.loop_mutation():
                        republished = _republish_live_mappings(
                            runtime.registry,
                            ensure_monitor=False,
                        )
                    if republished:
                        last_live_republish_at = now
                        published_monitor_generation = status_monitor_generation
                if runtime.should_exit():
                    break
                if runtime.has_active_demand():
                    idle_since = None
                elif idle_since is None:
                    idle_since = time.time()
                elif time.time() - idle_since >= idle_after_s:
                    break
                iterations += 1
                if max_iterations is not None and iterations >= max_iterations:
                    break
                time.sleep(poll_interval_s)
        finally:
            runtime.shutdown()
            mux_daemon_cutover.clear_route_if_owner(os.getpid(), root=resolved_root)
            if lease is not None:
                data = read_lock_data(lock)
                if isinstance(data, dict) and data.get("pid") == os.getpid():
                    try:
                        lock.unlink()
                    except OSError:
                        pass
        return 0
    finally:
        if lease is not None:
            _release_daemon_lease(lease)

def ensure_daemon_running(
    root: Path | None = None, *, boot_wait_s: float = BOOT_WAIT_S
) -> bool:
    """Start the resident Manager mux daemon unless one is already live.
    Returns whether a current daemon is believed running (already live, or
    freshly spawned within ``boot_wait_s``). Idempotent + cheap: a live
    daemon is a no-op.

    Does not itself hold any lock around the check-then-spawn sequence --
    correctness no longer depends on that (see
    :func:`run_daemon_foreground`'s own docstring): if two callers race
    here and both spawn a child, both children race for the SAME
    single-instance lease and only one actually starts a server, so this
    function stays simple and never risks deadlocking against the very
    child it just spawned."""
    resolved_root = root if root is not None else default_root()
    lock = lock_path(resolved_root)
    if _daemon_is_live(read_lock_data(lock)):
        return True
    # Propagate the resolved root to the child (Copilot review finding):
    # without this, a caller-supplied non-default `root` (e.g. a test's
    # scratch directory) would wait on a lock under that root while the
    # spawned `mux-daemon run` resolved its own default installation root
    # instead, so this helper would report failure despite successfully
    # spawning a daemon.
    argv = mux_daemon_process.detached_child_argv(root)
    if not _spawn_detached(argv):
        return False
    started = time.time()
    while time.time() - started < boot_wait_s:
        time.sleep(0.1)
        if _daemon_is_live(read_lock_data(lock)):
            return True
    return False

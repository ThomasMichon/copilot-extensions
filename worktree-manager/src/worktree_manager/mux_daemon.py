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
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from work_coalescing_singleton import CoalescingServer
from work_coalescing_singleton import client as wcs_client

from .mux_mapping_registry import (
    MuxMappingRegistry,
    _try_lock_file_once,
    _unlock_file,
    get_mapping,
    registry_path,
    register_mapping,
    register_next_mapping,
    remove_mapping,
)
from .self_install import default_root

__all__ = [
    "get_mapping",
    "register_mapping",
    "remove_mapping",
    "registry_path",
    "MuxMappingRegistry",
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
LIVE_REQUEST_DEADLINE_S = 2.0
LIVE_BOOT_WAIT_S = 6.0

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


def _agent_worktrees_root() -> Path | None:
    from . import agent_plugin_runtime

    slot = agent_plugin_runtime.resolve_installed_plugin_slot("agent-worktrees")
    if slot is None:
        return None
    return slot.parent.parent


def _status_monitor_lock_path() -> Path | None:
    root = _agent_worktrees_root()
    return None if root is None else root / "status-monitor.lock"


def _status_monitor_endpoint_from_rendezvous(data: dict | None) -> tuple[str, int, str] | None:
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
    if not 0 < port < 65536:
        return None
    return host, port, token


def _ensure_status_monitor_running() -> bool:
    from . import engine_client

    base = engine_client.engine_base_command()
    if not base:
        return False
    kwargs: dict = {
        "capture_output": True,
        "text": True,
        "timeout": 30,
        "check": False,
        "env": engine_client._engine_environment(),
        "stdin": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run([*base, "status-monitor-restart"], **kwargs)
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def live_push_key(payload: dict) -> str:
    project = payload.get("project")
    worktree_id = payload.get("worktree_id")
    revision = payload.get("mapping_revision")
    if not isinstance(project, str) or not project:
        raise ValueError("mux-live-v1 payload missing 'project'")
    if not isinstance(worktree_id, str) or not worktree_id:
        raise ValueError("mux-live-v1 payload missing 'worktree_id'")
    if isinstance(revision, bool) or not isinstance(revision, int):
        raise ValueError("mux-live-v1 payload missing 'mapping_revision'")
    return f"{len(project)}:{project}:{len(worktree_id)}:{worktree_id}:{revision}"


def mux_live_via_daemon(
    lock_data: dict | None,
    *,
    payload: dict,
    fallback: Callable[[], dict],
    request_deadline_s: float = LIVE_REQUEST_DEADLINE_S,
) -> dict:
    endpoint = _status_monitor_endpoint_from_rendezvous(lock_data)
    if endpoint is None:
        return fallback()
    key = live_push_key(payload)
    host, port, token = endpoint
    client_id = wcs_client.new_client_id()
    try:
        return wcs_client.request(
            host,
            port,
            token,
            kind=LIVE_KIND,
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
    request_deadline_s: float = LIVE_REQUEST_DEADLINE_S,
    boot_wait_s: float = LIVE_BOOT_WAIT_S,
    poll_interval_s: float = 0.1,
) -> dict:
    started = time.time()

    def _dial() -> tuple[str, int, str] | None:
        return _status_monitor_endpoint_from_rendezvous(read_lock_data())

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
            kind=LIVE_KIND,
            key=live_push_key(payload),
            payload=payload,
            request_deadline_s=request_deadline_s,
            client_id=client_id,
        )
    except wcs_client.DaemonUnavailable:
        return fallback()
    finally:
        wcs_client.release(host, port, token, client_id, timeout=request_deadline_s)


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
    request_deadline_s: float = LIVE_REQUEST_DEADLINE_S,
    boot_wait_s: float = LIVE_BOOT_WAIT_S,
) -> dict:
    lock = _status_monitor_lock_path()
    if lock is None:
        return _monitor_unavailable()
    return mux_live_with_boot(
        read_lock_data=lambda: read_lock_data(lock),
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
    root: Path | None = None,
) -> dict:
    result = remove_mapping(project, worktree_id, mapping_revision=mapping_revision, root=root)
    if result.get("applied"):
        entry = get_mapping(project, worktree_id, root=root)
        if entry is not None:
            publish_live_observation(entry, ensure_monitor=True)
    return result


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
    registry: MuxMappingRegistry, handler_tracker: "_ActiveHandlerTracker | None" = None
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

    def _compute(kind: str, payload: dict) -> dict:
        if kind != KIND:
            raise ValueError(f"mux_daemon does not serve kind={kind!r}")
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
                registry.remove(project, worktree_id, mapping_revision=entry["mapping_revision"])
                tombstone = registry.get(project, worktree_id)
                if tombstone is not None:
                    publish_live_observation(tombstone, ensure_monitor=False)
                return {"applied": False, "reason": "not-live"}

            # Recheck immediately before the actual write (Copilot review
            # finding): the interprocess lock above is only held for each
            # individual registry operation, not across this whole
            # probe-then-apply sequence, so a concurrent CLI
            # register/remove could still have superseded this mapping in
            # the gap between the fetch above and now. A changed
            # mapping_revision (or the mapping going missing/not-live)
            # means this entry is no longer the current target.
            current = registry.get(project, worktree_id)
            if (
                current is None
                or not current["live"]
                or current["mapping_revision"] != entry["mapping_revision"]
            ):
                return {"applied": False, "reason": "not-live"}

            if handler_tracker is not None:
                handler_tracker.enter()
            try:
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
                    publish_live_observation(refreshed, ensure_monitor=False)
            return {"applied": ok} if ok else {"applied": False, "reason": "apply-failed"}

    return _compute


def start_server(compute: Callable[[str, dict], dict]) -> CoalescingServer:
    return CoalescingServer(
        compute,
        linger_seconds=LINGER_SECONDS,
        subscriber_ttl=SUBSCRIBER_TTL_SECONDS,
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

    def __init__(self, registry_path_: Path) -> None:
        self.server: CoalescingServer | None = None
        self.registry = MuxMappingRegistry(registry_path_)
        self.handler_tracker = _ActiveHandlerTracker()

    def start(self) -> None:
        try:
            server = start_server(build_compute(self.registry, self.handler_tracker))
            self.server = server
            server.start()
        except Exception:
            self.shutdown()

    def lock_extra(self) -> dict:
        if self.server is None:
            return {}
        return rendezvous_fields(self.server)

    def has_active_demand(self) -> bool:
        if self.server is not None and self.server.subscriber_count() > 0:
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
    idle_after_s: float = IDLE_LINGER_S,
    poll_interval_s: float = 1.0,
    max_iterations: int | None = None,
) -> int:
    """The resident daemon's own loop: acquire this daemon's single-
    instance lease, start the server, publish the lock file, then
    idle-exit after ``idle_after_s`` seconds with no live mapping and no
    active subscriber. ``max_iterations`` is test-only (bounds the loop
    instead of relying on the idle timer alone).

    Acquiring :data:`_spawn_lock_path` as a lease held for this daemon's
    ENTIRE lifetime (not merely at startup) is a deliberate design choice
    (Copilot review finding): a briefly-held "check, then spawn" lock (as
    an earlier revision of this function used) only protects callers that
    go through it -- a direct ``mux-daemon run`` invocation (manual, or a
    second supervisor) bypasses that check-then-spawn dance entirely, and
    even between two lock-protected operations (an old daemon's cleanup
    and a new one's startup publish) there is still a gap where both could
    believe the lock file is theirs to write. Holding the SAME lease
    continuously instead makes mutual exclusion structural rather than
    timing-dependent: only one process can ever hold it, so a second
    ``run_daemon_foreground`` call (via any path) sees it already held and
    stands down immediately without ever starting a server or touching the
    rendezvous file -- and this process's own exit-time cleanup can remove
    the rendezvous unconditionally, since holding the lease the whole time
    already proves no other daemon could have published one.
    """
    resolved_root = root if root is not None else default_root()
    lease = _acquire_daemon_lease(resolved_root)
    if lease is None:
        # Another instance already holds the single-instance lease --
        # stand down rather than starting a second daemon.
        return 0
    try:
        lock = lock_path(resolved_root)
        runtime = MuxDaemonRuntime(registry_path(resolved_root))
        runtime.start()
        if runtime.server is None:
            return 1
        idle_since: float | None = None
        iterations = 0
        try:
            while True:
                write_lock_data(lock, runtime.lock_extra())
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
            try:
                lock.unlink()
            except OSError:
                pass
        return 0
    finally:
        _release_daemon_lease(lease)


def _daemon_is_live(data: dict | None) -> bool:
    """Prove liveness by actually reaching the endpoint (a real
    subscribe/release round trip), not merely by trusting the lock file's
    presence -- a crashed daemon leaves a stale-but-present lock. Cheaper
    and more direct than a PID/start-time liveness check: it proves the
    thing that actually matters (the daemon answers), not just that some
    process with a recorded pid still exists."""
    endpoint = endpoint_from_rendezvous(data)
    if endpoint is None:
        return False
    host, port, token = endpoint
    client_id = wcs_client.new_client_id()
    try:
        wcs_client.subscribe(host, port, token, client_id, timeout=2.0)
    except wcs_client.DaemonUnavailable:
        return False
    finally:
        wcs_client.release(host, port, token, client_id, timeout=2.0)
    return True


def _spawn_lock_path(root: Path) -> Path:
    return root / "mux-daemon.spawn.lock"


def _acquire_daemon_lease(root: Path):
    """Attempt this daemon's single-instance lease: a single non-blocking
    cross-process file-lock attempt, held for the daemon's entire lifetime
    (see :func:`run_daemon_foreground`'s own docstring for why). Returns an
    open file handle the caller must eventually pass to
    :func:`_release_daemon_lease`, or ``None`` if another instance already
    holds it."""
    root.mkdir(parents=True, exist_ok=True)
    fh = open(_spawn_lock_path(root), "a+b")
    if _try_lock_file_once(fh):
        return fh
    fh.close()
    return None


def _release_daemon_lease(fh) -> None:
    try:
        _unlock_file(fh)
    except OSError:
        pass
    fh.close()


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
    argv = [sys.executable, "-m", "worktree_manager", "mux-daemon", "run"]
    if root is not None:
        argv.append(f"--root={root}")
    if not _spawn_detached(argv):
        return False
    started = time.time()
    while time.time() - started < boot_wait_s:
        time.sleep(0.1)
        if _daemon_is_live(read_lock_data(lock)):
            return True
    return False


def _spawn_detached(argv: list[str]) -> bool:
    """Spawn a survivable background daemon. Best-effort; never raises.

    Kept minimal deliberately: unlike
    ``agent_worktrees.status_monitor_runtime._spawn_detached``, this does not
    yet special-case a windowless ``pythonw`` trampoline or root the child
    at HOME -- this daemon has no recurring console-child concern (it opens
    no further terminal windows the way the resident status-monitor's
    hook/classify servers' callers can) and is not yet on any production
    launch path (Step 2's own explicit scope). Revisit if/when Step 3 wires
    this into a UX-visible launch flow."""
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"GH_TOKEN", "GITHUB_TOKEN", "AGENT_WORKTREES_AHP_AUTH_TOKEN"}
    }
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": env,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(argv, **kwargs)  # detached: fixed, trusted argv
        return True
    except Exception:
        return False

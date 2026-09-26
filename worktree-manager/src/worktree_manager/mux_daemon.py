"""Worktree Manager mux-companion daemon (Phase 3b Slice 2 Sub-slice 3 Step 2
-- ``worktree-manager-control-plane`` effort).

Still off the main launch path: this module adds the Manager-side resident
daemon, its own runtime registry, and internal register/remove/ensure
helpers, per
``efforts/active/worktree-manager-control-plane/phase-3b-substatus-monitor-relocation.md``'s
"Ordered implementation steps" (Step 2). Nothing here is yet called by
``launch-session.ps1``/``launch-session.sh`` or the production Picker's real
launch/join/restore/remux actions -- that cutover is Step 3. This step only
proves the daemon's own lifecycle (start/idle-exit/restart recovery) and the
mapping registry's persistence, independent of any real caller.

Mirrors ``agent_worktrees.mux_link`` structurally (same
``work_coalescing_singleton.CoalescingServer`` shape, same lockfile-rendezvous
pattern, same cross-process advisory file lock around the mapping registry's
read-reconcile-write critical section) but is intentionally NOT an import of
that module -- ``mux_companion.py``'s own docstring documents the process-
boundary rule this package follows: Worktree Manager never imports
``agent_worktrees`` in-process, only reaches it through a subprocess/wire
boundary. The two directions of this slice's IPC contract are therefore
implemented independently on each side:

* **Manager -> agent-worktrees** (``mux-live-v1``, Step 3's job): pushed via
  ``agent_worktrees.mux_link.mux_live_with_boot``, called from *this* module's
  future Step-3 wiring -- not implemented yet.
* **agent-worktrees -> Manager** (``mux-status-v1``, this module): THIS
  daemon is the wire *server* for that kind. :func:`build_compute` is the
  ``compute(kind, payload)`` callback a ``CoalescingServer`` wraps, mirroring
  ``mux_link.build_compute`` exactly.

The mapping registry (:class:`MuxMappingRegistry`) is deliberately **not**
owned in-memory by one long-lived daemon process the way
``agent_worktrees.mux_link.ManagedMuxCache`` owns its cache: register/remove
calls come from short-lived CLI invocations (eventually the launch scripts
themselves), not from a process that stays alive for the mapping's whole
lifetime. The registry is therefore always disk-backed, with the same
cross-process advisory lock + read-reconcile-write pattern
``ManagedMuxCache.apply_observation`` uses, so any process (a register/remove
CLI call, or the resident daemon's own request handler) sees a consistent
view. "Recovery-on-restart" is therefore automatic: there is no separate
warm-load step because there is no separate in-memory state to warm.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from work_coalescing_singleton import CoalescingServer
from work_coalescing_singleton import client as wcs_client

from .self_install import default_root

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

LINGER_SECONDS = 5.0
SUBSCRIBER_TTL_SECONDS = 20.0
#: How long the resident daemon lingers with no live mapping and no active
#: subscriber before idle-exiting (mirrors the resident status-monitor's own
#: idle-strike discipline, scaled for a much lower-traffic daemon).
IDLE_LINGER_S = 60.0

_REQUIRED_MAPPING_STR_FIELDS = ("project", "worktree_id", "mux_session", "mux_bin")

# ---------------------------------------------------------------------------
# Cross-process advisory file lock (mirrors agent_worktrees.mux_link's own
# copy exactly -- duplicated, not imported, per the process-boundary rule
# this package follows).
# ---------------------------------------------------------------------------

_LOCK_ACQUIRE_TIMEOUT_S = 30.0
_LOCK_RETRY_INTERVAL_S = 0.2

if sys.platform == "win32":
    import msvcrt

    def _lock_file(fh) -> None:
        fh.seek(0, os.SEEK_END)
        if fh.tell() == 0:
            fh.write(b"\0")
            fh.flush()
        deadline = time.time() + _LOCK_ACQUIRE_TIMEOUT_S
        while True:
            fh.seek(0)
            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError:
                if time.time() >= deadline:
                    raise
                time.sleep(_LOCK_RETRY_INTERVAL_S)

    def _unlock_file(fh) -> None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock_file(fh) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)

    def _unlock_file(fh) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# Runtime paths
# ---------------------------------------------------------------------------


def lock_path(root: Path | None = None) -> Path:
    """The Manager mux-daemon's own lock/rendezvous file, mirroring
    ``agent-worktrees``' ``status-monitor.lock`` convention: sits directly
    under the runtime root, one instance per machine/installation cell."""
    return (root if root is not None else default_root()) / "mux-daemon.lock"


def registry_path(root: Path | None = None) -> Path:
    """The Manager-owned per-worktree mapping registry snapshot."""
    return (root if root is not None else default_root()) / "mux-mapping.json"


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
# Mapping registry
# ---------------------------------------------------------------------------


def _normalize_mapping_entry(payload: dict) -> dict:
    """Validate + normalize one mapping-registration payload. Raises
    ``ValueError`` for anything structurally malformed. Shape mirrors the
    documented ``mux-live-v1`` payload plus ``mux_bin`` -- the one field
    that is Manager-internal (the launcher already resolved and used a
    specific mux binary; recording it here means the daemon's own status-
    apply logic never needs a second, possibly-divergent resolution)."""
    for field in _REQUIRED_MAPPING_STR_FIELDS:
        value = payload.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError(f"mux mapping entry missing '{field}'")
    revision_raw = payload.get("mapping_revision")
    if isinstance(revision_raw, bool) or not isinstance(revision_raw, int):
        raise ValueError("mux mapping entry requires an integer mapping_revision")
    if revision_raw < 0:
        raise ValueError("mux mapping entry mapping_revision must be non-negative")
    live_raw = payload.get("live", True)
    if not isinstance(live_raw, bool):
        raise ValueError("mux mapping entry 'live' must be a boolean")
    worktree_path = payload.get("worktree_path")
    session_incarnation = payload.get("session_incarnation")
    if not isinstance(session_incarnation, str):
        session_incarnation = ""
    attached_clients = payload.get("attached_clients")
    if isinstance(attached_clients, bool) or not isinstance(attached_clients, int):
        attached_clients = 0
    panes = _normalize_panes(payload.get("panes"))
    observed_at = payload.get("observed_at")
    if not isinstance(observed_at, str) or not observed_at:
        observed_at = datetime.now(timezone.utc).isoformat()
    return {
        "project": payload["project"],
        "worktree_id": payload["worktree_id"],
        "worktree_path": worktree_path if isinstance(worktree_path, str) else None,
        "mux_session": payload["mux_session"],
        "mux_bin": payload["mux_bin"],
        "session_incarnation": session_incarnation,
        "panes": panes,
        "attached_clients": attached_clients,
        "mapping_revision": revision_raw,
        "live": live_raw,
        "observed_at": observed_at,
    }


def _normalize_panes(raw) -> list[dict]:
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


class MuxMappingRegistry:
    """The Manager-owned ``worktree_id ⇄ mux session/pane(s)`` mapping,
    always disk-backed (see this module's own docstring for why -- no
    long-lived in-memory owner). Keyed by ``(project, worktree_id)``, with
    the same monotonic-``mapping_revision`` guard
    ``agent_worktrees.mux_link.ManagedMuxCache`` uses so an out-of-order
    register/remove can never regress a newer mapping."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def _lock_file_path(self) -> Path:
        return self._path.with_suffix(self._path.suffix + ".lock")

    def _interprocess_lock(self):
        import contextlib

        @contextlib.contextmanager
        def _cm():
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._lock_file_path(), "a+b") as fh:
                _lock_file(fh)
                try:
                    yield
                finally:
                    _unlock_file(fh)

        return _cm()

    def _read_all(self) -> dict[tuple[str, str], dict]:
        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, ValueError):
            return {}
        if not isinstance(data, list):
            return {}
        entries: dict[tuple[str, str], dict] = {}
        for entry in data:
            if not isinstance(entry, dict):
                continue
            try:
                normalized = _normalize_mapping_entry(entry)
            except ValueError:
                continue
            key = (normalized["project"], normalized["worktree_id"])
            existing = entries.get(key)
            if existing is None or normalized["mapping_revision"] >= existing["mapping_revision"]:
                entries[key] = normalized
        return entries

    def _write_all(self, entries: dict[tuple[str, str], dict]) -> None:
        payload = list(entries.values())
        tmp_fd, tmp_path = tempfile.mkstemp(
            prefix=self._path.name + ".", suffix=".tmp", dir=str(self._path.parent)
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(payload, f)
            os.replace(tmp_path, str(self._path))
        except OSError:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def register(self, payload: dict) -> dict:
        """Validate, apply the monotonic-revision guard, and persist one
        mapping entry. Returns ``{"applied": bool, ...}``."""
        entry = _normalize_mapping_entry(payload)
        key = (entry["project"], entry["worktree_id"])
        with self._interprocess_lock():
            entries = self._read_all()
            current = entries.get(key)
            if current is not None and entry["mapping_revision"] < current["mapping_revision"]:
                return {
                    "applied": False,
                    "reason": "stale_revision",
                    "current_revision": current["mapping_revision"],
                }
            entries[key] = entry
            self._write_all(entries)
        return {"applied": True, "revision": entry["mapping_revision"]}

    def remove(
        self, project: str, worktree_id: str, *, mapping_revision: int | None = None
    ) -> dict:
        """Tombstone (``live: false``) rather than delete a mapping entry
        (Copilot review finding): deleting it outright loses the
        monotonic-revision high-water mark the wire contract depends on --
        register revision 5, remove at revision 6, then a delayed register
        at revision 5 arrives: with the entry gone, ``register()`` would
        see no current entry and accept the stale mapping, resurrecting it.
        Persisting a tombstone at the removal's own revision keeps the
        guard intact. If ``mapping_revision`` is given, a stored entry at a
        *newer* revision is left in place rather than tombstoned (an
        out-of-order remove arriving after a newer register must not
        clobber it).

        Two further fencing cases (Copilot review findings):

        * A *revisioned* remove for a key with no current entry still
          persists a durable tombstone at that revision -- otherwise a
          remove(6) racing ahead of (or following snapshot loss of) an
          eventual register(5) would leave nothing behind to reject that
          now-stale register against.
        * An *unversioned* remove (``mapping_revision=None``, the CLI's own
          default) bumps the tombstone's revision one past the current
          entry's, rather than keeping it unchanged -- ``register()``'s own
          guard only rejects a revision strictly *less than* current, so a
          delayed ``live: true`` update at the SAME (unbumped) revision
          would otherwise still be accepted, resurrecting the mapping this
          call just removed.
        """
        key = (project, worktree_id)
        with self._interprocess_lock():
            entries = self._read_all()
            current = entries.get(key)
            if current is None:
                if mapping_revision is None:
                    return {"applied": True, "reason": "absent"}
                tombstone = _normalize_mapping_entry(
                    {
                        "project": project,
                        "worktree_id": worktree_id,
                        "mux_session": "(tombstone)",
                        "mux_bin": "(tombstone)",
                        "mapping_revision": mapping_revision,
                        "live": False,
                    }
                )
                entries[key] = tombstone
                self._write_all(entries)
                return {"applied": True}
            if mapping_revision is not None and mapping_revision < current["mapping_revision"]:
                return {
                    "applied": False,
                    "reason": "stale_revision",
                    "current_revision": current["mapping_revision"],
                }
            tombstone = dict(current)
            tombstone["live"] = False
            tombstone["mapping_revision"] = (
                mapping_revision if mapping_revision is not None else current["mapping_revision"] + 1
            )
            entries[key] = tombstone
            self._write_all(entries)
        return {"applied": True}

    def get(self, project: str, worktree_id: str) -> dict | None:
        with self._interprocess_lock():
            entries = self._read_all()
        entry = entries.get((project, worktree_id))
        return dict(entry) if entry is not None else None

    def snapshot(self) -> dict[tuple[str, str], dict]:
        with self._interprocess_lock():
            entries = self._read_all()
        return {key: dict(entry) for key, entry in entries.items()}

    def has_any_live(self) -> bool:
        with self._interprocess_lock():
            entries = self._read_all()
        return any(e["live"] for e in entries.values())


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
    review finding). Including ``rendered_at`` in the key gives every
    distinct render its own coalescing slot -- ``set-option`` itself is
    idempotent, so re-applying an identical render that a retry coalesces
    onto is harmless; it is only two *different* renders coalescing that
    would drop one.

    Raises ``ValueError`` for a payload missing any of the three fields
    this key depends on, so a caller finds out immediately rather than
    silently deriving a key that can still collide.
    """
    project = payload.get("project")
    worktree_id = payload.get("worktree_id")
    rendered_at = payload.get("rendered_at")
    if not isinstance(project, str) or not project:
        raise ValueError("mux-status-v1 push payload missing 'project'")
    if not isinstance(worktree_id, str) or not worktree_id:
        raise ValueError("mux-status-v1 push payload missing 'worktree_id'")
    if not isinstance(rendered_at, str) or not rendered_at:
        raise ValueError("mux-status-v1 push payload missing 'rendered_at'")
    return f"{len(project)}:{project}:{len(worktree_id)}:{worktree_id}:{rendered_at}"


def build_compute(registry: MuxMappingRegistry) -> Callable[[str, dict], dict]:
    """Wrap the registry lookup + apply into a ``CoalescingServer``-shaped
    ``compute(kind, payload)`` callback, rejecting any request whose
    ``kind`` is not :data:`KIND`.

    Callers MUST derive their coalescing ``key`` via :func:`status_push_key`
    -- see that function's own docstring for why (Copilot review finding).
    """

    def _compute(kind: str, payload: dict) -> dict:
        if kind != KIND:
            raise ValueError(f"mux_daemon does not serve kind={kind!r}")
        project = payload.get("project")
        worktree_id = payload.get("worktree_id")
        values = payload.get("values")
        if not isinstance(project, str) or not project:
            raise ValueError("mux-status-v1 payload missing 'project'")
        if not isinstance(worktree_id, str) or not worktree_id:
            raise ValueError("mux-status-v1 payload missing 'worktree_id'")
        if not isinstance(values, dict):
            raise ValueError("mux-status-v1 payload missing 'values'")
        entry = registry.get(project, worktree_id)
        if entry is None or not entry["live"]:
            return {"applied": False, "reason": "not-live"}
        # Revalidate a recovered/persisted mapping against the REAL mux
        # server rather than trusting its stored `live` bit alone (Copilot
        # review finding, Step 2's own validation requirement): a mux
        # session that was torn down while the daemon was down (or since
        # this entry was last observed) must not still be treated as a
        # valid write target. A dead session invalidates the mapping so a
        # later lookup does not repeat the same probe forever.
        if not _mux_session_alive(entry["mux_bin"], entry["mux_session"]):
            registry.remove(project, worktree_id, mapping_revision=entry["mapping_revision"])
            return {"applied": False, "reason": "not-live"}
        ok = apply_status_options(entry, values)
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


class MuxDaemonRuntime:
    """Owns the mux-companion daemon's whole in-process lifecycle: the
    coalescing server plus its registry handle -- mirrors
    ``agent_worktrees.mux_link.InProcessRuntime`` structurally."""

    def __init__(self, registry_path_: Path) -> None:
        self.server: CoalescingServer | None = None
        self.registry = MuxMappingRegistry(registry_path_)

    def start(self) -> None:
        try:
            server = start_server(build_compute(self.registry))
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
            self.server.close()
        self.server = None


def run_daemon_foreground(
    root: Path | None = None,
    *,
    idle_after_s: float = IDLE_LINGER_S,
    poll_interval_s: float = 1.0,
    max_iterations: int | None = None,
) -> int:
    """The resident daemon's own loop: start the server, publish the lock
    file, then idle-exit after ``idle_after_s`` seconds with no live mapping
    and no active subscriber. ``max_iterations`` is test-only (bounds the
    loop instead of relying on the idle timer alone)."""
    resolved_root = root if root is not None else default_root()
    runtime = MuxDaemonRuntime(registry_path(resolved_root))
    runtime.start()
    if runtime.server is None:
        return 1
    lock = lock_path(resolved_root)
    generation = wcs_client.new_client_id()
    idle_since: float | None = None
    iterations = 0
    try:
        while True:
            extra = runtime.lock_extra()
            extra["generation_id"] = generation
            write_lock_data(lock, extra)
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
        # Ownership-aware cleanup (Copilot review finding): unconditionally
        # unlinking the lock file here is unsafe if a REPLACEMENT daemon
        # has already started and published its own (newer) rendezvous
        # under the same path while this one was shutting down -- that
        # would delete the live replacement's lock, leaving it
        # undiscoverable. Only remove the file if it still records THIS
        # daemon's own generation.
        current = read_lock_data(lock)
        if current is not None and current.get("generation_id") == generation:
            try:
                lock.unlink()
            except OSError:
                pass
    return 0


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


def _spawn_serialized(root: Path):
    """Cross-process advisory lock serializing the whole check-then-spawn
    boot sequence (Copilot review finding): without this, two concurrent
    first callers can both observe no live endpoint and each spawn their
    own dynamic-port daemon, with each then racing to publish/overwrite the
    shared lock file -- and the loser's daemon becomes an orphaned,
    undiscoverable process. Only one caller performs the check-and-spawn at
    a time; every other concurrent caller waits for the lock, then finds
    the first caller's daemon already live and skips spawning entirely."""
    import contextlib

    @contextlib.contextmanager
    def _cm():
        root.mkdir(parents=True, exist_ok=True)
        with open(_spawn_lock_path(root), "a+b") as fh:
            _lock_file(fh)
            try:
                yield
            finally:
                _unlock_file(fh)

    return _cm()


def ensure_daemon_running(
    root: Path | None = None, *, boot_wait_s: float = BOOT_WAIT_S
) -> bool:
    """Start the resident Manager mux daemon unless one is already live.
    Returns whether a current daemon is believed running (already live, or
    freshly spawned within ``boot_wait_s``). Idempotent + cheap: a live
    daemon is a no-op."""
    resolved_root = root if root is not None else default_root()
    lock = lock_path(resolved_root)
    if _daemon_is_live(read_lock_data(lock)):
        return True
    with _spawn_serialized(resolved_root):
        # Re-check now that this call holds the spawn lock (Copilot review
        # finding): a concurrent caller may have already spawned (and this
        # call's own daemon may now be live) while this call was waiting to
        # acquire it -- never spawn a second daemon on top of one that just
        # became live.
        if _daemon_is_live(read_lock_data(lock)):
            return True
        # Propagate the resolved root to the child (Copilot review
        # finding): without this, a caller-supplied non-default `root`
        # (e.g. a test's scratch directory) would wait on a lock under
        # that root while the spawned `mux-daemon run` resolved its own
        # default installation root instead, so this helper would report
        # failure despite successfully spawning a daemon.
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
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
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


# ---------------------------------------------------------------------------
# Register/remove mapping helpers (the "internal commands/helpers" Step 2
# calls for -- not yet wired to any real launch/join/restore/remux caller)
# ---------------------------------------------------------------------------


def register_mapping(payload: dict, root: Path | None = None) -> dict:
    """Register or update one worktree's mux mapping. Pure disk operation
    (see the module docstring for why this never talks to the resident
    daemon process) -- safe to call whether or not a daemon is currently
    running; the daemon reads the same file on its next lookup."""
    registry = MuxMappingRegistry(registry_path(root))
    return registry.register(payload)


def remove_mapping(
    project: str, worktree_id: str, *, mapping_revision: int | None = None, root: Path | None = None
) -> dict:
    """Remove one worktree's mux mapping (see :meth:`MuxMappingRegistry.remove`)."""
    registry = MuxMappingRegistry(registry_path(root))
    return registry.remove(project, worktree_id, mapping_revision=mapping_revision)


def get_mapping(project: str, worktree_id: str, root: Path | None = None) -> dict | None:
    registry = MuxMappingRegistry(registry_path(root))
    return registry.get(project, worktree_id)

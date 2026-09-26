"""Worktree Manager mux-mapping registry (Phase 3b Slice 2 Sub-slice 3 Step 2
-- ``worktree-manager-control-plane`` effort).

Split out of ``mux_daemon.py`` (which now owns the daemon lifecycle/wire
server) purely to stay under this repo's per-module line cap -- see that
module's own docstring for the full Step 2 rationale and the process-
boundary rule this package follows (Worktree Manager never imports
``agent_worktrees`` in-process).

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

import contextlib
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from .self_install import default_root

_REQUIRED_MAPPING_STR_FIELDS = ("project", "worktree_id", "mux_session", "mux_bin")

# ---------------------------------------------------------------------------
# Cross-process advisory file lock (mirrors agent_worktrees.mux_link's own
# copy exactly -- duplicated, not imported, per the process-boundary rule
# this package follows). Shared by the registry's own interprocess lock AND
# (via import) mux_daemon.py's single-instance daemon lease.
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

    def _try_lock_file_once(fh) -> bool:
        """A single non-blocking lock attempt -- unlike :func:`_lock_file`,
        never retries, so a caller can tell "someone else already holds
        this" from "acquired it" immediately."""
        fh.seek(0, os.SEEK_END)
        if fh.tell() == 0:
            fh.write(b"\0")
            fh.flush()
        fh.seek(0)
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
else:
    import fcntl

    def _lock_file(fh) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)

    def _unlock_file(fh) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def _try_lock_file_once(fh) -> bool:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False


def registry_path(root: Path | None = None) -> Path:
    """The Manager-owned per-worktree mapping registry snapshot."""
    return (root if root is not None else default_root()) / "mux-mapping.json"


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
        # Durable last-write-wins fence for mux-status-v1 applies (Copilot
        # review finding): an in-memory-only high-water mark does not
        # survive a daemon restart, so a delayed render older than what was
        # already applied before the restart could otherwise pass the
        # ordering check. Preserved across register()/remove() round-trips
        # since neither of those calls concerns status-render ordering.
        "last_status_rendered_at": (
            payload.get("last_status_rendered_at")
            if isinstance(payload.get("last_status_rendered_at"), str)
            else None
        ),
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
            if current is not None:
                if entry["mapping_revision"] < current["mapping_revision"]:
                    return {
                        "applied": False,
                        "reason": "stale_revision",
                        "current_revision": current["mapping_revision"],
                    }
                # An equal-revision LIVE registration must not resurrect an
                # explicitly-tombstoned entry at that exact revision
                # (Copilot review finding): the monotonic ``<``-only guard
                # above lets it through, since equal revisions are
                # otherwise meant to be accepted (e.g. a benign metadata
                # refresh at the same revision). An explicit
                # ``remove(..., mapping_revision=N)`` deliberately leaves a
                # ``live: false`` tombstone at exactly revision N to fence
                # against exactly this.
                if (
                    entry["mapping_revision"] == current["mapping_revision"]
                    and not current["live"]
                    and entry["live"]
                ):
                    return {
                        "applied": False,
                        "reason": "stale_revision",
                        "current_revision": current["mapping_revision"],
                    }
            # A register()/remove() payload is about MAPPING identity, not
            # status-render ordering -- it never carries
            # last_status_rendered_at itself, so preserve whatever fence
            # was already recorded rather than silently resetting it to
            # None on every re-register. Only for the SAME mapping
            # incarnation, though (Copilot review finding): a strictly
            # HIGHER mapping_revision is a genuinely new incarnation (e.g.
            # a new mux session after a cutover), and build_compute()
            # relies on a new incarnation starting with a fresh ordering
            # fence -- inheriting the old session's fence could reject that
            # new incarnation's very first render as a stale-render purely
            # because it predates the previous session's own timestamp.
            if (
                current is not None
                and entry["mapping_revision"] == current["mapping_revision"]
                and entry["last_status_rendered_at"] is None
            ):
                entry["last_status_rendered_at"] = current["last_status_rendered_at"]
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

    def record_applied_render(
        self, project: str, worktree_id: str, rendered_at: str, *, mapping_revision: int
    ) -> None:
        """Persist the ``rendered_at`` of the last mux-status-v1 render
        actually applied for this mapping (Copilot review finding): a
        purely in-memory last-applied high-water mark does not survive a
        daemon restart, so a delayed render older than what was already
        applied before the restart could otherwise pass the ordering check
        and violate last-write-wins. Best-effort and silently a no-op if
        the mapping has since been superseded (a different
        ``mapping_revision`` than the one this render was actually applied
        against) or removed -- never regresses the fence for a NEWER
        mapping incarnation."""
        key = (project, worktree_id)
        with self._interprocess_lock():
            entries = self._read_all()
            current = entries.get(key)
            if current is None or current["mapping_revision"] != mapping_revision:
                return
            current["last_status_rendered_at"] = rendered_at
            entries[key] = current
            self._write_all(entries)

    def snapshot(self) -> dict[tuple[str, str], dict]:
        with self._interprocess_lock():
            entries = self._read_all()
        return {key: dict(entry) for key, entry in entries.items()}

    def has_any_live(self) -> bool:
        with self._interprocess_lock():
            entries = self._read_all()
        return any(e["live"] for e in entries.values())


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

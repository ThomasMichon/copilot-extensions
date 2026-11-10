"""Point reconciliation and atomic initialization of legacy session registries."""

from __future__ import annotations

import copy

import yaml

from . import config as cfg, reclaim, sessions, tracking
from .push_timeout import ObservationUnavailable
from .record_io import read_record_mapping
from .session_projection import _valid_session_id

_INCARNATION = ("worktree_id", "worktree_path", "repo", "branch", "creation_nonce")
_HINTS = ("bound_live", "bound_live_at", "mux_live", "mux_live_at")


def _read(path):
    try:
        data = read_record_mapping(path)
    except yaml.YAMLError as error:
        raise ObservationUnavailable("Strict refresh tracking YAML is malformed") from error
    registry = data.get("sessions")
    if registry is not None and (
        not isinstance(registry, list)
        or any(
            not isinstance(entry, dict)
            or not isinstance(entry.get("session_id"), str)
            or not _valid_session_id(entry["session_id"])
            for entry in registry
        )
    ):
        raise ObservationUnavailable("Strict refresh session registry metadata is malformed")
    try:
        return tracking.parse_record_data(data, path=path)
    except (ValueError, TypeError, KeyError, OverflowError) as error:
        raise ObservationUnavailable("Strict refresh tracking metadata is malformed") from error


def _fence(record):
    fields = (*_INCARNATION, *_HINTS, "sessions", "lifecycle_revision",
              "execution_leg", "execution_leg_raw",
              "session_backend", "session_backend_raw")
    return (
        tuple(copy.deepcopy(getattr(record, name, None)) for name in fields),
        record.resolved_head_session,
    )


def initialize_registry(record, session_ids) -> bool:
    path = record.yaml_path
    with tracking._RecordLock(path, require_sidecar=True):
        current = tracking._load_record_uncached(path)
        if any(getattr(current, name) != getattr(record, name) for name in _INCARNATION):
            return False
        if current.sessions is None:
            current.sessions = [
                tracking.SessionEntry(session_id=session_id, started_at="")
                for session_id in session_ids
            ]
            tracking._next_lifecycle_revision(current, *session_ids)
            tracking.save_record(current, path)
        # Keep unrelated snapshot fields paired with their read-time CAS evidence.
        for field in (
            "sessions", "lifecycle_revision", "head_revision", "head_session",
            "head_transitions", "handoff_counter", "handoffs",
        ):
            setattr(record, field, getattr(current, field))
    return True


def refresh_record(record):
    path = record.yaml_path
    if path.parent.resolve() != cfg.tracking_dir().resolve() or path.name != record.worktree_id + ".yaml":
        raise ObservationUnavailable("Strict refresh requires the selected project's exact record")
    before = _read(path)
    if any(getattr(before, name) != getattr(record, name) for name in _INCARNATION):
        raise ObservationUnavailable("Worktree incarnation changed before strict refresh")
    observed = copy.copy(before)
    if observed.sessions is None:
        discovered = sessions.backfill_sessions([observed], strict=True)
        observed.sessions = [
            tracking.SessionEntry(session_id=session_id, started_at="")
            for session_id in discovered.get(record.worktree_id, [])
        ]
    bridge_live = record.worktree_id in reclaim.live_bridge_worktrees(strict=True)
    bound_live = bool(reclaim.resolve_bound_copilots(
        worktree_id=record.worktree_id,
        session_ids=[entry.session_id for entry in observed.sessions],
        strict=True,
    )) or bridge_live
    mux = sessions.mux_status_many([record.worktree_id]).get(record.worktree_id)
    if mux is None or not getattr(mux, "observed", True):
        raise ObservationUnavailable("Strict refresh mux observation is unavailable")
    expected = _fence(before)
    with tracking._RecordLock(path, require_sidecar=True):
        current = _read(path)
        if _fence(current) != expected:
            raise ObservationUnavailable("Tracking changed during strict refresh")
        initializing_registry = current.sessions is None
        current.sessions = observed.sessions
        if initializing_registry:
            tracking._next_lifecycle_revision(
                current, *(entry.session_id for entry in current.sessions),
            )
        current.bound_live, current.mux_live = bound_live, bool(mux.exists)
        current.bound_live_at = current.mux_live_at = tracking._now_iso()
        tracking.save_record(current, path)
        persisted = _read(path)
        if _fence(persisted) != _fence(current):
            raise ObservationUnavailable("Strict refresh tracking persistence was not confirmed")
    return persisted

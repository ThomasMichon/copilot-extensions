"""Opt-in, local owner transaction for an authorized new native conversation.

This promotes an already registered session. It does not register, launch, send
work, conclude a predecessor, transfer claims, or repair missing identity proof.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from uuid import UUID

import yaml

from . import (
    config_authority,
    finalize,
    launch_admission,
    native_lifecycle_source,
    native_source_context,
    output,
    sessions,
    tracking,
    worktree_lifecycle,
)
from .session_liveness import session_liveness


CONTRACT_VERSION = 1


@dataclass(frozen=True)
class ReplacementTarget:
    """Exact existing allocation and predecessor, before a candidate is started."""
    worktree_id: str
    worktree_path: str
    creation_nonce: str
    expected_head: str
    expected_head_revision: int
    expected_lifecycle_revision: int


@dataclass(frozen=True)
class HeadReplacement(ReplacementTarget):
    """Registered candidate and corroborated source; caller owns authorization."""
    replacement_session: str
    replacement_source: str
    native_source: object


@dataclass(frozen=True)
class HeadReplacementResult:
    previous_head: str
    head_session: str
    head_revision: int


def observe_candidate(worktree_id: str, session_id: str, pid: int) -> dict:
    """Corroborate a registered native activation without launching or binding.

    The caller's PID is only a lookup hint. Its current kernel birth/epoch and
    actual open session hold must reproduce the source recorded by the original
    native hook. Never capture this observer's ancestry as a substitute.
    """
    if (
        not worktree_id or Path(worktree_id).name != worktree_id
        or "/" in worktree_id or "\\" in worktree_id or worktree_id in {".", ".."}
        or str(UUID(session_id)) != session_id or type(pid) is not int or pid <= 0
    ):
        _refuse("An exact worktree, canonical native UUID and process are required")
    path = tracking._owning_tracking_dir(worktree_id) / f"{worktree_id}.yaml"
    record = tracking.load_record(path)
    _require_known_history(record, path)
    entry = record.session_entry(session_id)
    if (
        record.worktree_id != worktree_id or record.status != "active"
        or entry is None or entry.state != "active" or entry.ended_at
        or entry.predecessor or entry.successor or not entry.activations
        or entry.pruned_sources or entry.source_history_saturated
    ):
        _refuse("Candidate lacks an active registered native conversation")
    latest = max(entry.activations, key=lambda item: item.ordinal)
    if (
        latest.ended_at or not latest.source_identity or latest.source_ambiguous
        or sum(a.source_identity == latest.source_identity for a in entry.activations) != 1
    ):
        _refuse("Candidate native activation is ambiguous or ended")
    epoch = native_source_context.epoch()
    process = native_source_context.process(pid)
    witness = {
        "version": 1, "qualified": True, **epoch,
        "ancestors": [{"pid": pid, "start_time": process["start_time"]}],
    }
    if native_lifecycle_source.start_identity(session_id, witness) != latest.source_identity:
        _refuse("Candidate process does not match the original registered native source")
    cwd = _candidate_cwd(session_id)
    if (
        cwd is None or not record.worktree_path
        or cwd.resolve(strict=True) != Path(record.worktree_path).resolve(strict=True)
        or asdict(tracking.load_record(path)) != asdict(record)
    ):
        _refuse("Candidate workspace or registration changed during observation")
    return {
        "worktree_id": worktree_id, "worktree_path": record.worktree_path,
        "creation_nonce": record.creation_nonce,
        "expected_head": record.resolved_head_session,
        "expected_head_revision": record.head_revision,
        "expected_lifecycle_revision": record.lifecycle_revision,
        "replacement_session": session_id, "replacement_source": latest.source_identity,
        "native_source": witness,
        "head_transitions": [asdict(item) for item in record.head_transitions[-2:]],
    }


def _refuse(message: str) -> None:
    raise tracking.SessionLifecycleError(message)


def _candidate_cwd(session_id: str) -> Path:
    """Read pre-prompt metadata; callers independently prove the native open hold."""
    entry = sessions._session_state_dir() / session_id
    if sessions._is_detached_session(entry):
        _refuse("A detached continuation cannot be a replacement candidate")
    try:
        data = yaml.safe_load((entry / "workspace.yaml").read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise tracking.SessionLifecycleError("Candidate native workspace metadata is invalid") from exc
    cwd = data.get("cwd") if isinstance(data, dict) else None
    if not isinstance(cwd, str) or not cwd.strip() or not Path(cwd).is_absolute():
        _refuse("Candidate native workspace metadata is missing or invalid")
    path = Path(cwd)
    if not path.is_dir():
        _refuse("Candidate native workspace is unavailable")
    return path


def _known_values(raw: object, parsed: object) -> bool:
    """Reject fields the compatibility reader would drop or reinterpret."""
    if isinstance(raw, dict) and isinstance(parsed, dict):
        return raw.keys() <= parsed.keys() and all(
            _known_values(value, parsed[key]) for key, value in raw.items()
        )
    if isinstance(raw, list) and isinstance(parsed, list):
        return len(raw) == len(parsed) and all(
            _known_values(left, right) for left, right in zip(raw, parsed)
        )
    if isinstance(raw, (datetime, date)):
        raw = raw.isoformat()
    return type(raw) is type(parsed) and raw == parsed


def _require_known_history(record: tracking.WorktreeRecord, path: Path) -> None:
    # Ordinary loading is intentionally permissive for old/future records.
    # This destructive-authority decision cannot silently discard opaque history.
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        _refuse("Worktree history is opaque")
    for name in ("head_revision", "lifecycle_revision"):
        if not _known_values(raw.get(name), getattr(record, name)):
            _refuse("Worktree revision history is opaque")
    for name in ("sessions", "head_transitions", "handoffs"):
        entries = getattr(record, name) or []
        if not _known_values(raw.get(name, []), [asdict(item) for item in entries]):
            _refuse(f"Worktree {name} history is opaque")
    ids = [entry.session_id for entry in record.sessions or ()]
    revisions = [item.revision for item in record.head_transitions]
    if (
        len(ids) != len(set(ids))
        or not revisions or any(type(rev) is not int or rev <= 0 for rev in revisions)
        or revisions != sorted(set(revisions))
        or record.head_revision != revisions[-1]
        or record.lifecycle_revision < record.head_revision
        or record.lifecycle_revision >= tracking.MAX_PERSISTED_COUNTER
        or record.head_session != record.resolved_head_session
    ):
        _refuse("Worktree head history is missing, ambiguous or inconsistent")
    for entry in record.sessions or ():
        ordinals = [activation.ordinal for activation in entry.activations]
        if (
            entry.state not in {"active", "yielded", "handed-off", "concluded"}
            or ordinals != sorted(set(ordinals))
            or any(type(ordinal) is not int or ordinal <= 0 for ordinal in ordinals)
        ):
            _refuse("Worktree native activation history is ambiguous")
    # Saving must not prune predecessor history as a side effect of promotion.
    if (
        len(record.head_transitions) >= tracking._MAX_HEAD_TRANSITIONS
        or len(record.handoffs) > tracking._MAX_HANDOFFS
        or any(len(s.activations) > tracking._MAX_SESSION_ACTIVATIONS for s in record.sessions or ())
    ):
        _refuse("Worktree history capacity cannot preserve this transition")


def _check_target(
    request: ReplacementTarget, record: tracking.WorktreeRecord, path: Path,
) -> None:
    if (
        record.worktree_id != request.worktree_id
        or not record.creation_nonce or record.creation_nonce != request.creation_nonce
        or not record.worktree_path
        or Path(record.worktree_path) != Path(record.worktree_path).resolve(strict=True)
        or Path(request.worktree_path) != Path(request.worktree_path).resolve(strict=True)
        or Path(record.worktree_path).resolve(strict=True)
        != Path(request.worktree_path).resolve(strict=True)
    ):
        _refuse("Worktree identity, path or creation nonce changed")
    if record.status != "active" or record.completed_at or record.reaped_at:
        _refuse("Worktree is finalizing, terminal or not active")
    if record.pending_handoffs:
        _refuse("Worktree has a pending handoff")
    if record.paused or record.pending_seed:
        _refuse("Worktree is paused or has pending delivery")
    if (
        record.execution_leg_opaque or record.session_backend_opaque
        or record.controller_metadata_opaque or record.dispatch_attempt_opaque
        or record.execution_leg is not None or record.session_backend is not None
    ):
        _refuse("Worktree execution authority is opaque or externally held")
    _require_known_history(record, path)
    if (
        record.resolved_head_session != request.expected_head
        or record.head_revision != request.expected_head_revision
        or record.lifecycle_revision != request.expected_lifecycle_revision
    ):
        _refuse("Expected head or revision changed; replacement is stale or replayed")


def _check_holders(
    record: tracking.WorktreeRecord, expected_head: str, replacement_session: str | None = None,
) -> None:
    mux = sessions._list_mux_sessions()
    if mux is None:
        _refuse("Mux ownership is unknown")
    if sessions.mux_session_name(record.worktree_id) in mux:
        _refuse("A competing mux holds the worktree")
    if session_liveness(expected_head) != "dead":
        _refuse("Predecessor is not provably dead")
    for other in record.sessions or ():
        if other.session_id not in {expected_head, replacement_session}:
            if session_liveness(other.session_id) != "dead":
                _refuse("Another registered native holder is live or unknown")


def _validate_target(request: ReplacementTarget) -> None:
    if (
        not request.worktree_id or Path(request.worktree_id).name != request.worktree_id
        or "/" in request.worktree_id or "\\" in request.worktree_id
        or request.worktree_id in {".", ".."}
        or not Path(request.worktree_path).is_absolute() or not request.creation_nonce
        or type(request.expected_head_revision) is not int or request.expected_head_revision <= 0
        or type(request.expected_lifecycle_revision) is not int
        or not request.expected_head_revision <= request.expected_lifecycle_revision < tracking.MAX_PERSISTED_COUNTER
        or str(UUID(request.expected_head)) != request.expected_head
    ):
        _refuse("Exact worktree, creation nonce, native head and revisions are required")


def check_dead_head(request: ReplacementTarget) -> dict:
    """Read the exact stopped target; never reserve, register, launch or promote."""
    _validate_target(request)
    path = tracking._owning_tracking_dir(request.worktree_id) / f"{request.worktree_id}.yaml"
    with tracking._RecordLock(path, require_sidecar=True):
        record = tracking.load_record(path)
        config_authority.registration_record_config(record)
        _check_target(request, record, path)
        _check_holders(record, request.expected_head)
        return asdict(request)


def _check(request: HeadReplacement, record: tracking.WorktreeRecord, path: Path) -> None:
    _check_target(request, record, path)
    if any(item.session_id == request.replacement_session for item in record.head_transitions):
        _refuse("Replacement has already been a head; a new conversation is required")
    entry = record.session_entry(request.replacement_session)
    if (
        entry is None or entry.state != "active" or entry.ended_at
        or entry.predecessor or entry.successor
        or entry.pruned_sources or entry.source_history_saturated
        or not entry.activations
    ):
        _refuse("Replacement lacks an unambiguous registered native activation")
    latest = max(entry.activations, key=lambda activation: activation.ordinal)
    if (
        latest.ended_at or latest.source_ambiguous
        or latest.source_identity != request.replacement_source
        or sum(a.source_identity == request.replacement_source for a in entry.activations) != 1
    ):
        _refuse("Replacement native activation changed or is ambiguous")
    _check_holders(record, request.expected_head, request.replacement_session)
    cwd = _candidate_cwd(request.replacement_session)
    if cwd is None or cwd.resolve(strict=True) != Path(record.worktree_path).resolve(strict=True):
        _refuse("Replacement native workspace does not match the worktree")
    if native_lifecycle_source.start_identity(
        request.replacement_session, request.native_source,
    ) != request.replacement_source:
        _refuse("Replacement native ownership is unconfirmed")


def replace_dead_head(request: HeadReplacement) -> HeadReplacementResult:
    """Conditionally promote one registered native session under owner locks.

    Raises on missing proof, contention, stale/replayed input or persistence
    failure; never falls back to normal bind or repairs state. Unsupported
    native ownership platforms fail closed through ``start_identity``.
    """
    _validate_target(request)
    if not request.replacement_source or request.expected_head == request.replacement_session:
        _refuse("Exact worktree, creation nonce, head revision and replacement are required")
    for session_id in (request.expected_head, request.replacement_session):
        if str(UUID(session_id)) != session_id:
            _refuse("Only exact canonical native session UUIDs are accepted")
    path = tracking._owning_tracking_dir(request.worktree_id) / f"{request.worktree_id}.yaml"
    before = tracking.load_record(path)
    config = config_authority.registration_record_config(before)
    root = Path(config.repos[before.repo].worktree_root).resolve(strict=True)
    lock = finalize.FinalizeLock(root / ".finalize.lock", timeout=3, stale_after=3600)
    with output.stdout_to_stderr():
        lock.acquire()
    try:
        with worktree_lifecycle.guard(path), launch_admission.guard(path):
            with tracking._RecordLock(path, require_sidecar=True):
                record = tracking.load_record(path)
                current = config_authority.registration_record_config(record)
                if (
                    record.repo != before.repo
                    or Path(current.repos[record.repo].worktree_root).resolve(strict=True) != root
                ):
                    _refuse("Worktree lifecycle authority changed")
                _check(request, record, path)
                transition = tracking._append_head_transition(
                    record, request.replacement_session,
                    reason="authorized-dead-head-replacement",
                )
                tracking.save_record(record, path)
                return HeadReplacementResult(
                    request.expected_head, request.replacement_session, transition.revision,
                )
    finally:
        lock.release()

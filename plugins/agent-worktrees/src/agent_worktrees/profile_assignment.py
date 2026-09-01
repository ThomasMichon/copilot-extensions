"""Opt-in balanced profile assignment for new Copilot session generations."""

from __future__ import annotations

import hashlib
import json
import random
import secrets
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from . import config as cfg
from . import tracking

ASSIGNMENT_TOKEN_ENV = "AGENT_WORKTREES_PROFILE_ASSIGNMENT_TOKEN"
STATE_SCHEMA_VERSION = 1
DEFAULT_PENDING_TTL_SECONDS = 900
DEFAULT_HISTORY_LIMIT = 256
RECORD_HISTORY_LIMIT = 128
DIAGNOSTIC_LIMIT = 32
DIAGNOSTIC_DETAIL_LIMIT = 240
_DIAGNOSTIC_KEYS: set[str] = set()


class ProfileAssignmentError(RuntimeError):
    """An armed assignment policy cannot safely select or replay a profile."""


class ProfileAssignmentStateError(RuntimeError):
    """Optional assignment state is unavailable or incompatible."""


@dataclass(frozen=True)
class LaunchProfileSelection:
    """An ordinary profile plus its durable assignment metadata, when assigned."""

    profile: cfg.CopilotProfile | None
    assignment: tracking.ProfileAssignment | None = None
    launch_token: str | None = None
    warning: str = ""


def _diagnostic(key: str, message: str) -> str:
    """Emit one bounded warning per distinct optional-assignment failure."""
    detail = " ".join(str(message).split())[:DIAGNOSTIC_DETAIL_LIMIT]
    if key not in _DIAGNOSTIC_KEYS and len(_DIAGNOSTIC_KEYS) < DIAGNOSTIC_LIMIT:
        _DIAGNOSTIC_KEYS.add(key)
        print(f"warning: profile assignment: {detail}", file=sys.stderr)
    return detail


def state_path() -> Path:
    """Return the active project's profile-assignment state file."""
    return cfg.project_dir() / "profile-assignments.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="seconds")


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _profile_map(profiles: list[cfg.CopilotProfile]) -> dict[str, cfg.CopilotProfile]:
    return {profile.name: profile for profile in profiles}


def validate_policy(
    policy: cfg.ProfileAssignmentPolicy | None,
    profiles: list[cfg.CopilotProfile],
) -> None:
    """Reject user-owned policy errors or an unsafe armed profile pool."""
    if policy is None:
        return
    if policy.error:
        raise ProfileAssignmentError(policy.error)
    if not policy.armed:
        return
    if policy.repository_error:
        raise ProfileAssignmentError(policy.repository_error)
    available = _profile_map(profiles)
    missing = [name for name in policy.profiles if name not in available]
    if missing:
        raise ProfileAssignmentError(
            "profile assignment references unavailable profiles: "
            + ", ".join(missing)
        )


def _empty_state() -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "policy": "",
        "seed": "",
        "pool": [],
        "generation": 0,
        "position": 0,
        "bag": [],
        "assignments": [],
    }


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _empty_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ProfileAssignmentStateError(
            f"profile-assignment state is unreadable: {path}"
        ) from exc
    if not isinstance(data, dict):
        raise ProfileAssignmentStateError(
            f"profile-assignment state is malformed: {path}"
        )
    try:
        schema_version = tracking._bounded_nonnegative_int(
            data.get("schema_version"),
            field="schema_version",
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise ProfileAssignmentStateError(
            f"profile-assignment state has a malformed schema version: {path}"
        ) from exc
    if schema_version != STATE_SCHEMA_VERSION:
        raise ProfileAssignmentStateError(
            f"profile-assignment state has an unsupported schema: {path}"
        )
    assignments = data.get("assignments")
    if not isinstance(assignments, list):
        raise ProfileAssignmentStateError(
            f"profile-assignment history is malformed: {path}"
        )
    try:
        data["generation"] = tracking._bounded_nonnegative_int(
            data.get("generation"),
            field="generation",
        )
        data["position"] = tracking._bounded_nonnegative_int(
            data.get("position"),
            field="position",
        )
        for index, raw in enumerate(assignments):
            if not isinstance(raw, dict):
                raise ValueError(f"assignments[{index}] must be a mapping")
            raw["bag_generation"] = tracking._bounded_nonnegative_int(
                raw.get("bag_generation"),
                field=f"assignments[{index}].bag_generation",
            )
            raw["bag_position"] = tracking._bounded_nonnegative_int(
                raw.get("bag_position"),
                field=f"assignments[{index}].bag_position",
            )
    except (TypeError, ValueError, OverflowError) as exc:
        raise ProfileAssignmentStateError(
            f"profile-assignment numeric state is malformed: {path}"
        ) from exc
    return data


def _write_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tracking._atomic_write(
        path,
        json.dumps(state, indent=2, sort_keys=True) + "\n",
    )


def _pool_fingerprint(policy: str, pool: tuple[str, ...]) -> str:
    payload = json.dumps([policy, list(pool)], separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _shuffled_bag(
    seed: str,
    policy: str,
    pool: tuple[str, ...],
    generation: int,
) -> list[str]:
    digest = hashlib.sha256(
        json.dumps(
            [seed, policy, generation, list(pool)],
            separators=(",", ":"),
        ).encode("utf-8")
    ).digest()
    rng = random.Random(int.from_bytes(digest, "big"))
    bag = list(pool)
    rng.shuffle(bag)
    return bag


def _record_from_state(raw: dict[str, Any]) -> tracking.ProfileAssignment:
    return tracking.ProfileAssignment(
        policy=str(raw.get("policy") or ""),
        assignment_label=str(raw.get("assignment_label") or ""),
        selected_profile=str(raw.get("selected_profile") or ""),
        bag_generation=tracking._bounded_nonnegative_int(
            raw.get("bag_generation"),
            field="assignment.bag_generation",
        ),
        bag_position=tracking._bounded_nonnegative_int(
            raw.get("bag_position"),
            field="assignment.bag_position",
        ),
        assigned_at=str(raw.get("assigned_at") or ""),
        disposition=(
            raw.get("disposition")
            if raw.get("disposition") in ("pending", "bound", "abandoned")
            else "pending"
        ),
        session_id=(
            str(raw["session_id"]) if raw.get("session_id") else None
        ),
        lane=str(raw.get("lane") or ""),
        abandoned_at=(
            str(raw["abandoned_at"]) if raw.get("abandoned_at") else None
        ),
        bound_at=str(raw["bound_at"]) if raw.get("bound_at") else None,
        predecessor_session_id=(
            str(raw["predecessor_session_id"])
            if raw.get("predecessor_session_id")
            else None
        ),
    )


def _state_from_record(
    assignment: tracking.ProfileAssignment,
    *,
    launch_token: str,
    worktree_id: str,
    generation_key: str,
) -> dict[str, Any]:
    data = asdict(assignment)
    data["launch_token"] = launch_token
    data["worktree_id"] = worktree_id
    data["generation_key"] = generation_key
    return data


def _assignment_identity(
    assignment: tracking.ProfileAssignment,
) -> tuple[str, int, int, str]:
    return (
        assignment.policy,
        assignment.bag_generation,
        assignment.bag_position,
        assignment.assigned_at,
    )


def _with_fallback_profile(
    selection: LaunchProfileSelection,
    fallback_profile: cfg.CopilotProfile | None,
) -> LaunchProfileSelection:
    """Preserve the ordinary launch profile when assignment is unavailable."""
    if selection.profile is not None or fallback_profile is None:
        return selection
    return LaunchProfileSelection(
        profile=fallback_profile,
        assignment=selection.assignment,
        launch_token=selection.launch_token,
        warning=selection.warning,
    )


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _retire_token(raw: dict[str, Any]) -> None:
    token = raw.pop("launch_token", None)
    if isinstance(token, str) and token:
        raw["retired_token_digest"] = _token_digest(token)


def _compact_history(state: dict[str, Any], limit: int) -> bool:
    original = list(state.get("assignments", []))
    assignments = [
        raw for raw in state.get("assignments", []) if isinstance(raw, dict)
    ]
    assignments.sort(key=lambda raw: str(raw.get("assigned_at") or ""))
    if len(assignments) <= limit:
        state["assignments"] = assignments
    else:
        pending = [
            raw for raw in assignments if raw.get("disposition") == "pending"
        ]
        terminal = [
            raw for raw in assignments if raw.get("disposition") != "pending"
        ]
        terminal_limit = max(0, limit - len(pending))
        kept_terminal = terminal[-terminal_limit:] if terminal_limit else []
        state["assignments"] = kept_terminal + pending
        state["assignments"].sort(
            key=lambda raw: str(raw.get("assigned_at") or "")
        )
    return state["assignments"] != original


def _expire_pending_locked(
    state: dict[str, Any],
    *,
    now: datetime,
    ttl_seconds: int,
) -> list[tuple[str, tracking.ProfileAssignment]]:
    changed: list[tuple[str, tracking.ProfileAssignment]] = []
    cutoff = now - timedelta(seconds=max(1, ttl_seconds))
    for raw in state.get("assignments", []):
        if not isinstance(raw, dict) or raw.get("disposition") != "pending":
            continue
        assigned_at = _parse_timestamp(raw.get("assigned_at"))
        if assigned_at is None or assigned_at > cutoff:
            continue
        raw["disposition"] = "abandoned"
        raw["abandoned_at"] = _timestamp(now)
        _retire_token(raw)
        changed.append((
            str(raw.get("worktree_id") or ""),
            _record_from_state(raw),
        ))
    return changed


def _sync_record(worktree_id: str, assignment: tracking.ProfileAssignment) -> None:
    if not worktree_id or worktree_id == tracking.ANCHOR_ID:
        return
    path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    if not path.exists():
        return
    with tracking._RecordLock(path, require_sidecar=True):
        record = tracking.load_record(path)
        existing = next(
            (
                item
                for item in record.profile_assignments
                if _assignment_identity(item) == _assignment_identity(assignment)
            ),
            None,
        )
        if existing is None:
            record.profile_assignments.append(assignment)
        else:
            if existing == assignment:
                return
            if existing.disposition != "pending":
                # The allocator ledger advances pending exactly once to a
                # terminal disposition. A delayed pending reflection must not
                # roll that authoritative record view backward, and terminal
                # outcomes are never allowed to switch sideways.
                return
            index = record.profile_assignments.index(existing)
            record.profile_assignments[index] = assignment
        record.profile_assignments = record.profile_assignments[-RECORD_HISTORY_LIMIT:]
        record.profile_assignment_revision = tracking._bounded_nonnegative_int(
            record.profile_assignment_revision + 1,
            field="profile_assignment_revision",
        )
        tracking.save_record(record)


def _sync_record_best_effort(
    worktree_id: str,
    assignment: tracking.ProfileAssignment,
) -> None:
    try:
        _sync_record(worktree_id, assignment)
    except (OSError, OverflowError, TimeoutError, TypeError, ValueError) as exc:
        _diagnostic(
            "record-sync",
            "record metadata could not be refreshed; continuing without "
            f"making assignment metadata load-bearing ({type(exc).__name__})",
        )


def expire_pending(
    *,
    now: datetime | None = None,
    ttl_seconds: int = DEFAULT_PENDING_TTL_SECONDS,
    history_limit: int = DEFAULT_HISTORY_LIMIT,
) -> int:
    """Mark expired pending assignments abandoned and bound their history."""
    path = state_path()
    if not path.exists():
        return 0
    changed: list[tuple[str, tracking.ProfileAssignment]]
    with tracking._RecordLock(path, require_sidecar=True):
        state = _load_state(path)
        changed = _expire_pending_locked(
            state,
            now=now or _now(),
            ttl_seconds=ttl_seconds,
        )
        compacted = _compact_history(state, max(1, history_limit))
        if changed or compacted:
            _write_state(path, state)
    for worktree_id, assignment in changed:
        _sync_record_best_effort(worktree_id, assignment)
    return len(changed)


def maintain(
    *,
    now: datetime | None = None,
    ttl_seconds: int = DEFAULT_PENDING_TTL_SECONDS,
    history_limit: int = DEFAULT_HISTORY_LIMIT,
) -> int:
    """Best-effort expiry/compaction for safe status and lifecycle paths."""
    try:
        return expire_pending(
            now=now,
            ttl_seconds=ttl_seconds,
            history_limit=history_limit,
        )
    except (
        OSError,
        OverflowError,
        TimeoutError,
        ProfileAssignmentStateError,
        TypeError,
        ValueError,
    ) as exc:
        _diagnostic(
            "maintenance-state",
            "state maintenance was skipped; core status and session lifecycle "
            f"continue normally ({type(exc).__name__})",
        )
        return 0


def allocate(
    policy: cfg.ProfileAssignmentPolicy | None,
    profiles: list[cfg.CopilotProfile],
    *,
    worktree_id: str,
    lane: str,
    generation_key: str,
    now: datetime | None = None,
    seed: str | None = None,
    token: str | None = None,
    ttl_seconds: int = DEFAULT_PENDING_TTL_SECONDS,
    history_limit: int = DEFAULT_HISTORY_LIMIT,
    predecessor_session_id: str | None = None,
) -> LaunchProfileSelection:
    """Allocate or reuse one pending profile assignment under an atomic lock."""
    if policy is None or not policy.armed or lane not in policy.eligible_lanes:
        return LaunchProfileSelection(profile=None)
    validate_policy(policy, profiles)
    profile_by_name = _profile_map(profiles)
    current_time = now or _now()
    path = state_path()
    changed: list[tuple[str, tracking.ProfileAssignment]] = []

    with tracking._RecordLock(path, require_sidecar=True):
        state = _load_state(path)
        changed = _expire_pending_locked(
            state,
            now=current_time,
            ttl_seconds=ttl_seconds,
        )
        for raw in state.get("assignments", []):
            if (
                isinstance(raw, dict)
                and raw.get("disposition") == "pending"
                and raw.get("policy") == policy.name
                and raw.get("worktree_id") == worktree_id
                and raw.get("generation_key") == generation_key
            ):
                assignment = _record_from_state(raw)
                profile = profile_by_name.get(assignment.selected_profile)
                if profile is None:
                    raise ProfileAssignmentStateError(
                        "pending profile assignment references unavailable profile "
                        f"{assignment.selected_profile!r}"
                    )
                launch_token = str(raw.get("launch_token") or "")
                if not launch_token:
                    raise ProfileAssignmentStateError(
                        "pending profile assignment has no launch token"
                    )
                compacted = _compact_history(state, max(1, history_limit))
                if changed or compacted:
                    _write_state(path, state)
                break
        else:
            pool = tuple(policy.profiles)
            fingerprint = _pool_fingerprint(policy.name, pool)
            state_pool = tuple(
                str(item) for item in state.get("pool", []) if isinstance(item, str)
            )
            generation = tracking._bounded_nonnegative_int(
                state.get("generation"),
                field="generation",
            )
            if (
                state.get("policy") != policy.name
                or _pool_fingerprint(str(state.get("policy") or ""), state_pool)
                != fingerprint
            ):
                if state.get("policy") or state_pool:
                    generation = tracking._bounded_nonnegative_int(
                        generation + 1,
                        field="generation",
                    )
                state.update({
                    "policy": policy.name,
                    "seed": str(state.get("seed") or seed or secrets.token_hex(16)),
                    "pool": list(pool),
                    "generation": generation,
                    "position": 0,
                    "bag": [],
                })
            elif not state.get("seed"):
                state["seed"] = seed or secrets.token_hex(16)

            bag = [
                str(item) for item in state.get("bag", []) if isinstance(item, str)
            ]
            position = tracking._bounded_nonnegative_int(
                state.get("position"),
                field="position",
            )
            if not bag or position >= len(bag):
                if bag:
                    generation = tracking._bounded_nonnegative_int(
                        generation + 1,
                        field="generation",
                    )
                bag = _shuffled_bag(
                    str(state["seed"]),
                    policy.name,
                    pool,
                    generation,
                )
                position = 0

            selected_name = bag[position]
            launch_token = token or secrets.token_hex(16)
            assignment = tracking.ProfileAssignment(
                policy=policy.name,
                assignment_label=policy.assignment_label,
                selected_profile=selected_name,
                bag_generation=generation,
                bag_position=position,
                assigned_at=_timestamp(current_time),
                disposition="pending",
                lane=lane,
                predecessor_session_id=predecessor_session_id,
            )
            state.update({
                "policy": policy.name,
                "pool": list(pool),
                "generation": generation,
                "position": position + 1,
                "bag": bag,
            })
            state.setdefault("assignments", []).append(
                _state_from_record(
                    assignment,
                    launch_token=launch_token,
                    worktree_id=worktree_id,
                    generation_key=generation_key,
                )
            )
            _compact_history(state, max(1, history_limit))
            _write_state(path, state)
            profile = profile_by_name[selected_name]

    for changed_worktree, changed_assignment in changed:
        _sync_record_best_effort(changed_worktree, changed_assignment)
    _sync_record_best_effort(worktree_id, assignment)
    return LaunchProfileSelection(
        profile=profile,
        assignment=assignment,
        launch_token=launch_token,
    )


def allocate_best_effort(
    policy: cfg.ProfileAssignmentPolicy | None,
    profiles: list[cfg.CopilotProfile],
    *,
    fallback_profile: cfg.CopilotProfile | None = None,
    **kwargs: Any,
) -> LaunchProfileSelection:
    """Allocate when possible without making optional state load-bearing."""
    validate_policy(policy, profiles)
    try:
        return _with_fallback_profile(
            allocate(policy, profiles, **kwargs),
            fallback_profile,
        )
    except (
        OSError,
        OverflowError,
        TimeoutError,
        ProfileAssignmentStateError,
        TypeError,
        ValueError,
    ) as exc:
        warning = _diagnostic(
            "allocation-state",
            "assignment state is unavailable; continuing with the ordinary "
            f"launch profile ({type(exc).__name__})",
        )
        return LaunchProfileSelection(
            profile=fallback_profile,
            warning=warning,
        )


def bind(
    launch_token: str | None,
    session_id: str,
    worktree_id: str,
    *,
    now: datetime | None = None,
) -> tracking.ProfileAssignment | None:
    """Best-effort bind of one token-keyed assignment to a Copilot session."""
    if not launch_token:
        return None
    path = state_path()
    bound: tracking.ProfileAssignment | None = None
    changed: list[tuple[str, tracking.ProfileAssignment]] = []
    current_time = now or _now()
    digest = _token_digest(launch_token)
    try:
        with tracking._RecordLock(path, require_sidecar=True):
            state = _load_state(path)
            changed = _expire_pending_locked(
                state,
                now=current_time,
                ttl_seconds=DEFAULT_PENDING_TTL_SECONDS,
            )
            for raw in state.get("assignments", []):
                if not isinstance(raw, dict):
                    continue
                token_matches = raw.get("launch_token") == launch_token
                retired_matches = raw.get("retired_token_digest") == digest
                if not token_matches and not retired_matches:
                    continue
                raw_worktree = str(raw.get("worktree_id") or "")
                if raw_worktree != worktree_id:
                    _diagnostic(
                        "bind-foreign",
                        "the launch token belongs to another worktree; session "
                        "registration continues without assignment binding",
                    )
                    break
                disposition = raw.get("disposition")
                if disposition == "abandoned":
                    _diagnostic(
                        "bind-expired",
                        "the launch token expired before session registration; "
                        "registration continues without assignment binding",
                    )
                    break
                existing_session = raw.get("session_id")
                if disposition == "bound":
                    if existing_session != session_id:
                        _diagnostic(
                            "bind-already-bound",
                            "the launch token was already retired for another "
                            "session; registration continues without rebinding",
                        )
                    bound = (
                        _record_from_state(raw)
                        if existing_session == session_id
                        else None
                    )
                    break
                raw["disposition"] = "bound"
                raw["session_id"] = session_id
                raw["bound_at"] = _timestamp(current_time)
                raw.pop("abandoned_at", None)
                _retire_token(raw)
                bound = _record_from_state(raw)
                break
            else:
                _diagnostic(
                    "bind-unknown",
                    "the launch token is unknown or already retired; session "
                    "registration continues without assignment binding",
                )
            if bound is not None or changed:
                _compact_history(state, DEFAULT_HISTORY_LIMIT)
                _write_state(path, state)
    except (
        OSError,
        OverflowError,
        TimeoutError,
        ProfileAssignmentStateError,
        TypeError,
        ValueError,
    ) as exc:
        _diagnostic(
            "bind-state",
            "assignment binding was skipped; session registration continues "
            f"normally ({type(exc).__name__})",
        )
        return None
    for changed_worktree, changed_assignment in changed:
        _sync_record_best_effort(changed_worktree, changed_assignment)
    if bound is not None:
        _sync_record_best_effort(worktree_id, bound)
    return bound


def assignment_for_session(
    record: tracking.WorktreeRecord,
    session_id: str | None,
) -> tracking.ProfileAssignment | None:
    """Return the newest bound assignment for ``session_id`` from the record."""
    if not session_id:
        return None
    for assignment in reversed(getattr(record, "profile_assignments", [])):
        if (
            assignment.disposition == "bound"
            and assignment.session_id == session_id
        ):
            return assignment
    return None


def replay(
    assignment: tracking.ProfileAssignment | None,
    profiles: list[cfg.CopilotProfile],
    *,
    fallback_profile: cfg.CopilotProfile | None = None,
) -> LaunchProfileSelection:
    """Resolve a persisted session assignment back to an ordinary profile."""
    if assignment is None:
        return LaunchProfileSelection(profile=fallback_profile)
    profile = _profile_map(profiles).get(assignment.selected_profile)
    if profile is None:
        warning = _diagnostic(
            "replay-missing-profile",
            "the saved assignment profile is unavailable on this machine; "
            "resume continues with the ordinary default/manual profile",
        )
        return LaunchProfileSelection(
            profile=fallback_profile,
            warning=warning,
        )
    return LaunchProfileSelection(profile=profile, assignment=assignment)


def metadata(assignment: tracking.ProfileAssignment) -> dict[str, object]:
    """Render neutral machine-readable assignment metadata."""
    return {
        "policy": assignment.policy,
        "assignment_label": assignment.assignment_label,
        "selected_profile": assignment.selected_profile,
        "bag_generation": assignment.bag_generation,
        "bag_position": assignment.bag_position,
        "assigned_at": assignment.assigned_at,
        "disposition": assignment.disposition,
        "session_id": assignment.session_id,
        "lane": assignment.lane,
        "abandoned_at": assignment.abandoned_at,
        "bound_at": assignment.bound_at,
        "predecessor_session_id": assignment.predecessor_session_id,
    }

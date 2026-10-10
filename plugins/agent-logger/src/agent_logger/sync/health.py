"""Health classification for persisted session-sync metadata."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from agent_logger.sync.targets.base import SyncStatus

MAX_DEFERRED_FILE_SAMPLES = 10
MAX_DEFERRED_PATH_CHARS = 512


@dataclass
class SyncHealth:
    machine: str
    health: str
    reason: str
    last_sync_utc: str | None = None
    age_hours: float | None = None
    latest_status: str | None = None
    consecutive_partial_count: int = 0
    session_count: int | None = None
    deferred_file_count: int | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _integer(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _health_count(value: object) -> int:
    count = _integer(value)
    if count is None:
        raise OSError("invalid sync health count")
    return count


def _health_samples(value: object, *, limit: int, chars: int) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise OSError("invalid sync health deferred samples")
    return [item[:chars] for item in value[:limit]]


def _health_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or len(value) != 20:
        raise OSError("invalid sync health timestamp")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise OSError("invalid sync health timestamp") from exc


def _validated_legs(stored: object) -> dict[str, dict[str, Any]]:
    if (
        not isinstance(stored, dict)
        or "session-state" not in stored
        or set(stored) - {"session-state", "process-logs"}
    ):
        raise OSError("invalid sync health legs")
    legs: dict[str, dict[str, Any]] = {}
    for name in ("session-state", "process-logs"):
        if name not in stored:
            continue
        entry = stored[name]
        if not isinstance(entry, dict):
            raise OSError("invalid sync health leg")
        status = entry.get("status")
        if not isinstance(status, str) or len(status) > 256:
            raise OSError("invalid sync health leg status")
        _health_timestamp(entry.get("last_attempt_utc"))
        _health_timestamp(entry.get("last_checked_utc"))
        streak = _health_count(entry.get("consecutive_partial_count"))
        count = _health_count(entry.get("deferred_file_count"))
        samples = _health_samples(
            entry.get("deferred_files"),
            limit=MAX_DEFERRED_FILE_SAMPLES,
            chars=MAX_DEFERRED_PATH_CHARS,
        )
        if (
            samples != entry["deferred_files"]
            or count < len(samples)
            or (status == "ok" and (streak or count))
            or (status == "partial" and streak < 1)
        ):
            raise OSError("inconsistent sync health leg")
        legs[name] = {
            "status": status,
            "last_attempt_utc": entry["last_attempt_utc"],
            "last_checked_utc": entry["last_checked_utc"],
            "consecutive_partial_count": streak,
            "deferred_file_count": count,
            "deferred_files": samples,
        }
    return legs


def _aggregate_health(legs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    invalid_status = next(
        (entry["status"] for entry in legs.values() if entry["status"] not in {"ok", "partial"}),
        None,
    )
    return {
        "status": (
            invalid_status
            if invalid_status is not None
            else "partial"
            if any(entry["status"] == "partial" for entry in legs.values())
            else "ok"
        ),
        "consecutive_partial_count": max(
            entry["consecutive_partial_count"] for entry in legs.values()
        ),
        "deferred_file_count": sum(entry["deferred_file_count"] for entry in legs.values()),
        "deferred_files": [path for entry in legs.values() for path in entry["deferred_files"]][
            :MAX_DEFERRED_FILE_SAMPLES
        ],
    }


def migrate_legacy_health(metadata: dict[str, Any]) -> None:
    """Attribute legacy health conservatively without inventing an attempt."""
    _health_timestamp(metadata.get("last_sync_utc"))
    status = metadata.get("status")
    if not isinstance(status, str):
        raise OSError("invalid legacy sync status")
    streak = _health_count(metadata.get("consecutive_partial_count", 0))
    if status == "partial":
        streak = max(1, streak)
    entry = {
        "status": status[:256],
        "last_attempt_utc": metadata["last_sync_utc"],
        "last_checked_utc": metadata["last_sync_utc"],
        "consecutive_partial_count": streak,
        "deferred_file_count": _health_count(metadata.get("deferred_file_count", 0)),
        "deferred_files": _health_samples(
            metadata.get("deferred_files", []),
            limit=MAX_DEFERRED_FILE_SAMPLES,
            chars=MAX_DEFERRED_PATH_CHARS,
        ),
    }
    metadata["sync_legs"] = {"session-state": entry}
    metadata.update(_aggregate_health(metadata["sync_legs"]))


def merge_health(
    metadata: dict[str, Any],
    previous: dict[str, Any] | None,
    *,
    leg: Literal["session-state", "process-logs"],
    status: str,
    deferred: list[str],
    sample_limit: int,
    sample_chars: int,
) -> None:
    """Update one leg; another leg's success cannot clear its failure streak."""
    status = status[:256]
    prior = previous or {}
    stored = prior.get("sync_legs")
    legs: dict[str, dict[str, Any]] = {}
    if "sync_legs" in prior:
        legs = _validated_legs(stored)
    elif prior:
        # Legacy partial metadata cannot identify the failed leg. A log-only
        # retry must not clear it until session-state is actually transferred.
        migrated = dict(prior)
        migrate_legacy_health(migrated)
        legs = migrated["sync_legs"]
    previous_leg = legs.get(leg, {})
    streak = 0
    if status == "partial":
        streak = min(previous_leg.get("consecutive_partial_count", 0), 999_999) + 1
    legs[leg] = {
        "status": status,
        "last_attempt_utc": metadata["last_sync_utc"],
        "last_checked_utc": metadata["last_sync_utc"],
        "consecutive_partial_count": streak,
        "deferred_file_count": len(deferred),
        "deferred_files": [path[:sample_chars] for path in deferred[:sample_limit]],
    }
    metadata["sync_legs"] = legs
    metadata.update(_aggregate_health(legs))


def classify_sync_health(
    machine: str,
    status: SyncStatus,
    *,
    max_age_hours: float,
    partial_threshold: int,
    now: datetime | None = None,
) -> SyncHealth:
    """Classify one machine's latest result for automation and fleet summaries."""
    if status.error:
        return SyncHealth(machine, "unhealthy", "unreadable_metadata")
    if status.metadata is None:
        return SyncHealth(machine, "unhealthy", "missing_metadata")

    metadata = status.metadata
    raw_timestamp = metadata.get("last_sync_utc")
    if not isinstance(raw_timestamp, str):
        return SyncHealth(machine, "unhealthy", "invalid_timestamp")
    try:
        timestamp = datetime.strptime(raw_timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return SyncHealth(
            machine,
            "unhealthy",
            "invalid_timestamp",
            last_sync_utc=raw_timestamp,
        )

    current = now or datetime.now(timezone.utc)
    if "sync_legs" in metadata:
        try:
            legs = _validated_legs(metadata["sync_legs"])
            aggregate = _aggregate_health(legs)
        except OSError:
            return SyncHealth(machine, "unhealthy", "invalid_leg_metadata")
        if any(
            _integer(metadata.get(key)) is None
            for key in ("consecutive_partial_count", "deferred_file_count")
        ):
            return SyncHealth(machine, "unhealthy", "invalid_leg_metadata")
        if any(metadata.get(key) != value for key, value in aggregate.items()):
            return SyncHealth(machine, "unhealthy", "inconsistent_leg_metadata")
        for entry in legs.values():
            timestamp = min(timestamp, _health_timestamp(entry["last_checked_utc"]))
    age_hours = max(0.0, (current - timestamp).total_seconds() / 3600)
    latest_status = metadata.get("status")
    partial_streak = _integer(metadata.get("consecutive_partial_count"))
    if latest_status == "partial" and partial_streak is None:
        partial_streak = 1
    partial_streak = partial_streak or 0
    common = SyncHealth(
        machine,
        "unhealthy",
        "invalid_status",
        last_sync_utc=raw_timestamp,
        age_hours=round(age_hours, 3),
        latest_status=latest_status if isinstance(latest_status, str) else None,
        consecutive_partial_count=partial_streak,
        session_count=_integer(metadata.get("session_count")),
        deferred_file_count=_integer(metadata.get("deferred_file_count")),
    )

    if age_hours > max_age_hours:
        return replace(common, reason="stale")
    if latest_status == "ok":
        return replace(common, health="healthy", reason="fresh_complete")
    if latest_status == "partial":
        if partial_streak >= partial_threshold:
            return replace(common, reason="repeated_partial")
        return replace(common, health="degraded", reason="transient_partial")
    return common

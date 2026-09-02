"""Health classification for persisted session-sync metadata."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from agent_logger.sync.targets.base import SyncStatus


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


def _integer(value) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


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
    age_hours = max(0.0, (current - timestamp).total_seconds() / 3600)
    latest_status = metadata.get("status")
    partial_streak = _integer(metadata.get("consecutive_partial_count"))
    if latest_status == "partial" and partial_streak is None:
        partial_streak = 1
    partial_streak = partial_streak or 0
    common = {
        "last_sync_utc": raw_timestamp,
        "age_hours": round(age_hours, 3),
        "latest_status": latest_status if isinstance(latest_status, str) else None,
        "consecutive_partial_count": partial_streak,
        "session_count": _integer(metadata.get("session_count")),
        "deferred_file_count": _integer(metadata.get("deferred_file_count")),
    }

    if age_hours > max_age_hours:
        return SyncHealth(machine, "unhealthy", "stale", **common)
    if latest_status == "ok":
        return SyncHealth(machine, "healthy", "fresh_complete", **common)
    if latest_status == "partial":
        if partial_streak >= partial_threshold:
            return SyncHealth(machine, "unhealthy", "repeated_partial", **common)
        return SyncHealth(machine, "degraded", "transient_partial", **common)
    return SyncHealth(machine, "unhealthy", "invalid_status", **common)

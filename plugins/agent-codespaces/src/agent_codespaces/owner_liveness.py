"""Value-only Connection Owner beacon identity and freshness."""

from __future__ import annotations

from dataclasses import dataclass
import time

_LIVE_STALE_INTERVALS = 3
_LIVE_STALE_FLOOR = 45.0
_LIVE_FUTURE_TOLERANCE = 5.0


@dataclass
class OwnerLiveness:
    """A snapshot of the running Connection Owner daemon's liveness beacon."""

    pid: int
    host: str
    heartbeat_at: float
    interval: float
    process_started_at: float = 0.0
    process_identity: str | None = None
    active: tuple[str, ...] = ()
    bridge_forwards: tuple[str, ...] = ()
    version: str = ""

    def staleness_threshold(self) -> float:
        return max(_LIVE_STALE_FLOOR, _LIVE_STALE_INTERVALS * max(self.interval, 0.0))

    def is_fresh(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        age = now - self.heartbeat_at
        if age < 0:
            return age >= -_LIVE_FUTURE_TOLERANCE
        return age <= self.staleness_threshold()

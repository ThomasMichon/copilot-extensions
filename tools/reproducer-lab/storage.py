"""Monitored storage thresholds, never write-time quotas or lab admission."""

from __future__ import annotations

import math
from dataclasses import dataclass

from boundary import Experiment


@dataclass(frozen=True)
class VolumeIdentity:
    name: str
    created_at: str
    mountpoint: str


@dataclass(frozen=True)
class Resources:
    container_id: str
    volumes: tuple[VolumeIdentity, ...]


@dataclass(frozen=True)
class Usage:
    resources: Resources
    started: float
    finished: float
    persistent_bytes: int
    output_bytes: int
    minimum_free_bytes: int


@dataclass(frozen=True)
class MonitoringPolicy:
    cadence_seconds: int
    lifetime_seconds: int
    minimum_free_bytes: int

    def __post_init__(self) -> None:
        for value, ceiling in (
            (self.cadence_seconds, 60),
            (self.lifetime_seconds, 3600),
            (self.minimum_free_bytes, 4 * 1024**3),
        ):
            if type(value) is not int or not 0 < value <= ceiling:
                raise ValueError("monitoring policy must use positive bounded integers")
        if self.lifetime_seconds < self.cadence_seconds:
            raise ValueError("monitor lifetime must cover at least one observation cadence")


class StorageMonitor:
    """One active caller's observations; a new/resumed owner starts unmeasured."""

    def __init__(
        self, experiment: Experiment, resources: Resources, policy: MonitoringPolicy, *, now: float,
    ) -> None:
        if not math.isfinite(now):
            raise ValueError("monitor start must be finite monotonic time")
        self.experiment = experiment
        self.resources = resources
        self.policy = policy
        self.started = now
        self.latest: Usage | None = None
        self.closed = False

    def invalidate(self) -> None:
        """Call before each attempted refresh, so a failed read cannot reuse success."""
        self.latest = None

    def record(self, usage: Usage, *, now: float) -> None:
        self.invalidate()
        if self.closed or usage.resources != self.resources:
            raise ValueError("observation does not belong to this active monitor")
        if not all(math.isfinite(value) for value in (usage.started, usage.finished, now)):
            raise ValueError("observation time must be finite")
        if not self.started <= usage.started <= usage.finished <= now:
            raise ValueError("observation is outside this monitor's lifetime")
        if any(type(value) is not int or value < 0 for value in (
            usage.persistent_bytes, usage.output_bytes, usage.minimum_free_bytes,
        )):
            raise ValueError("usage must contain nonnegative integer byte counts")
        self.latest = usage

    def close(self) -> None:
        self.closed = True
        self.invalidate()

    def refusals(self, *, now: float) -> tuple[str, ...]:
        """Storage-only refusal reasons for NEW work; never stop accepted work."""
        if not math.isfinite(now) or now < self.started:
            return ("monotonic observation time is invalid",)
        reasons: list[str] = []
        if self.closed:
            reasons.append("host monitor is closed")
        if now >= self.started + self.policy.lifetime_seconds:
            reasons.append("active-case observation lifetime has expired")
        usage = self.latest
        if usage is None:
            reasons.append("fresh storage measurement is unavailable")
            return tuple(reasons)
        # Age includes sampling time, rather than renewing a slow scan at completion.
        if now < usage.finished or now - usage.started >= self.policy.cadence_seconds:
            reasons.append("storage measurement is stale or from a future clock")
        budgets = self.experiment.budgets
        if usage.persistent_bytes >= budgets.persistent_bytes:
            reasons.append("persistent usage reached its monitored threshold")
        if usage.output_bytes >= budgets.output_bytes:
            reasons.append("evidence usage reached its monitored output threshold")
        if usage.minimum_free_bytes <= self.policy.minimum_free_bytes:
            reasons.append("backing free space reached its monitored reserve")
        return tuple(reasons)

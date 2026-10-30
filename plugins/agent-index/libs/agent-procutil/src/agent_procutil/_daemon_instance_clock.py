"""Lease clock bounds; Linux timestamps also share a boot/namespace domain."""

from __future__ import annotations

import math
from pathlib import Path


def domain() -> str:
    boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    namespace = Path("/proc/self/ns/time").stat()
    if not boot:
        raise ValueError("daemon monotonic clock boot identity unavailable")
    return f"linux-monotonic:{boot}:{namespace.st_dev}:{namespace.st_ino}"


def fields(ttl: float, maximum: float, now: float) -> dict[str, str]:
    if not all(math.isfinite(value) for value in (ttl, maximum, now, now + maximum)) or not (
        now >= 0 and 0 < ttl <= maximum
    ):
        raise ValueError("invalid daemon monotonic lifetime")
    return {
        "schema": "2", "clock_domain": domain(),
        "deadline_mono": str(now + ttl), "hard_deadline_mono": str(now + maximum),
    }


def bounds(record: dict[str, str]) -> tuple[float, float]:
    if record.get("schema") != "2":
        raise ValueError("Linux disposable lease requires monotonic record schema 2")
    if record.get("clock_domain") != domain():
        raise ValueError("daemon monotonic clock domain differs or is unavailable")
    deadline, hard = float(record["deadline_mono"]), float(record["hard_deadline_mono"])
    if not all(math.isfinite(value) for value in (deadline, hard)) or not (
        0 <= deadline <= hard and hard > 0
    ):
        raise ValueError("invalid daemon monotonic expiry policy")
    return deadline, hard


class WallLeaseObservation:
    """Keep a schema-1 lease's original hard cap on an observer-local clock."""

    def __init__(self, record: dict[str, str], *, wall: float, monotonic: float):
        deadline, self.hard_wall = self._bounds(record)
        self.hard_monotonic = monotonic + max(0, self.hard_wall - wall)
        self.soft_monotonic = monotonic + max(0, deadline - wall)
        self.updated = record["updated"]

    @staticmethod
    def _bounds(record: dict[str, str]) -> tuple[float, float]:
        if record.get("schema") != "1" or record.get("purpose") not in (
            "unattended-test", "interactive-test",
        ):
            raise ValueError("observer requires a disposable schema-1 lease")
        deadline, hard = float(record["deadline"]), float(record["hard_deadline"])
        if not all(math.isfinite(value) for value in (deadline, hard)) or not (
            0 < deadline <= hard
        ):
            raise ValueError("invalid daemon expiry policy")
        return deadline, hard

    def past_hard(self, monotonic: float) -> bool:
        return monotonic >= self.hard_monotonic

    def expired(self, record: dict[str, str], *, wall: float, monotonic: float) -> bool:
        deadline, hard = self._bounds(record)
        if hard != self.hard_wall:
            raise ValueError("daemon hard bound changed after admission")
        if record["updated"] != self.updated:
            self.soft_monotonic = monotonic + max(0, deadline - wall)
            self.updated = record["updated"]
        return (
            wall >= deadline or monotonic >= self.soft_monotonic
            or self.past_hard(monotonic)
        )

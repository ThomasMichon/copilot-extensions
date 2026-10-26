"""Linux lease timestamps share a boot and time-namespace clock domain."""

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

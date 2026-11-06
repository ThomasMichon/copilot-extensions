"""Bounded, content-free accounting for one native metrics operation."""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

log = logging.getLogger(__name__)
Clock = Callable[[], float | None]


@dataclass(frozen=True)
class SampleAccounting:
    instance: str
    sequence: int
    state: Literal["pending", "succeeded", "failed"]
    started_at: float | None = None
    completed_at: float | None = None
    wall_seconds: float | None = None
    thread_cpu_seconds: float | None = None
    issues: tuple[str, ...] = ()


def _read_clock(clock: Clock, label: str) -> tuple[float | None, tuple[str, ...]]:
    try:
        value = clock()
        if value is None:
            reason = "unavailable"
        elif isinstance(value, bool) or not math.isfinite(value) or value < 0:
            reason = "invalid"
        else:
            return value, ()
    except Exception as error:
        log.warning("Sample %s clock failed (%s)", label, type(error).__name__)
        return None, (f"{label}_failed",)
    log.warning("Sample %s clock is %s", label, reason)
    return None, (f"{label}_{reason}",)


def _elapsed(
    start: float | None, end: float | None, label: str,
) -> tuple[float | None, tuple[str, ...]]:
    if start is None or end is None:
        return None, ()
    delta = end - start
    if not math.isfinite(delta) or delta < 0:
        log.warning("Sample %s clock regressed or produced invalid elapsed time", label)
        return None, (f"{label}_regressed",)
    return delta, ()


class _SampleTimer:
    def __init__(
        self, instance: str, sequence: int, wall_clock: Clock, thread_clock: Clock,
    ) -> None:
        self._wall_clock = wall_clock
        self._thread_clock = thread_clock
        started_at, wall_issues = _read_clock(wall_clock, "wall_start")
        self._cpu_start, cpu_issues = _read_clock(thread_clock, "thread_cpu_start")
        self.pending = SampleAccounting(
            instance, sequence, "pending", started_at=started_at,
            issues=wall_issues + cpu_issues,
        )

    def complete(self, state: Literal["succeeded", "failed"]) -> SampleAccounting:
        cpu_end, cpu_issues = _read_clock(self._thread_clock, "thread_cpu_completion")
        completed_at, wall_issues = _read_clock(self._wall_clock, "wall_completion")
        wall, wall_delta_issues = _elapsed(self.pending.started_at, completed_at, "wall")
        cpu, cpu_delta_issues = _elapsed(self._cpu_start, cpu_end, "thread_cpu")
        return SampleAccounting(
            self.pending.instance, self.pending.sequence, state,
            started_at=self.pending.started_at,
            completed_at=None if wall_delta_issues else completed_at,
            wall_seconds=wall, thread_cpu_seconds=cpu,
            issues=(
                self.pending.issues + wall_issues + cpu_issues
                + wall_delta_issues + cpu_delta_issues
            ),
        )

"""Cooperative raw-work admission shared by cache iterators and their collector."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class WorkBudget:
    remaining: int
    deadline: float
    cancelled: Callable[[], bool]
    clock: Callable[[], float] = time.monotonic
    stopped: bool = False

    def available(self) -> bool:
        self.stopped = (
            self.stopped or self.remaining <= 0
            or self.clock() >= self.deadline or self.cancelled()
        )
        return not self.stopped

    def consume(self) -> bool:
        if not self.available():
            return False
        self.remaining -= 1
        return True

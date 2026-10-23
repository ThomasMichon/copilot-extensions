"""Independent startup observation clocks; expiry grants no cancellation authority."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

PROGRESS_PREFIX = "@@agent-startup "


@dataclass(frozen=True)
class StartupTimeouts:
    communication: float = 30.0
    process: float = 300.0
    expected: float = 60.0

    def __post_init__(self) -> None:
        for name in ("communication", "process", "expected"):
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"startup {name} budget must be finite and positive")


@dataclass(frozen=True)
class StartupExpiry:
    clock: str
    elapsed: float
    silence: float
    phase: str


class StartupClock:
    """Receipt-time liveness and a fixed lifetime, ending at startup readiness."""

    def __init__(
        self,
        policy: StartupTimeouts,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.policy = policy
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._started = self._contact = monotonic()
        self._phase = "startup"
        self._ready = False

    def receive(self, phase: str | None = None) -> None:
        """Every target update counts, independently of recognized event meaning."""
        with self._lock:
            self._contact = self._monotonic()
            if phase is not None:
                self._phase = phase

    def mark_ready(self) -> None:
        """Readiness ends startup clocks, not an agent's subsequent turn."""
        with self._lock:
            self._ready = True

    @property
    def elapsed(self) -> float:
        with self._lock:
            return max(0.0, self._monotonic() - self._started)

    @property
    def ready(self) -> bool:
        with self._lock:
            return self._ready

    def remaining(self) -> float | None:
        with self._lock:
            if self._ready:
                return None
            now = self._monotonic()
            return max(0.0, min(
                self.policy.process - (now - self._started),
                self.policy.communication - (now - self._contact),
            ))

    def process_remaining(self) -> float:
        with self._lock:
            return max(0.0, self.policy.process - (self._monotonic() - self._started))

    def expired(self) -> StartupExpiry | None:
        with self._lock:
            if self._ready:
                return None
            now = self._monotonic()
            elapsed = max(0.0, now - self._started)
            silence = max(0.0, now - self._contact)
            if elapsed >= self.policy.process:
                return StartupExpiry("process", elapsed, silence, self._phase)
            if silence >= self.policy.communication:
                return StartupExpiry("communication", elapsed, silence, self._phase)
            return None

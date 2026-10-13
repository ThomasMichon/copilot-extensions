"""Provider-local wall-overrun admission, independent of worker lifetime."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ReaderAdmission:
    consecutive_overruns: int = 0
    retry_at: float | None = None
    probing: bool = False
    suppressed: int = 0

    def admit(self, now: float) -> bool:
        if self.retry_at is None:
            return True
        if now < self.retry_at or self.probing:
            self.suppressed += 1
            return False
        self.probing = True
        return True

    def complete(self, now: float, *, timed_out: bool, native_success: bool) -> None:
        if native_success:
            self.consecutive_overruns = 0
            self.retry_at = None
            self.probing = False
            return
        if timed_out:
            self.consecutive_overruns += 1
        else:
            self.consecutive_overruns = 0
        if self.probing or self.consecutive_overruns >= 2:
            self.retry_at = now + 60.0
        self.probing = False

"""Bounded operational-warning selection."""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass, field

from .model import Finding


@dataclass(frozen=True)
class WarningBatch:
    """Detailed findings selected for one operational emission."""

    emitted: tuple[Finding, ...]
    suppressed: int = 0
    recovered: int = 0


@dataclass
class WarningTracker:
    """Fingerprint-deduplicate findings while preserving exhaustive doctor data."""

    limit: int = 10
    repeat_after_seconds: float = 3600.0
    _seen: dict[str, float] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.limit < 0:
            raise ValueError("limit must be non-negative")
        if self.repeat_after_seconds < 0:
            raise ValueError("repeat_after_seconds must be non-negative")

    def select(
        self,
        findings: Iterable[Finding],
        *,
        now: float | None = None,
    ) -> WarningBatch:
        """Select at most ``limit`` details and summarize the rest.

        Every current fingerprint is marked observed, including suppressed
        findings: repeated sweeps stay quiet and direct the operator to exhaustive
        doctor output rather than slowly leaking every stale entry into logs.
        """
        instant = time.monotonic() if now is None else now
        ordered = sorted(
            findings,
            key=lambda f: (f.registry, f.entry, f.reason, f.target or ""),
        )
        current = {finding.fingerprint() for finding in ordered}
        recovered = len(set(self._seen) - current)
        for fingerprint in tuple(self._seen):
            if fingerprint not in current:
                del self._seen[fingerprint]

        candidates: list[Finding] = []
        processed: set[str] = set()
        for finding in ordered:
            fingerprint = finding.fingerprint()
            if fingerprint in processed:
                continue
            processed.add(fingerprint)
            last = self._seen.get(fingerprint)
            if last is None or instant - last >= self.repeat_after_seconds:
                candidates.append(finding)
                self._seen[fingerprint] = instant

        emitted = tuple(candidates[: self.limit])
        return WarningBatch(
            emitted=emitted,
            suppressed=max(0, len(candidates) - len(emitted)),
            recovered=recovered,
        )

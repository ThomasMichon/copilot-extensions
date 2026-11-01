"""Portable process-observation errors without loading native bindings."""

from __future__ import annotations


class _CensusChanged(ValueError):
    """Discard every row from this pass and obtain a fresh complete census."""

    def __init__(
        self, reason: str, *, pid: int = 0, flavor: int = 0,
        phase: str, native_errno: int = 0, observed_pgid: int = 0,
    ) -> None:
        self.reason = reason
        self.pid = pid
        self.flavor = flavor
        self.phase = phase
        self.native_errno = native_errno
        self.observed_pgid = observed_pgid
        super().__init__(
            f"{reason} pid={pid} flavor={flavor} phase={phase} "
            f"errno={native_errno} observed_pgid={observed_pgid}"
        )


class GroupObservationChanged(ValueError):
    """Known census churn exhausted one bounded observation, not an empty group."""

    def __init__(self, last: _CensusChanged) -> None:
        self.last = last
        self.pid, self.flavor, self.phase = last.pid, last.flavor, last.phase
        self.native_errno = last.native_errno
        super().__init__(f"Darwin process-group observation: group churn exceeded; last={last}")


class _ObservationDeadline(TimeoutError):
    """The caller's current sampling cycle has no observation budget left."""

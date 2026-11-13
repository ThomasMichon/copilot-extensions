"""Shared absolute budgets for finite execution and native settlement."""

from dataclasses import dataclass
import math
import time


@dataclass(frozen=True)
class OwnedExecutionBudget:
    """One monotonic deadline, including cleanup, shared by sequential observations.

    Execution stops before ``deadline`` by ``cleanup_reserve`` seconds.
    The reserve is not renewed by another child, stream worker or guardian.
    Callers retain a separate response margin outside this budget.
    """

    deadline: float
    cleanup_reserve: float

    def __post_init__(self):
        for value in (self.deadline, self.cleanup_reserve):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError("owned deadline and cleanup reserve must be finite and positive")

    @classmethod
    def from_timeout(cls, timeout: float, *, cleanup_reserve: float):
        """Start one aggregate clock; expired budgets are never renewed."""
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("owned aggregate timeout must be finite and positive")
        if cleanup_reserve >= timeout:
            raise ValueError("cleanup reserve must be smaller than aggregate timeout")
        return cls(time.monotonic() + timeout, cleanup_reserve)

    @property
    def execution_deadline(self):
        return self.deadline - self.cleanup_reserve

    def cleanup_timeout(self, maximum: float = 2.0):
        """Bound each settlement wait by the same remaining absolute lifetime."""
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("owned aggregate settlement deadline expired")
        return min(maximum, remaining)

    def enclosed_budget(self):
        """Settle and publish a nested owner before this owner's read cutoff."""
        publication = min(.25, self.cleanup_reserve / 4)
        reserve = self.cleanup_reserve / 2 - publication
        return OwnedExecutionBudget(self.execution_deadline + reserve, reserve)

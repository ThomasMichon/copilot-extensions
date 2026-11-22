"""Validated per-attempt push deadlines and dependent publication lock budgets."""

from __future__ import annotations

import math

from .push_timeout import DEFAULT_PUSH_TIMEOUT
from .git_acquisition_budget import NETWORK_HARD_TIMEOUT

# Poll-based waits use signed 32-bit millisecond budgets on supported platforms.
MAX_WAIT_SECONDS = (2**31 - 1) / 1000
# Base/fork fetches, reconciliation, head proofs, readback and shared discovery.
_HOLDER_NETWORK_READS = 2 + 2 + 2 + 1 + 1
_HOLDER_LOCAL_SECONDS = 90.0


def _holder_budget(timeout: float) -> float:
    return 4 * timeout + _HOLDER_NETWORK_READS * NETWORK_HARD_TIMEOUT + _HOLDER_LOCAL_SECONDS


def validate(value: object, *, location: str = "pr.push_timeout_seconds") -> float:
    """Reject settings that would disable or overflow a bounded publication."""
    error = f"{location} must be a finite positive number of seconds"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(error)
    try:
        timeout = float(value)
    except (OverflowError, ValueError):
        raise ValueError(error) from None
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError(error)
    if 2 * _holder_budget(timeout) > MAX_WAIT_SECONDS:
        raise ValueError(
            f"{location} must keep dependent wait budgets at or below {MAX_WAIT_SECONDS:.3f} seconds"
        )
    return timeout


def configured(prcfg: object) -> float:
    return validate(getattr(prcfg, "push_timeout_seconds", DEFAULT_PUSH_TIMEOUT))


def lock_wait(timeout: float) -> float:
    """Allow a complete authority holder, including fork acquisition and recovery."""
    return _holder_budget(validate(timeout))


def lifecycle_budget(prcfg: object) -> float:
    """Cover an existing authority holder and our own complete publication."""
    return 2 * lock_wait(configured(prcfg))

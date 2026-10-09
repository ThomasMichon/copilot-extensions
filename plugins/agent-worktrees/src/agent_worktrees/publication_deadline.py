"""Validated per-attempt push deadlines and dependent publication lock budgets."""

from __future__ import annotations

import math

from .push_timeout import DEFAULT_PUSH_TIMEOUT

# Poll-based waits use signed 32-bit millisecond budgets on supported platforms.
MAX_WAIT_SECONDS = (2**31 - 1) / 1000


def validate(value: object) -> float:
    """Reject settings that would disable or overflow a bounded publication."""
    error = "pr.push_timeout_seconds must be a finite positive number of seconds"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(error)
    try:
        timeout = float(value)
    except (OverflowError, ValueError):
        raise ValueError(error) from None
    if not math.isfinite(timeout) or timeout <= 0 or 4 * timeout + 180 > MAX_WAIT_SECONDS:
        raise ValueError(error)
    return timeout


def configured(prcfg) -> float:
    return validate(getattr(prcfg, "push_timeout_seconds", DEFAULT_PUSH_TIMEOUT))


def lock_wait(timeout: float) -> float:
    """Allow both authentication attempts and process-tree cleanup."""
    return 2 * validate(timeout) + 30.0


def lifecycle_budget(prcfg) -> float:
    """Cover waiting for a publisher, our own push, and fetch/rebase overhead."""
    return 2 * lock_wait(configured(prcfg)) + 120.0

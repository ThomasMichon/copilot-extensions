"""Request-scoped native acquisition budget, independent of installer imports."""

from __future__ import annotations

import math

TIMEOUT_ENV = "WORKTREE_MANAGER_ENGINE_ACQUISITION_TIMEOUT_SEC"
DEFAULT_TIMEOUT_S = 180.0


def acquisition_timeout(environment: dict[str, str]) -> float:
    value = environment.get(TIMEOUT_ENV)
    if value is None:
        return DEFAULT_TIMEOUT_S
    message = f"{TIMEOUT_ENV} must be a positive finite number no greater than 3600"
    try:
        timeout = float(value)
    except (TypeError, ValueError):
        raise ValueError(message) from None
    if not math.isfinite(timeout) or not 0 < timeout <= 3600:
        raise ValueError(message)
    return timeout

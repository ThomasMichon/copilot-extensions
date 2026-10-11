"""Validate environment-wait passthrough before any container work.

Each wrapper invocation owns an isolated workspace and environment. Exclusion
of preparation and use belongs to the inner runner, not a host-global lease.
"""

from __future__ import annotations

import math


def resolve_admission_wait(passthrough: list[str], canonicalize) -> float:
    """Match argparse's last-occurrence-wins value, defaulting to fail fast.

    Reject malformed, negative, or non-finite waits before starting container
    work, including --list. Inner-runner validation alone would be too late.
    """
    value = 0.0
    for i, arg in enumerate(passthrough):
        name, eq, value_str = arg.partition("=")
        if canonicalize(name) != "--admission-wait":
            continue
        if not eq:
            value_str = passthrough[i + 1] if i + 1 < len(passthrough) else ""
        try:
            value = float(value_str)
        except ValueError:
            raise SystemExit(f"--admission-wait: invalid float value: {value_str!r}") from None
    if not math.isfinite(value) or value < 0:
        raise SystemExit(f"--admission-wait must be a non-negative, finite number, got {value:g}")
    return value

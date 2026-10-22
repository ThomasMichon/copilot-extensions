"""Pure offline summaries; missing observations never become successful samples."""

from __future__ import annotations

import math


def summarize(
    samples: list[dict], *, expected: int, budget_ns: int | None = None,
) -> dict:
    """Summarize one homogeneous stage/workload supplied by the measurement owner.

    Nearest-rank quantiles retain their sample count; fewer than 100 successful
    samples cannot qualify a p99 claim. A maximum ceiling can still be evaluated.
    This does not qualify unobserved native boundaries or validate their provenance.
    """
    if type(expected) is not int or expected < 1 or len(samples) > expected:
        raise ValueError("invalid sample count")
    if budget_ns is not None and (type(budget_ns) is not int or budget_ns <= 0):
        raise ValueError("invalid budget")
    durations = []
    copied = []
    for sample in samples:
        if type(sample) is not dict or set(sample) != {"outcome", "duration_ns", "dropped"}:
            raise ValueError("invalid sample shape")
        outcome, duration, dropped = sample["outcome"], sample["duration_ns"], sample["dropped"]
        if outcome not in {"ok", "error", "cancelled", "unavailable"}:
            raise ValueError("invalid sample outcome")
        if type(dropped) is not int or dropped < 0:
            raise ValueError("invalid drop count")
        if duration is not None and (type(duration) is not int or duration < 0):
            raise ValueError("invalid duration")
        copied.append(dict(sample))
        if outcome == "ok" and duration is not None and dropped == 0:
            durations.append(duration)
    durations.sort()
    complete = len(durations) == expected
    quantiles = {
        name: durations[max(0, math.ceil(len(durations) * fraction) - 1)] if durations else None
        for name, fraction in (("p50_ns", .5), ("p95_ns", .95), ("p99_ns", .99), ("max_ns", 1))
    }
    return {
        "schema": "agent.performance-summary.v1", "samples": copied,
        "expected": expected, "observed": len(samples), "complete": complete,
        "successful": len(durations), "quantile_method": "nearest-rank",
        "tail_qualified": complete and expected >= 100,
        **quantiles,
        "budget_ns": budget_ns,
        "budget_met": (
            max(durations) <= budget_ns if complete and budget_ns is not None else None
        ),
    }

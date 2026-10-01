"""Coverage-efficient fallback (smoke-tier) curation.

Realizes the vision's `coverage-efficient fallback curation` Concept: a
greedy, budget-bounded weighted-set-cover selection over the baseline's own
per-test coverage and wall-clock-cost data. The general weighted-set-cover
problem is NP-hard; this is the standard greedy log-approximation, chosen
because the fallback is a safety net that must stay cheap to recompute as
the baseline evolves, not because it is provably optimal.

Pure stdlib, operates only on the portable JSON baseline.

**Phase 0 scope note:** the vision requires the fallback tier to be
"always-safe" -- drawn from the portfolio's own already-vetted tiers, not
just whichever tests happen to score well on coverage-per-cost. This
prototype accepts an optional `eligible_tests` restriction for exactly that
reason; when omitted, it defaults to the full baseline (every collected
test is a candidate) as a documented Phase 0 simplification, **not** a
safety claim -- wiring real portfolio-tier eligibility (`test-portfolio`'s
own tiering) into `eligible_tests` is required before this curation is used
as an actual CI fallback tier (tracked in this effort's own Phase 3).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

_MIN_DURATION_S = 1e-6  # avoid division by zero for a measured 0.00s test


@dataclass(frozen=True)
class FallbackSet:
    selected_tests: tuple[str, ...]
    total_runtime_s: float
    covered_fraction: float
    universe_size: int

    def as_dict(self) -> dict:
        return {
            "selected_tests": list(self.selected_tests),
            "total_runtime_s": self.total_runtime_s,
            "covered_fraction": self.covered_fraction,
            "universe_size": self.universe_size,
        }


def compute_fallback_set(
    baseline: dict,
    runtime_budget_s: float,
    *,
    eligible_tests: frozenset[str] | None = None,
) -> FallbackSet:
    """Greedily build a smoke tier within `runtime_budget_s`.

    Repeatedly adds whichever remaining **affordable** candidate is cheapest
    per unit of still-uncovered baseline coverage (lines, each keyed by
    (file, line)), stopping once no remaining candidate both covers
    something new and fits the leftover budget -- a budget-first selection,
    not a coverage-saturation-first one, per the vision's own framing. A
    candidate exceeding the remaining budget is skipped in favor of a
    cheaper, still-useful one rather than ending the pass early; only when
    nothing fits at all and the fallback would otherwise be empty is the
    single cheapest viable candidate force-picked, so the safety net is
    never empty by construction.

    A test with no, non-numeric, non-finite, or negative duration data is
    treated as **ineligible** (excluded from candidates entirely), never as
    a free/near-zero-cost pick -- incomplete timing data must never make a
    test look artificially attractive to the optimizer.
    """
    test_lines: dict[str, set] = {}
    for file, lines in baseline.get("coverage", {}).items():
        for lineno, tests in lines.items():
            for test in tests:
                if eligible_tests is not None and test not in eligible_tests:
                    continue
                test_lines.setdefault(test, set()).add((file, lineno))

    universe: set = set()
    for covered in test_lines.values():
        universe |= covered

    durations = baseline.get("tests", {})
    cost_of: dict[str, float] = {}
    for test in test_lines:
        raw = durations.get(test, {}).get("duration_s")
        if (
            not isinstance(raw, (int, float))
            or isinstance(raw, bool)
            or not math.isfinite(raw)
            or raw < 0
        ):
            continue  # missing/invalid duration -> ineligible, not "free"
        cost_of[test] = max(raw, _MIN_DURATION_S)

    remaining = set(universe)
    candidates = set(cost_of)
    selected: list[str] = []
    total_cost = 0.0

    while remaining and candidates:
        # Stable, score-descending ranking every round: (score desc, cost
        # asc, test-id asc) so ties never depend on set/hash iteration
        # order -- the same baseline always curates the same fallback set.
        ranked = sorted(
            (t for t in candidates if test_lines[t] & remaining),
            key=lambda t: (
                -(len(test_lines[t] & remaining) / cost_of[t]),
                cost_of[t],
                t,
            ),
        )
        if not ranked:
            break  # no remaining candidate covers anything new

        affordable = next(
            (t for t in ranked if total_cost + cost_of[t] <= runtime_budget_s),
            None,
        )
        if affordable is None:
            if selected:
                break  # budget spent; a non-empty fallback already exists
            # Never return an empty fallback when at least one candidate
            # covers something: force the cheapest viable pick even if it
            # alone exceeds the nominal budget.
            affordable = min(ranked, key=lambda t: (cost_of[t], t))

        selected.append(affordable)
        total_cost += cost_of[affordable]
        remaining -= test_lines[affordable]
        candidates.discard(affordable)

    covered_fraction = 1.0 if not universe else 1.0 - (len(remaining) / len(universe))

    return FallbackSet(
        selected_tests=tuple(selected),
        total_runtime_s=total_cost,
        covered_fraction=covered_fraction,
        universe_size=len(universe),
    )

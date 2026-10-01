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

**`covered_fraction` is always measured against the full baseline**,
independent of `eligible_tests`: restricting candidates can leave lines
only an ineligible test covers permanently unreachable, and the metric must
show that gap rather than silently shrinking its own denominator to hide
it.

**The runtime budget is a hard cap, never silently breached.** If even the
single cheapest useful candidate would exceed `runtime_budget_s`, this
returns an empty selection rather than forcing a pick over budget -- an
empty, clearly-incomplete fallback (`selected_tests == ()` with
`covered_fraction < 1.0`) is auditable; a fallback that silently cost more
than its own caller's stated budget is not.
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

    Repeatedly adds whichever remaining **affordable** eligible candidate is
    cheapest per unit of still-uncovered baseline coverage (lines, each
    keyed by (file, line)), stopping once no remaining candidate both covers
    something new and fits the leftover budget -- never by force-picking an
    over-budget candidate (see module docstring). A candidate exceeding the
    remaining budget is skipped in favor of a cheaper, still-useful one
    rather than ending the pass early.

    A test with no, non-numeric, non-finite, or negative duration data is
    treated as **ineligible** (excluded from candidates entirely), never as
    a free/near-zero-cost pick -- incomplete timing data must never make a
    test look artificially attractive to the optimizer.
    """
    all_test_lines: dict[str, set] = {}
    for file, lines in baseline.get("coverage", {}).items():
        for lineno, tests in lines.items():
            for test in tests:
                all_test_lines.setdefault(test, set()).add((file, lineno))

    # The universe is every line the *full* baseline attributes to any
    # test, regardless of eligibility -- see module docstring on why this
    # must not shrink when `eligible_tests` restricts candidates.
    universe: set = set()
    for covered in all_test_lines.values():
        universe |= covered

    eligible_test_lines = (
        all_test_lines
        if eligible_tests is None
        else {t: lines for t, lines in all_test_lines.items() if t in eligible_tests}
    )

    durations = baseline.get("tests", {})
    actual_cost: dict[str, float] = {}
    for test in eligible_test_lines:
        raw = durations.get(test, {}).get("duration_s")
        if (
            not isinstance(raw, (int, float))
            or isinstance(raw, bool)
            or not math.isfinite(raw)
            or raw < 0
        ):
            continue  # missing/invalid duration -> ineligible, not "free"
        actual_cost[test] = float(raw)  # a genuine 0.0s test is real, not invalid

    def score_cost(test: str) -> float:
        # _MIN_DURATION_S guards only the scoring division, never the
        # budget/report accounting below -- a genuinely free (0.0s) test
        # must cost exactly 0.0 in `total_runtime_s`/affordability, not an
        # invented near-zero floor.
        return max(actual_cost[test], _MIN_DURATION_S)

    remaining = set(universe)
    candidates = set(actual_cost)
    selected: list[str] = []
    total_cost = 0.0

    while remaining and candidates:
        # Stable, score-descending ranking every round: (score desc, cost
        # asc, test-id asc) so ties never depend on set/hash iteration
        # order -- the same baseline always curates the same fallback set.
        ranked = sorted(
            (t for t in candidates if eligible_test_lines[t] & remaining),
            key=lambda t: (
                -(len(eligible_test_lines[t] & remaining) / score_cost(t)),
                actual_cost[t],
                t,
            ),
        )
        if not ranked:
            break  # no remaining candidate covers anything new

        affordable = next(
            (t for t in ranked if total_cost + actual_cost[t] <= runtime_budget_s),
            None,
        )
        if affordable is None:
            break  # the budget is a hard cap -- never force an over-budget pick

        selected.append(affordable)
        total_cost += actual_cost[affordable]
        remaining -= eligible_test_lines[affordable]
        candidates.discard(affordable)

    covered_fraction = 1.0 if not universe else 1.0 - (len(remaining) / len(universe))

    return FallbackSet(
        selected_tests=tuple(selected),
        total_runtime_s=total_cost,
        covered_fraction=covered_fraction,
        universe_size=len(universe),
    )

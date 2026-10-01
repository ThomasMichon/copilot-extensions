"""Coverage-efficient fallback (smoke-tier) curation.

Realizes the vision's `coverage-efficient fallback curation` Concept: a
greedy, budget-bounded weighted-set-cover selection over the baseline's own
per-test coverage and wall-clock-cost data. The general weighted-set-cover
problem is NP-hard; this is the standard greedy log-approximation, chosen
because the fallback is a safety net that must stay cheap to recompute as
the baseline evolves, not because it is provably optimal.

Pure stdlib, operates only on the portable JSON baseline.
"""

from __future__ import annotations

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


def compute_fallback_set(baseline: dict, runtime_budget_s: float) -> FallbackSet:
    """Greedily build a smoke tier within `runtime_budget_s`.

    Repeatedly adds whichever remaining test is cheapest per unit of
    still-uncovered baseline coverage (lines/branches, each keyed by
    (file, line)), stopping once the budget is spent -- a budget-first
    selection, not a coverage-saturation-first one, per the vision's own
    framing ("stopping once a runtime budget is spent, not once coverage
    saturates").
    """
    test_lines: dict = {}
    for file, lines in baseline.get("coverage", {}).items():
        for lineno, tests in lines.items():
            for test in tests:
                test_lines.setdefault(test, set()).add((file, lineno))

    universe: set = set()
    for covered in test_lines.values():
        universe |= covered

    durations = baseline.get("tests", {})
    cost_of = {
        test: max(durations.get(test, {}).get("duration_s", 0.0), _MIN_DURATION_S)
        for test in test_lines
    }

    remaining = set(universe)
    candidates = set(test_lines)
    selected: list[str] = []
    total_cost = 0.0

    while remaining and candidates:
        best_test = None
        best_score = -1.0
        best_cost = 0.0
        for test in candidates:
            newly_covered = len(test_lines[test] & remaining)
            if newly_covered == 0:
                continue
            cost = cost_of[test]
            score = newly_covered / cost
            if score > best_score:
                best_test, best_score, best_cost = test, score, cost
        if best_test is None:
            break  # no remaining candidate covers anything new
        if selected and total_cost + best_cost > runtime_budget_s:
            break  # budget spent; stop (always keep at least one pick)
        selected.append(best_test)
        total_cost += best_cost
        remaining -= test_lines[best_test]
        candidates.discard(best_test)

    covered_fraction = 1.0 if not universe else 1.0 - (len(remaining) / len(universe))

    return FallbackSet(
        selected_tests=tuple(selected),
        total_runtime_s=total_cost,
        covered_fraction=covered_fraction,
        universe_size=len(universe),
    )

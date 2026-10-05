"""Coverage-efficient fallback (smoke-tier) curation.

Realizes the vision's `coverage-efficient fallback curation` Concept: a
greedy, budget-bounded weighted-set-cover selection over the baseline's own
per-test coverage and wall-clock-cost data. The general weighted-set-cover
problem is NP-hard; this is the standard greedy log-approximation, chosen
because the fallback is a safety net that must stay cheap to recompute as
the baseline evolves, not because it is provably optimal.

Pure stdlib, operates only on the portable JSON baseline.

**Fallback eligibility is real, portfolio-tier-restricted, not every
collected test.** `compute_fallback_set` itself stays a general,
reusable primitive: its own `eligible_tests` parameter accepts any
caller-supplied restriction, or `None` for "every collected test is a
candidate" -- a deliberately unsafe default no actual CI fallback should
ever rely on directly. `decide.py`'s own `decide()` is what wires the
*real* safety restriction: unless a caller explicitly overrides it,
`decide()` always derives `eligible_tests` from
`default_tier_eligible_tests` below, which restricts candidates to the
test-portfolio's own default, always-on tiers (T0-T2 and untiered) --
never T3 (clean-room) or T4 (end-to-end), which `pytest_portfolio_guard.py`
itself skips unless a run explicitly opts in with `--allow-explicit-tiers`.
A fallback tier that silently drew from those gated tiers would grant
every PR's smoke fallback an opt-in no one actually asked for.

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

#: Portfolio tiers `pytest_portfolio_guard.py` itself skips by default
#: (requires `--allow-explicit-tiers` to opt in) -- never part of the
#: portfolio's own always-on default run, so never eligible for an
#: always-on CI fallback tier either. See `default_tier_eligible_tests`.
_INELIGIBLE_TIERS = frozenset({"T3", "T4"})


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


def default_tier_eligible_tests(baseline: dict) -> frozenset[str]:
    """The real, test-portfolio-tier-restricted candidate universe for an
    always-on CI fallback: every test recorded in `baseline["tests"]`
    whose own `portfolio_tier` (baseline schema v3+, see `baseline.py`) is
    NOT one of `_INELIGIBLE_TIERS` (T3/T4) -- the tiers
    `pytest_portfolio_guard.py` itself skips unless a run explicitly opts
    in with `--allow-explicit-tiers`. A test with no `portfolio_tier` key
    at all (either genuinely untiered, or recorded in a baseline collected
    before schema v3 added the field) is treated as eligible, matching
    `pytest_portfolio_guard.py`'s own behavior for a test with no declared
    tier -- it runs unrestricted in every default invocation, so it must
    not be penalized here as though it were excluded.

    This is the actual safety wiring the vision requires of the fallback
    tier ("drawn from the portfolio's own already-vetted tiers"); `decide()`
    uses this as its own default `eligible_tests` whenever a caller doesn't
    explicitly override it.
    """
    return frozenset(
        nodeid
        for nodeid, info in baseline.get("tests", {}).items()
        if info.get("portfolio_tier") not in _INELIGIBLE_TIERS
    )

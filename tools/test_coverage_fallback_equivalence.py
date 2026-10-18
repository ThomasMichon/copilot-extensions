"""Prove optimized curation preserves the original greedy policy."""
from __future__ import annotations

import math
import random

import pytest

from tools.coverage_guided_selection.fallback import compute_fallback_set
from tools.coverage_guided_selection import fallback


@pytest.mark.parametrize("budget", [-1, float("nan"), -float("inf")])
def test_unusable_budget_skips_heap_but_keeps_universe_metrics(budget, monkeypatch):
    monkeypatch.setattr(fallback.heapq, "heapify", lambda *_a: pytest.fail("built an unusable heap"))
    baseline = {
        "tests": {"test_free": {"duration_s": 0}},
        "coverage": {"module.py": {"1": ["test_free"]}},
    }
    result = compute_fallback_set(baseline, budget)
    assert result.selected_tests == ()
    assert result.total_runtime_s == 0
    assert result.universe_size == 1
    assert result.covered_fraction == 0


def test_zero_budget_still_selects_free_tests_and_infinite_budget_is_unbounded():
    baseline = {
        "tests": {"test_free": {"duration_s": 0}, "test_paid": {"duration_s": 1}},
        "coverage": {"module.py": {"1": ["test_free"], "2": ["test_paid"]}},
    }
    zero = compute_fallback_set(baseline, 0)
    assert zero.selected_tests == ("test_free",)
    assert zero.total_runtime_s == 0
    assert zero.covered_fraction == 0.5
    infinite = compute_fallback_set(baseline, float("inf"))
    assert set(infinite.selected_tests) == {"test_free", "test_paid"}
    assert infinite.total_runtime_s == 1
    assert infinite.covered_fraction == 1


def _reference(baseline: dict, budget: float, eligible: frozenset[str] | None) -> dict:
    covered: dict[str, set] = {}
    for file, lines in baseline["coverage"].items():
        for line, tests in lines.items():
            for test in tests:
                covered.setdefault(test, set()).add((file, line))
    universe = set().union(*covered.values()) if covered else set()
    costs = {}
    for test in covered:
        raw = baseline["tests"].get(test, {}).get("duration_s")
        if (
            (eligible is None or test in eligible)
            and isinstance(raw, (float, int)) and not isinstance(raw, bool)
            and math.isfinite(raw) and raw >= 0
        ):
            costs[test] = float(raw)
    remaining, candidates, chosen, total = universe.copy(), set(costs), [], 0.0
    while remaining and candidates:
        ranked = sorted(
            (test for test in candidates if covered[test] & remaining),
            key=lambda test: (
                -len(covered[test] & remaining) / max(costs[test], 1e-6),
                costs[test], test,
            ),
        )
        affordable = next((test for test in ranked if total + costs[test] <= budget), None)
        if affordable is None:
            break
        chosen.append(affordable)
        total += costs[affordable]
        remaining -= covered[affordable]
        candidates.remove(affordable)
    return {
        "selected_tests": chosen, "total_runtime_s": total,
        "covered_fraction": 1.0 if not universe else 1.0 - len(remaining) / len(universe),
        "universe_size": len(universe),
    }


@pytest.mark.parametrize("seed", range(100))
def test_lazy_curation_matches_original_greedy(seed: int) -> None:
    rng = random.Random(seed)
    names = [f"tests/test_module.py::test_{index}" for index in range(30)]
    baseline = {
        "tests": {
            name: {"duration_s": rng.choice([0, 0.001, 0.02, 0.1, 1, None, True, -1, float("nan")])}
            for name in names
        },
        "coverage": {
            f"module{file}.py": {
                str(line): rng.sample(names, rng.randrange(15))
                for line in range(20)
            }
            for file in range(4)
        },
    }
    eligible = None if seed % 2 else frozenset(rng.sample(names, 15))
    for budget in (-1, 0, 0.001, 0.05, 0.3, 1, 10, float("nan"), float("inf")):
        assert compute_fallback_set(
            baseline, budget, eligible_tests=eligible,
        ).as_dict() == _reference(baseline, budget, eligible)

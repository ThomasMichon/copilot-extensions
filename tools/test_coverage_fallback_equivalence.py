"""Prove optimized curation preserves the original greedy policy."""
from __future__ import annotations

import math
import random

import pytest

from tools.coverage_guided_selection.fallback import compute_fallback_set


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

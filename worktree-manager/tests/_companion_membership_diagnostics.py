"""Opt-in paired elapsed clocks around unchanged private membership budgets."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import cProfile
from dataclasses import dataclass
import io
import json
import pstats
import time
from types import ModuleType
from typing import Any, TypeVar
from unittest.mock import patch

T = TypeVar("T")


@dataclass
class BudgetSample:
    budget: Any
    original_check: Callable[[], None]
    created_before: float
    created_after: float
    monotonic_started: float
    elapsed_min_ms: float = 0
    elapsed_max_ms: float = 0
    monotonic_after_ms: float = 0
    checks: int = 0

    def check(self) -> None:
        before = time.perf_counter()
        try:
            self.original_check()
        finally:
            after = time.perf_counter()
            self.elapsed_min_ms = (before - self.created_after) * 1000
            self.elapsed_max_ms = (after - self.created_before) * 1000
            self.monotonic_after_ms = (time.monotonic() - self.monotonic_started) * 1000
            self.checks += 1

    def evidence(self) -> dict[str, Any]:
        return {
            "steps": self.budget.steps,
            "max_steps": self.budget.max_steps,
            "limit_ms": self.budget.seconds * 1000,
            "exhausted": self.budget.exhausted,
            "checks": self.checks,
            "elapsed_min_ms": self.elapsed_min_ms,
            "elapsed_max_ms": self.elapsed_max_ms,
            "monotonic_after_ms": self.monotonic_after_ms,
        }


@contextmanager
def paired_budgets(
    module: ModuleType | None,
    samples: list[BudgetSample],
) -> Iterator[None]:
    if module is None:
        yield
        return
    budget_type = module.ReadBudget

    def factory() -> Any:
        before = time.perf_counter()
        monotonic = time.monotonic()
        budget = budget_type()
        after = time.perf_counter()
        if len(samples) < 8:
            sample = BudgetSample(budget, budget.check, before, after, monotonic)
            samples.append(sample)
            budget.check = sample.check
        return budget

    with patch.object(module, "ReadBudget", factory):
        yield


def membership_seed(
    manager: Any,
    owner: str,
    source_for: Callable[[Any, str], T],
    report: Callable[[str], None] | None = None,
    *,
    budget_module: ModuleType | None = None,
) -> T:
    """Profile one original call; instrumentation time still counts toward its deadline."""
    if report is None:
        return source_for(manager, owner)
    profile, output, samples = cProfile.Profile(), io.StringIO(), []
    wall, cpu = time.perf_counter(), time.thread_time()
    outcome = "error"
    try:
        with paired_budgets(budget_module, samples):
            result = profile.runcall(source_for, manager, owner)
        outcome = "seeded"
        return result
    finally:
        wall_ms = (time.perf_counter() - wall) * 1000
        cpu_ms = (time.thread_time() - cpu) * 1000
        stats = pstats.Stats(profile, stream=output).strip_dirs().sort_stats("cumulative")
        stats.print_stats(25)
        clocks = {name: vars(time.get_clock_info(name)) for name in ("monotonic", "perf_counter")}
        report(
            f"membership-seed-profile outcome={outcome} "
            f"wall_ms={wall_ms:.3f} thread_ms={cpu_ms:.3f}\n"
            f"paired-clocks={json.dumps(clocks, sort_keys=True)}\n"
            f"private-budgets={json.dumps([sample.evidence() for sample in samples])}\n"
            f"{output.getvalue()}"
        )

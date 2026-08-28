"""Deterministic dependency planning without installer execution."""

from __future__ import annotations

import heapq
from collections import defaultdict
from collections.abc import Mapping, Sequence

from .model import (
    ConfigurationEmpty,
    DiscoveryReport,
    Module,
    Plan,
    PlanState,
    PlanStep,
    Platform,
    ReadinessResult,
    ReadinessState,
)


def _topological(modules: Sequence[Module]) -> tuple[Module, ...]:
    by_id = {module.qualified_id: module for module in modules}
    indegree = {module_id: 0 for module_id in by_id}
    dependents: dict[str, list[str]] = defaultdict(list)
    for module in modules:
        for dependency in module.dependencies:
            qualified = f"{module.owner.provenance.marketplace_id}::{dependency}"
            indegree[module.qualified_id] += 1
            dependents[qualified].append(module.qualified_id)
    ready = list(module_id for module_id, degree in indegree.items() if degree == 0)
    heapq.heapify(ready)
    ordered: list[Module] = []
    while ready:
        module_id = heapq.heappop(ready)
        ordered.append(by_id[module_id])
        for dependent in sorted(dependents[module_id]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                heapq.heappush(ready, dependent)
    if len(ordered) != len(modules):
        raise ValueError("cannot plan a dependency cycle")
    return tuple(ordered)


def build_plan(
    report: DiscoveryReport,
    platform: Platform | str,
    readiness: Mapping[str, ReadinessResult] | None = None,
) -> Plan:
    """Classify a validated graph for later consumer execution.

    No command is executed. A failed, unsupported, or unsatisfied-empty
    prerequisite blocks only its transitive dependents; independent modules
    remain planned.
    """
    if not report.valid:
        raise ValueError("cannot plan installer/readiness modules with validation findings")
    selected_platform = Platform(platform)
    outcomes = dict(readiness or {})
    modules = _topological(report.modules)
    known = {module.qualified_id for module in modules}
    unknown = sorted(set(outcomes) - known)
    if unknown:
        raise ValueError(f"readiness contains unknown module ids: {', '.join(unknown)}")
    by_module_id = {module.qualified_id: module.module_id for module in modules}
    for qualified_id, result in outcomes.items():
        if not isinstance(result, ReadinessResult):
            raise ValueError(f"readiness for {qualified_id} is not a ReadinessResult")
        if result.module_id != by_module_id[qualified_id]:
            raise ValueError(
                f"readiness module id mismatch for {qualified_id}: {result.module_id}"
            )
        if not isinstance(result.state, ReadinessState):
            raise ValueError(f"readiness state for {qualified_id} is invalid")
    steps: list[PlanStep] = []
    by_state: dict[str, PlanStep] = {}
    for module in modules:
        dependency_ids = tuple(
            f"{module.owner.provenance.marketplace_id}::{dependency}"
            for dependency in module.dependencies
        )
        blockers: list[str] = []
        for dependency_id in dependency_ids:
            dependency_step = by_state[dependency_id]
            dependency = dependency_step.module
            if dependency_step.state in (
                PlanState.FAILED,
                PlanState.BLOCKED,
                PlanState.UNSUPPORTED,
            ):
                blockers.append(dependency_id)
            elif (
                dependency_step.state is PlanState.CONFIGURATION_EMPTY
                and dependency.configuration_empty is ConfigurationEmpty.UNSATISFIED
            ):
                blockers.append(dependency_id)
        if blockers:
            state = PlanState.BLOCKED
        elif selected_platform not in module.platforms:
            state = PlanState.UNSUPPORTED
        else:
            result = outcomes.get(module.qualified_id)
            if result is None or result.state is ReadinessState.NOT_READY:
                state = PlanState.PLANNED
            elif result.state is ReadinessState.READY:
                state = PlanState.READY
            elif result.state is ReadinessState.CONFIGURATION_EMPTY:
                state = PlanState.CONFIGURATION_EMPTY
            else:
                state = PlanState.FAILED
        step = PlanStep(module=module, state=state, blocked_by=tuple(sorted(blockers)))
        steps.append(step)
        by_state[module.qualified_id] = step
    return Plan(platform=selected_platform, steps=tuple(steps))

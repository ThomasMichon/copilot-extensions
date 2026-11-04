"""The existing sync tick admits, rotates and reports typed stores independently."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from agent_procutil.owned_budget import OwnedExecutionBudget
from agent_procutil.owned_process import OwnedCleanupError

from agent_logger.aggregate import (
    Admission,
    AggregateInputs,
    Claim,
    DiscoveredDeclaration,
    ExecutionMode,
    Provenance,
    RepositoryDeclaration,
    RepositoryPolicy,
    ResolvedPlan,
    ResourceBinding,
    SourceSet,
)
from agent_logger.aggregate_machine import resolve_machine_identity
from agent_logger.config import Config
from agent_logger.sync import worktree_state_tick as tick
from agent_logger.sync.targets.base import Target
from agent_logger.sync.worktree_state import WorktreeStateError
from agent_logger.sync.worktree_state_providers import ProjectInventory

from .test_worktree_state import Packet
from .test_worktree_state import packet as packet
from .test_worktree_state_pipeline import selection


def _inputs(packet: Packet, count: int = 1) -> AggregateInputs:
    cfg, target, _, _ = packet
    admissions = []
    discoveries = []
    for index in range(count):
        repository = f"github.com/example/repo-{index}"
        admission = Admission(
            repository,
            cfg.home.parent / f"checkout-{index}",
            collection_targets={"archive": ResourceBinding("filesystem", str(target._root()))},
        )
        admissions.append(admission)
        discoveries.append(
            DiscoveredDeclaration(
                admission.authoritative_checkout,
                repository,
                RepositoryDeclaration(
                    1,
                    repository,
                    Provenance("config.yaml", repository),
                    RepositoryPolicy(
                        "default",
                        claims=(
                            Claim(
                                f"repo-{index}",
                                SourceSet(repositories=frozenset({repository})),
                                collection_target="archive",
                            ),
                        ),
                    ),
                ),
            ),
        )
    return AggregateInputs(
        resolve_machine_identity(cfg),
        ExecutionMode.ENFORCE,
        tuple(admissions),
        tuple(discoveries),
    )


def _provider(
    monkeypatch: pytest.MonkeyPatch, inputs: AggregateInputs
) -> list[OwnedExecutionBudget]:
    loads = []

    class Provider:
        def __init__(self, *, machine: Any, home: Path, budget: OwnedExecutionBudget) -> None:
            assert machine == inputs.machine
            self.budget = budget

        def load(self) -> AggregateInputs:
            loads.append(self.budget)
            return inputs

    monkeypatch.setattr(tick, "FileSystemAggregateInputProvider", Provider)
    return loads


def _inventory(admission: Admission, home: Path, *, stores: int = 1) -> ProjectInventory:
    _, original, placement = selection(home)
    return replace(
        original,
        repository=admission.repository,
        placements=tuple(
            replace(placement, store_id=f"{index:032x}", state_home=home.parent / f"store-{index}")
            for index in range(stores)
        ),
    )


def test_tick_policy_precedes_inventory_and_cursor_writes(
    packet: Packet,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg, target, _, _ = packet
    inputs = replace(_inputs(packet), mode=ExecutionMode.OBSERVE)
    loads = _provider(monkeypatch, inputs)
    monkeypatch.setattr(
        tick,
        "read_project_inventory",
        lambda *a, **k: pytest.fail("unadmitted inventory"),
    )
    result = tick.collect_pass(cfg, target)
    assert len(loads) == 1
    assert result.failed and not result.completed
    assert not cfg.home.exists() and not target._root().exists()


def test_tick_keeps_one_input_snapshot_typed_checkout_and_shared_budget(
    packet: Packet,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg, target, _, _ = packet
    inputs = _inputs(packet)
    loads = _provider(monkeypatch, inputs)
    inventory = _inventory(inputs.admissions[0], cfg.home)
    budgets = []

    def discover(
        admission: Admission,
        home: Path,
        *,
        budget: OwnedExecutionBudget,
    ) -> ProjectInventory:
        assert admission is inputs.admissions[0] and home == cfg.home
        budgets.append(budget)
        return inventory

    def collect(
        actual_cfg: Config,
        actual_target: Target,
        plan: ResolvedPlan,
        admission: Admission,
        observed: ProjectInventory,
        placement: Any,
        budget: OwnedExecutionBudget,
    ) -> None:
        assert actual_cfg is cfg and actual_target is target
        assert plan.machine == inputs.machine and admission is inputs.admissions[0]
        assert observed is inventory and placement is inventory.placements[0]
        budgets.append(budget)

    monkeypatch.setattr(tick, "read_project_inventory", discover)
    monkeypatch.setattr(tick, "collect_managed_worktree_state", collect)
    result = tick.collect_pass(cfg, target)
    assert len(result.completed) == 1 and not result.failed and not result.deferred
    assert len(loads) == 1 and all(budget is loads[0] for budget in budgets)


def test_tick_failed_repository_does_not_drop_healthy_independent_store(
    packet: Packet,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg, target, _, _ = packet
    inputs = _inputs(packet, 2)
    _provider(monkeypatch, inputs)

    def discover(
        admission: Admission,
        home: Path,
        *,
        budget: OwnedExecutionBudget,
    ) -> ProjectInventory:
        if admission is inputs.admissions[0]:
            raise WorktreeStateError("owner read refused")
        return _inventory(admission, home)

    monkeypatch.setattr(tick, "read_project_inventory", discover)
    monkeypatch.setattr(tick, "collect_managed_worktree_state", lambda *a: None)
    result = tick.collect_pass(cfg, target)
    assert len(result.failed) == len(result.completed) == 1
    assert not result.deferred


def test_tick_rotation_survives_failed_exports_and_new_pass_instances(
    packet: Packet,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg, target, _, _ = packet
    inputs = _inputs(packet, 2)
    _provider(monkeypatch, inputs)
    monkeypatch.setattr(tick, "MAX_REPOSITORIES_PER_PASS", 1)
    monkeypatch.setattr(
        tick,
        "read_project_inventory",
        lambda admission, home, **k: _inventory(admission, home, stores=2),
    )
    attempts = []

    def failed(
        cfg: Config,
        target: Target,
        plan: ResolvedPlan,
        admission: Admission,
        inventory: ProjectInventory,
        placement: Any,
        budget: OwnedExecutionBudget,
    ) -> None:
        attempts.append((admission.repository, placement.store_id))
        raise WorktreeStateError("export refused; staging retained")

    monkeypatch.setattr(tick, "collect_managed_worktree_state", failed)
    results = [tick.collect_pass(cfg, target) for _ in range(4)]
    assert attempts == [
        (inputs.admissions[0].repository, f"{0:032x}"),
        (inputs.admissions[1].repository, f"{0:032x}"),
        (inputs.admissions[0].repository, f"{1:032x}"),
        (inputs.admissions[1].repository, f"{1:032x}"),
    ]
    assert all(len(result.failed) == 1 and len(result.deferred) == 2 for result in results)


def test_tick_budget_exhaustion_defers_without_renewal(
    packet: Packet,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg, target, _, _ = packet
    inputs = _inputs(packet, 2)
    loads = _provider(monkeypatch, inputs)
    clock = [0.0]
    monkeypatch.setattr(tick.time, "monotonic", lambda: clock[0])

    def discover(
        admission: Admission,
        home: Path,
        *,
        budget: OwnedExecutionBudget,
    ) -> ProjectInventory:
        return _inventory(admission, home, stores=2)

    def collect(*args: Any) -> None:
        clock[0] = loads[0].deadline

    monkeypatch.setattr(tick, "read_project_inventory", discover)
    monkeypatch.setattr(tick, "collect_managed_worktree_state", collect)
    result = tick.collect_pass(cfg, target)
    assert len(loads) == 1 and len(result.completed) == 1
    assert len(result.deferred) == 2 and not result.failed


def test_tick_unknown_cleanup_stays_exceptional_and_retains_cursor(
    packet: Packet,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg, target, _, _ = packet
    inputs = _inputs(packet)
    _provider(monkeypatch, inputs)
    monkeypatch.setattr(
        tick,
        "read_project_inventory",
        lambda admission, home, **k: _inventory(admission, home),
    )
    failure = OwnedCleanupError("native descendant settlement unconfirmed")

    def uncertain(*args: Any) -> None:
        raise failure

    monkeypatch.setattr(tick, "collect_managed_worktree_state", uncertain)
    with pytest.raises(OwnedCleanupError) as raised:
        tick.collect_pass(cfg, target)
    assert raised.value is failure
    assert (cfg.home / tick.CURSOR_NAME).is_file()


def test_tick_malformed_cursor_refuses_without_overwrite_or_inventory(
    packet: Packet,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg, target, _, _ = packet
    _provider(monkeypatch, _inputs(packet))
    cfg.home.mkdir()
    cursor = cfg.home / tick.CURSOR_NAME
    cursor.write_bytes(b'{"version":1,"after": "not-a-complete-cursor"}')
    monkeypatch.setattr(
        tick,
        "read_project_inventory",
        lambda *a, **k: pytest.fail("corrupt cursor reused"),
    )
    result = tick.collect_pass(cfg, target)
    assert result.failed and not result.completed
    assert cursor.read_bytes() == b'{"version":1,"after": "not-a-complete-cursor"}'


def test_tick_cursor_publication_failure_retains_private_evidence_before_owner_read(
    packet: Packet,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_logger.sync.publication_directory import PublicationDirectory

    cfg, target, _, _ = packet
    _provider(monkeypatch, _inputs(packet))

    def failed(*args: Any) -> None:
        raise OSError("cursor barrier failed")

    monkeypatch.setattr(PublicationDirectory, "replace", failed)
    monkeypatch.setattr(
        tick,
        "read_project_inventory",
        lambda *a, **k: pytest.fail("unsettled cursor reused"),
    )
    result = tick.collect_pass(cfg, target)
    assert len(result.failed) == 1 and not result.completed
    (temporary,) = cfg.home.glob(f".{tick.CURSOR_NAME}.*.tmp")
    assert temporary.stat().st_mode & 0o777 == 0o600
    assert not (cfg.home / tick.CURSOR_NAME).exists()

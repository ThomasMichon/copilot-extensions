"""Paired clock evidence preserves the original deadline and seed outcome."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

import _companion_membership_diagnostics as diagnostic
from test_agenda_end_to_end import ROOT
from test_companion_bridge_session_source import semantic_peer as semantic_peer
from native.test_companion_joined_fixture import (
    test_private_bridge_membership_cli_completes_before_client_composition as _qualify_private_cli,
)


@pytest.fixture
def reader(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    monkeypatch.syspath_prepend(str(ROOT / "plugins/agent-bridge/src"))
    return importlib.import_module("agent_bridge.owner_membership_read")


@pytest.mark.parametrize(
    "elapsed,monotonic,failed",
    [
        (0.047, 0.062, True),
        (0.075, 0.062, True),
        (0.012, 0.015, False),
    ],
)
def test_paired_budget_separates_early_quantized_refusal_from_true_elapsed_work(
    reader: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    elapsed: float,
    monotonic: float,
    failed: bool,
) -> None:
    clock, samples = [100.0, 10.0], []
    original = reader.ReadBudget
    monkeypatch.setattr(reader, "time", SimpleNamespace(monotonic=lambda: clock[1]))
    monkeypatch.setattr(
        diagnostic,
        "time",
        SimpleNamespace(
            perf_counter=lambda: clock[0],
            monotonic=lambda: clock[1],
        ),
    )
    with diagnostic.paired_budgets(reader, samples):
        budget = reader.ReadBudget()
        budget.started = 10.0
        clock[:] = [100.0 + elapsed, 10.0 + monotonic]
        if failed:
            with pytest.raises(reader.ObservationError) as caught:
                budget.check()
            assert caught.value.code == "capture-budget-exhausted"
        else:
            budget.check()
    assert reader.ReadBudget is original
    evidence = samples[0].evidence()
    assert evidence["checks"] == 1 and evidence["exhausted"] is failed
    assert evidence["elapsed_max_ms"] == pytest.approx(elapsed * 1000)
    assert evidence["monotonic_after_ms"] == pytest.approx(monotonic * 1000)
    assert (failed and evidence["elapsed_max_ms"] < evidence["limit_ms"]) is (
        failed and elapsed < 0.05
    )


@pytest.mark.parametrize("profile,failed", [(False, False), (True, False), (True, True)])
def test_actual_budget_diagnostic_preserves_single_seed_and_restores_factory(
    reader: ModuleType,
    profile: bool,
    failed: bool,
) -> None:
    calls, reports, budgets = [], [], []
    original, result = reader.ReadBudget, object()

    def source_for(manager: object, owner: str) -> object:
        calls.append((manager, owner))
        budget = reader.ReadBudget()
        budgets.append(budget)
        if failed:
            budget.steps = budget.max_steps + 1
        budget.check()
        return result

    manager = object()
    if failed:
        with pytest.raises(reader.ObservationError) as caught:
            diagnostic.membership_seed(
                manager,
                "example",
                source_for,
                reports.append if profile else None,
                budget_module=reader,
            )
        assert caught.value.code == "capture-budget-exhausted"
    else:
        assert (
            diagnostic.membership_seed(
                manager,
                "example",
                source_for,
                reports.append if profile else None,
                budget_module=reader,
            )
            is result
        )
    assert calls == [(manager, "example")] and reader.ReadBudget is original
    assert budgets[0].seconds == 0.05 and budgets[0].max_steps == 100_000
    if profile:
        body = reports[0]
        clocks = json.loads(body.split("paired-clocks=", 1)[1].splitlines()[0])
        evidence = json.loads(body.split("private-budgets=", 1)[1].splitlines()[0])
        assert clocks["perf_counter"]["monotonic"]
        assert len(evidence) == 1 and evidence[0]["exhausted"] is failed
        assert evidence[0]["checks"] == 1
    else:
        assert not reports and "check" not in vars(budgets[0])


def test_paired_budget_retains_bounded_evidence_without_skipping_original_checks(
    reader: ModuleType,
) -> None:
    samples = []
    with diagnostic.paired_budgets(reader, samples):
        budgets = [reader.ReadBudget() for _ in range(12)]
        for budget in budgets:
            budget.steps = budget.max_steps + 1
            with pytest.raises(reader.ObservationError):
                budget.check()
    assert len(samples) == 8 and all(budget.exhausted for budget in budgets)


def test_profiled_actual_membership_seed_retains_private_cli_observation(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("COMPANION_MEMBERSHIP_PROFILE", "1")
    peer = request.getfixturevalue("semantic_peer")
    _qualify_private_cli(peer, tmp_path)
    assert not peer["failures"]

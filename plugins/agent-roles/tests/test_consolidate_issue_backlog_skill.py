"""Static consolidation-contract regressions, not provider or model qualification."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1] / "skills" / "consolidate-issue-backlog"
SCENARIOS = json.loads((SKILL / "assets" / "scenarios.json").read_text(encoding="utf-8"))
CASES = {case["id"]: case for case in SCENARIOS["cases"]}


@pytest.mark.parametrize("case_id", [
    "incomplete-receiver",
    "unknown-write-outcome",
    "inflow-and-excluded-denominator",
    "target-met-closeout-pending",
])
def test_failure_scenarios_retain_their_controlling_contract(case_id: str) -> None:
    scenario = CASES[case_id]
    assert SCENARIOS["example_only"] is True
    assert scenario["given"] and scenario["expected"]
    assert scenario["required_guidance"]
    for relative, clauses in scenario["required_guidance"].items():
        text = " ".join((SKILL / relative).read_text(encoding="utf-8").split())
        assert clauses
        for clause in clauses:
            assert " ".join(clause.split()) in text, (scenario["id"], relative, clause)

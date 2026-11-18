"""Static delivery contracts; these checks do not claim model compliance."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


PLUGIN = Path(__file__).resolve().parents[1]
TEMPLATE = PLUGIN / "instructions/charter-boundary.instructions.md"


def test_charter_boundary_is_delivered_as_bounded_static_guidance() -> None:
    declarations = json.loads((PLUGIN / "instruction-projections.json").read_text())
    matches = [p for p in declarations["projections"] if p["id"] == "charter-boundary"]
    assert len(matches) == 1
    assert matches[0]["template"] == "instructions/charter-boundary.instructions.md"
    assert matches[0]["applyTo"] == "**"
    assert len(TEMPLATE.read_bytes()) <= 4096
    assert len((PLUGIN / "instructions/useful-autonomy.instructions.md").read_bytes()) <= 4096


@pytest.mark.parametrize("clause", [
    "freeze supporting tooling/harness source",
    "A product can span several repositories, including its implementation libraries",
    "fork/PR delivery in those repositories are in scope without another generic prompt",
    "Report the error and offer a bug report",
    "do not automatically create an issue or PR",
    "or wait for upstream review, merge, release or rollout",
    "supported recovery or installer-equivalent local repair",
    "Repair, reporting and source publication are separate authorities",
    "its normal tests, additional relevant tests, understood rebases, required fork/PR publication",
    "Do not ask generic permission for each such step",
    "an enabled maintainer plugin",
    "does not expand the charter",
    "Do not weaken tests, bypass CI",
    "explicitly assign harness maintenance",
    "harness-focused test failures remains bounded to that harness work",
])
def test_product_failures_and_explicit_contributions_have_distinct_authority(clause: str) -> None:
    assert clause in " ".join(TEMPLATE.read_text().split())

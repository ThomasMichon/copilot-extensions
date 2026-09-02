from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCENARIO = (
    ROOT
    / "tools"
    / "clean-room"
    / "scenarios"
    / "progressive-context-disclosure-baseline"
)


def _fixture_module():
    path = SCENARIO / "fixture.py"
    spec = importlib.util.spec_from_file_location(
        "progressive_context_disclosure_fixture", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_frozen_progressive_context_fixture_matches_suite() -> None:
    result = _fixture_module().verify(ROOT)

    assert result["ok"] is True
    assert result["suiteContributorCount"] == 21
    assert result["syntheticContributorCount"] == 8
    assert result["taskCount"] == 11


def test_full_inline_baseline_is_larger_than_concise_kernel() -> None:
    baselines = _fixture_module().baseline_record()["baselines"]

    assert (
        baselines["current-full-inline"]["utf8Bytes"]
        > baselines["current-concise-kernel"]["utf8Bytes"]
    )
    assert (
        baselines["current-full-inline"]["estimatedTokens"]
        > baselines["current-concise-kernel"]["estimatedTokens"]
    )


def test_negative_reference_stimuli_and_windows_escape_are_explicit() -> None:
    fixture = _fixture_module()
    corpus = fixture._load("corpus.json")
    tasks = fixture._load("tasks.json")["tasks"]
    contributors = corpus["contributors"]

    negative = [task for task in tasks if task.get("referenceFixture")]
    assert {task["id"] for task in negative} == {
        "unavailable-guide",
        "unsafe-guide",
    }
    for task in negative:
        locator = task["referenceFixture"]["locator"]
        rendered = fixture.render_task_cell(
            "current-concise-kernel", task, contributors
        )
        assert rendered.count(locator) == 1
        for representation in fixture._load("experiment.json")["axes"][
            "referenceRepresentation"
        ]:
            represented = fixture.render_task_cell(
                "current-concise-kernel",
                task,
                contributors,
                reference_representation=representation,
            )
            assert task["referenceFixture"]["guideId"] in represented

    assert fixture._safe_guide_path(r"guides\sub\..\..\secret.md") is False

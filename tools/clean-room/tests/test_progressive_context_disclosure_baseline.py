from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest


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
            for emphasis in fixture._load("experiment.json")["axes"][
                "emphasis"
            ]:
                phase2 = fixture.render_variant(
                    deferral_level="F3",
                    reference_representation=representation,
                    emphasis=emphasis,
                    assembly="flat-with-index",
                    task=task,
                    contributors=contributors,
                )
                assert phase2.count(locator) == 1
                assert (
                    f"{task['referenceFixture']['applicability']} "
                    in phase2
                )

    unsafe = next(task for task in negative if task["id"] == "unsafe-guide")
    absolute = fixture.render_variant(
        deferral_level="F3",
        reference_representation="backtick-absolute-contained",
        emphasis="safety-gated",
        assembly="flat-fragments",
        task=unsafe,
        contributors=contributors,
    )
    assert "/repository/../outside/unsafe-guide.md" in absolute

    assert fixture._safe_guide_path(r"guides\sub\..\..\secret.md") is False
    assert fixture._safe_repository_path("C:escape.json") is False


def test_phase2_renderer_covers_frozen_axes_and_preserves_baselines() -> None:
    fixture = _fixture_module()
    result = fixture.phase2_matrix_record()

    assert result == {
        "ok": True,
        "renderCount": 3080,
        "distinctRenderHashes": 1160,
        "phase2Assemblies": ["flat-fragments", "flat-with-index"],
        "matrixSha256": (
            "f39f0ac677fddd4876ee2bf79d1fed67"
            "a50e38a191a8eec2c5c31ea733fd1066"
        ),
    }
    assert fixture.verify_phase2() == result


def test_f3_renders_only_task_applicable_references() -> None:
    fixture = _fixture_module()
    corpus = fixture._load("corpus.json")
    task = fixture._task_by_id("one-guide")
    rendered = fixture.render_variant(
        deferral_level="F3",
        reference_representation="backtick-repository-relative",
        emphasis="conditional",
        assembly="flat-fragments",
        task=task,
        contributors=corpus["contributors"],
    )

    assert "guides/runtime-diagnostics.md" in rendered
    assert "guides/publication-checks.md" not in rendered
    assert "guides/command-reference.md" not in rendered
    assert "READY-1" not in rendered
    assert "Absence of an affirmative READY signal" in rendered


def test_materialized_runs_use_fresh_canaries_outside_fixture(
    tmp_path: Path,
) -> None:
    fixture = _fixture_module()
    first = tmp_path / "run-a"
    second = tmp_path / "run-b"
    coordinates = {
        "source": ROOT,
        "deferral_level": "F2",
        "reference_representation": "backtick-payload-relative",
        "emphasis": "imperative",
        "assembly": "flat-with-index",
        "task_id": "one-guide",
        "model": "calibration-model",
        "repetition": 1,
    }

    first_metadata = fixture.materialize(root=first, **coordinates)
    fixture.materialize(root=second, **coordinates)
    first_canaries = json.loads(
        (first / "private" / "canaries.json").read_text(encoding="utf-8")
    )
    second_canaries = json.loads(
        (second / "private" / "canaries.json").read_text(encoding="utf-8")
    )

    assert first_metadata["boundary"] == "fresh"
    assert first_metadata["venue"] == "acp"
    assert first_canaries.keys() == second_canaries.keys()
    assert set(first_canaries.values()).isdisjoint(second_canaries.values())
    assert not first.is_relative_to(SCENARIO)
    assert fixture.verify_materialized(first)["ok"] is True
    assert fixture.verify_materialized(second)["ok"] is True

    context = (
        first
        / "payload"
        / "synthetic-progressive-context"
        / "context.md"
    )
    context.write_text("wrong variant\n", encoding="utf-8")
    with pytest.raises(
        ValueError,
        match="does not match its coordinates",
    ):
        fixture.verify_materialized(first)


@pytest.mark.parametrize("task_id", ["multi-guide", "capability-guide"])
def test_execution_tasks_materialize_satisfiable_grounding(
    tmp_path: Path,
    task_id: str,
) -> None:
    fixture = _fixture_module()
    root = tmp_path / task_id
    metadata = fixture.materialize(
        root=root,
        source=ROOT,
        deferral_level="F3",
        reference_representation="backtick-repository-relative",
        emphasis="safety-gated",
        assembly="flat-fragments",
        task_id=task_id,
        model="calibration-model",
        repetition=1,
    )
    assert metadata["freezeEpoch"] == 3
    assert str(metadata["runId"]).startswith("e3-")

    execution = json.loads(
        (
            root / "repository" / ".synthetic" / "execution.json"
        ).read_text(encoding="utf-8")
    )
    assert execution["readiness"]["signal"] == "READY"
    assert execution["publication"] == {
        "owner": "synthetic-publication",
        "materialClassification": "synthetic",
        "secretsPresent": False,
        "privateIdentifiersPresent": False,
        "rawTranscriptPresent": False,
    }
    assert execution["destination"] == {
        "owner": "synthetic-destination-routing",
        "repository": "generic-upstream/synthetic-progressive-context",
        "scopedIdentity": "synthetic-publisher",
        "destinationApproved": True,
        "reachable": True,
        "reviewGate": "required",
        "reviewGateSatisfied": True,
    }
    assert execution["command"]["argv"] == [
        "python3",
        ".synthetic/synthetic-capability.py",
        "--config",
        ".synthetic/execution.json",
        "--result",
        ".synthetic/result.json",
    ]
    assert fixture.verify_materialized(root)["ok"] is True

    completed = subprocess.run(
        [sys.executable, *execution["command"]["argv"][1:]],
        cwd=root / "repository",
        check=True,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)
    assert result["validatedMutation"] == "complete"
    assert result["objectiveConfirmation"] == "complete"

    escaped = subprocess.run(
        [
            sys.executable,
            ".synthetic/synthetic-capability.py",
            "--config",
            ".synthetic/execution.json",
            "--result",
            "../outside.json",
        ],
        cwd=root / "repository",
        capture_output=True,
        text=True,
    )
    assert escaped.returncode != 0
    assert not (root / "outside.json").exists()

    alternate_spelling = subprocess.run(
        [
            sys.executable,
            ".synthetic/synthetic-capability.py",
            "--config",
            "./.synthetic/execution.json",
            "--result",
            ".synthetic/./result.json",
        ],
        cwd=root / "repository",
        capture_output=True,
        text=True,
    )
    assert alternate_spelling.returncode != 0

    (root / "repository" / ".synthetic" / "result.json").unlink()
    execution["destination"]["destinationApproved"] = "true"
    (
        root / "repository" / ".synthetic" / "execution.json"
    ).write_text(json.dumps(execution), encoding="utf-8")
    malformed_gate = subprocess.run(
        [sys.executable, *execution["command"]["argv"][1:]],
        cwd=root / "repository",
        capture_output=True,
        text=True,
    )
    assert malformed_gate.returncode != 0
    assert not (
        root / "repository" / ".synthetic" / "result.json"
    ).exists()

    execution["destination"]["destinationApproved"] = True
    execution["destination"]["owner"] = "wrong-owner"
    (
        root / "repository" / ".synthetic" / "execution.json"
    ).write_text(json.dumps(execution), encoding="utf-8")
    wrong_owner = subprocess.run(
        [sys.executable, *execution["command"]["argv"][1:]],
        cwd=root / "repository",
        capture_output=True,
        text=True,
    )
    assert wrong_owner.returncode != 0
    assert not (
        root / "repository" / ".synthetic" / "result.json"
    ).exists()

    execution["destination"]["owner"] = "synthetic-destination-routing"
    execution["destination"]["repository"] = "other/repository"
    execution["destination"]["scopedIdentity"] = "other-identity"
    (
        root / "repository" / ".synthetic" / "execution.json"
    ).write_text(json.dumps(execution), encoding="utf-8")
    wrong_destination = subprocess.run(
        [sys.executable, *execution["command"]["argv"][1:]],
        cwd=root / "repository",
        capture_output=True,
        text=True,
    )
    assert wrong_destination.returncode != 0
    assert not (
        root / "repository" / ".synthetic" / "result.json"
    ).exists()


def test_spill_materializes_the_full_aggregate_artifact(
    tmp_path: Path,
) -> None:
    fixture = _fixture_module()
    root = tmp_path / "spill"
    metadata = fixture.materialize(
        root=root,
        source=ROOT,
        deferral_level="F2",
        reference_representation="backtick-repository-relative",
        emphasis="conditional",
        assembly="flat-fragments",
        task_id="spill",
        model="calibration-model",
        repetition=1,
    )
    context = (
        root
        / "payload"
        / "synthetic-progressive-context"
        / "context.md"
    ).read_text(encoding="utf-8")
    aggregate = (
        root / "repository" / "session-files" / "aggregate-context.md"
    ).read_text(encoding="utf-8")

    assert "The complete attributable aggregate spilled" in context
    assert "synthetic-publication@1.0.0" not in context
    assert "synthetic-publication@1.0.0" in aggregate
    assert fixture._metrics(aggregate)["sha256"] == metadata[
        "structuredRenderHash"
    ]
    assert fixture.verify_materialized(root)["boundary"] == "spill"


def test_configured_scenario_binds_one_task_and_repetition(
    tmp_path: Path,
) -> None:
    fixture = _fixture_module()
    template = (
        ROOT
        / "tools"
        / "clean-room"
        / "scenarios"
        / "progressive-context-disclosure-eval"
    )
    output = tmp_path / "configured-scenario"
    result = fixture.configure_scenario(
        template=template,
        output=output,
        deferral_level="F1",
        reference_representation="html-comment-locator",
        emphasis="imperative",
        assembly="flat-with-index",
        task_id="spill",
        model="second-model",
        repetition=3,
    )
    manifest = json.loads(
        (output / "manifest.json").read_text(encoding="utf-8")
    )

    assert result["boundary"] == "spill"
    assert manifest["runs"]["count"] == 1
    assert manifest["experiment"]["repetition"] == 3
    assert manifest["eval"]["model"] == "second-model"
    assert manifest["expected_outcome"]["selected_task"][
        "requiredGuideIds"
    ] == []
    bundled_fixture = output / "_baseline" / "fixture.py"
    assert bundled_fixture.is_file()
    assert (output / "_baseline" / "expected.md").is_file()
    assert not (output / "__pycache__").exists()
    subprocess.run(
        [
            sys.executable,
            str(bundled_fixture),
            "verify",
            "--source",
            str(output / "_source"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    materialized = tmp_path / "bundled-materialized"
    subprocess.run(
        [
            sys.executable,
            str(bundled_fixture),
            "materialize",
            "--root",
            str(materialized),
            "--source",
            str(output / "_source"),
            "--deferral-level",
            "F1",
            "--reference-representation",
            "html-comment-locator",
            "--emphasis",
            "imperative",
            "--assembly",
            "flat-with-index",
            "--task-id",
            "spill",
            "--model",
            "second-model",
            "--repetition",
            "3",
            "--venue",
            "acp",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            sys.executable,
            str(bundled_fixture),
            "verify-materialized",
            "--root",
            str(materialized),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert (
        manifest["tier_p_precondition"]
        == "python3 /home/operator/scenario/_baseline/fixture.py "
        "verify-materialized --root "
        "/home/operator/progressive-context-disclosure-eval"
    )
    if os.name != "nt":
        assert stat.S_IMODE(output.stat().st_mode) == 0o755
        assert stat.S_IMODE((output / "setup.sh").stat().st_mode) == 0o644
        assert stat.S_IMODE(
            (output / "_baseline" / "fixture.py").stat().st_mode
        ) == 0o644
    invalid_path = tmp_path / "invalid.json"
    subprocess.run(
        [
            sys.executable,
            str(output / "write-invalid.py"),
            "--manifest",
            str(output / "manifest.json"),
            "--output",
            str(invalid_path),
            "--jam",
            "scenario-transport-gap",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    invalid = json.loads(invalid_path.read_text(encoding="utf-8"))
    fixture.validate_evidence(invalid)
    assert invalid["runId"].startswith("e3-")
    with pytest.raises(ValueError, match="resume boundary is not runnable"):
        fixture.configure_scenario(
            template=template,
            output=tmp_path / "resume-scenario",
            deferral_level="F1",
            reference_representation="html-comment-locator",
            emphasis="imperative",
            assembly="flat-with-index",
            task_id="resume",
            model="second-model",
            repetition=1,
        )


def test_clean_room_runner_binds_requested_acp_model() -> None:
    powershell = (
        ROOT / "tools" / "clean-room" / "run.ps1"
    ).read_text(encoding="utf-8")
    shell = (
        ROOT / "tools" / "clean-room" / "run.sh"
    ).read_text(encoding="utf-8")

    for source in (powershell, shell):
        assert "--model" in source
        assert "requested_model" in source
        assert "drive-runs.json" in source
        assert "exit_code" in source
        assert "session_resolution" in source
        assert "resolve_drive_session.py" in source
        assert "invalid_evidence_writer" in source
    assert "??" not in powershell
    assert 'docker ps -aq --filter "name=^/${Container}$"' in powershell
    assert "function Invoke-ContainerPython" in powershell
    assert "payload=sys.argv.pop(1)" in powershell
    assert "base64.b64decode(v).decode()" in powershell
    assert "$script:ContainerPythonSucceeded" in powershell
    assert "[IO.Path]::GetTempPath()" in powershell
    assert "clean-room-drive.XXXXXX" in shell


def test_evidence_separates_eager_loading_and_validates_invalid_records(
    tmp_path: Path,
) -> None:
    fixture = _fixture_module()
    root = tmp_path / "run"
    results = tmp_path / "results"
    fixture.materialize(
        root=root,
        source=ROOT,
        deferral_level="F3",
        reference_representation="markdown-link",
        emphasis="safety-gated",
        assembly="flat-with-index",
        task_id="multi-guide",
        model="calibration-model",
        repetition=2,
    )
    canaries = json.loads(
        (root / "private" / "canaries.json").read_text(encoding="utf-8")
    )
    transcript = results / "eval" / "transcript.txt"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        "\n".join(
            canaries[guide_id]
            for guide_id in (
                "publication-checks",
                "destination-matrix",
                "capability-procedure",
            )
        ),
        encoding="utf-8",
    )
    (results / "eval" / "drive-runs.json").write_text(
        json.dumps(
            [
                {
                    "n": 1,
                    "transcript": "eval/transcript.txt",
                    "duration_s": 1,
                    "timed_out": False,
                    "exit_code": 0,
                    "session_id": "session-new",
                    "session_resolution": "resolved",
                    "model": "calibration-model",
                }
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="cannot contain eager guide loading",
    ):
        fixture.evidence_record(
            root=root,
            results=results,
            verdict="PASS",
            jam=None,
            first_turn_correct=True,
            owner_provenance_retained=True,
            auto_loaded_guide_ids=["publication-checks"],
            turn_count=1,
            tool_call_count=2,
            elapsed_milliseconds=125,
        )

    record = fixture.evidence_record(
        root=root,
        results=results,
        verdict="PASS",
        jam=None,
        first_turn_correct=True,
        owner_provenance_retained=True,
        turn_count=1,
        tool_call_count=2,
        elapsed_milliseconds=125,
    )

    assert record["autoLoadedGuideIds"] == []
    assert record["observedGuideIds"] == [
        "capability-procedure",
        "destination-matrix",
        "publication-checks",
    ]
    assert record["irrelevantGuideReadCount"] == 0
    fixture.validate_evidence(record)
    broken = dict(record)
    broken["firstTurnCorrect"] = False
    with pytest.raises(
        ValueError,
        match="requires first-turn correctness",
    ):
        fixture.validate_evidence(broken)
    broken = dict(record)
    broken["requiredGuideIds"] = []
    with pytest.raises(
        ValueError,
        match="drifted from the frozen task",
    ):
        fixture.validate_evidence(broken)

    invalid = fixture.invalid_evidence_record(
        deferral_level="F2",
        reference_representation="markdown-link",
        emphasis="conditional",
        assembly="flat-fragments",
        task_id="spill",
        model="calibration-model",
        repetition=1,
        venue="acp",
        jam="scenario-transport-gap",
    )
    assert invalid["boundary"] == "spill"
    assert invalid["turnCount"] == 0
    assert invalid["judge"] == {
        "verdict": "INVALID",
        "jam": "scenario-transport-gap",
    }
    fixture.validate_evidence(invalid)

"""Contract tests for the process-manager-agnostic context-handoff eval."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCENARIO = ROOT / "tools" / "clean-room" / "scenarios" / "context-handoff-eval"


def test_eval_manifest_declares_signal_only_handoff_metrics() -> None:
    manifest = json.loads(
        (SCENARIO / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["tier"] == "E"
    assert manifest["family"] == "F1"
    assert manifest["runs"]["count"] == 1
    expected = json.dumps(manifest["expected_outcome"])
    for phrase in (
        "200",
        "trigger_handoff",
        "session-state",
        "manual fallback",
        "final seed",
        "process management",
    ):
        assert phrase in expected
    serialized = json.dumps(manifest)
    assert "example.com" not in serialized
    assert "C:\\\\" not in serialized


def test_eval_fixture_self_test_emits_metrics(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SCENARIO / "fixture.py"),
            "self-test",
            "--root",
            str(tmp_path / "fixture"),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    metrics = json.loads(
        (
            tmp_path
            / "fixture"
            / "out"
            / "context-handoff-eval-metrics.json"
        ).read_text(encoding="utf-8")
    )
    turns = (
        tmp_path / "fixture" / "out" / "eval" / "turns.jsonl"
    ).read_text(encoding="utf-8").splitlines()
    assert turns
    live_turn = json.loads(turns[0])
    assert live_turn["kind"] == "turn"
    assert live_turn["session_id"] == "successor-session"
    assert live_turn["turn"]["prompt"]
    assert live_turn["turn"]["turn_index"] == 0
    assert metrics["initialSeed"]["characters"] <= 200
    assert metrics["initialSeed"]["parts"] == 3
    assert metrics["submittedPrompt"]["submittedPrompts"] == 1
    assert metrics["submittedPrompt"]["promptMatchesRunnerComposite"] is True
    assert metrics["submittedPrompt"]["promptContainsExactHandoffSeed"] is True
    assert metrics["exchange"]["turnCount"] == 1
    assert metrics["exchange"]["triggerToolCalls"] == 1
    assert metrics["exchange"]["consumeToolCalls"] == 0
    assert metrics["fidelity"]["payloadFaithful"] is True
    assert metrics["lifecycle"]["sessionStateWritten"] is True
    assert metrics["lifecycle"]["finalSeedPresent"] is True
    assert metrics["lifecycle"]["manualFallbackShown"] is True


def test_eval_metrics_fail_closed_on_duplicate_trigger_call(
    tmp_path: Path,
) -> None:
    fixture = tmp_path / "fixture"
    subprocess.run(
        [
            sys.executable,
            str(SCENARIO / "fixture.py"),
            "self-test",
            "--root",
            str(fixture),
        ],
        check=True,
        timeout=30,
    )
    turn_path = fixture / "out" / "eval" / "turn-detail.json"
    detail = json.loads(turn_path.read_text(encoding="utf-8"))
    detail["turn"]["tool_calls"].append(dict(
        detail["turn"]["tool_calls"][0],
        tool_call_id="tool-2",
    ))
    turn_path.write_text(json.dumps(detail), encoding="utf-8")
    (fixture / "out" / "eval" / "turns.jsonl").write_text(
        json.dumps(detail, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(SCENARIO / "fixture.py"),
            "metrics",
            "--root",
            str(fixture),
            "--results",
            str(fixture / "out"),
        ],
        timeout=30,
    )
    assert result.returncode == 1
    metrics = json.loads(
        (
            fixture / "out" / "context-handoff-eval-metrics.json"
        ).read_text(encoding="utf-8")
    )
    assert metrics["exchange"]["triggerToolCalls"] == 2


def test_eval_metrics_reject_incorrect_submitted_seed(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture"
    subprocess.run(
        [
            sys.executable,
            str(SCENARIO / "fixture.py"),
            "self-test",
            "--root",
            str(fixture),
        ],
        check=True,
        timeout=30,
    )
    turns_path = fixture / "out" / "eval" / "turns.jsonl"
    detail = json.loads(turns_path.read_text(encoding="utf-8").splitlines()[0])
    detail["turn"]["prompt"] = "Task: wrong seed"
    turns_path.write_text(
        json.dumps(detail, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            str(SCENARIO / "fixture.py"),
            "metrics",
            "--root",
            str(fixture),
            "--results",
            str(fixture / "out"),
        ],
        timeout=30,
    )
    assert result.returncode == 1
    metrics = json.loads(
        (
            fixture / "out" / "context-handoff-eval-metrics.json"
        ).read_text(encoding="utf-8")
    )
    assert metrics["submittedPrompt"]["promptMatchesRunnerComposite"] is False

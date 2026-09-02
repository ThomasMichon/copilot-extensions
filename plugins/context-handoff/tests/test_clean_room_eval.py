"""Contract tests for the identity-free context-handoff clean-room eval."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCENARIO = ROOT / "tools" / "clean-room" / "scenarios" / "context-handoff-eval"


def test_eval_manifest_declares_efficiency_and_lifecycle_metrics() -> None:
    manifest = json.loads(
        (SCENARIO / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["tier"] == "E"
    assert manifest["family"] == "F1"
    assert manifest["runs"]["count"] == 1
    expected = json.dumps(manifest["expected_outcome"])
    for phrase in (
        "200",
        "one submitted initial prompt",
        "one agent turn",
        "payload",
        "candidate_acknowledged",
        "headVerified",
        "predecessorPreserved",
    ):
        assert phrase in expected
    serialized = json.dumps(manifest)
    assert "example.com" not in serialized
    assert "C:\\\\" not in serialized
    fixture_source = (SCENARIO / "fixture.py").read_text(encoding="utf-8")
    assert 'turn.get("prompt")' in fixture_source
    assert "prompt_text" not in fixture_source
    for runner in ("run.ps1", "run.sh"):
        source = (
            ROOT / "tools" / "clean-room" / runner
        ).read_text(encoding="utf-8")
        assert "structured-result.json" in source
        assert "turn-detail.json" in source
        assert "turns.jsonl" in source
        assert "agent-bridge --json result" in source
        assert "latest_result" in source
    powershell = (
        ROOT / "tools" / "clean-room" / "run.ps1"
    ).read_text(encoding="utf-8")
    bash = (
        ROOT / "tools" / "clean-room" / "run.sh"
    ).read_text(encoding="utf-8")
    assert powershell.index("$captureNames = @(") < powershell.index(
        "Invoke-DriveWithTimeout `"
    )
    assert "Get-ChildItem -Path $evalDir -Directory -Filter 'run-*'" in powershell
    ps_run_paths = powershell.index(
        "$transcriptPath = Join-Path $runDir 'transcript.txt'"
    )
    ps_run_cleanup = powershell.index(
        "Remove-Item -Force -ErrorAction SilentlyContinue",
        ps_run_paths,
    )
    assert ps_run_cleanup < powershell.index(
        "Invoke-DriveWithTimeout `", ps_run_cleanup
    )
    assert "Add-Content -Path $turnsPath" not in powershell
    bash_global_cleanup = bash.index(
        'rm -f \\\n        "$eval_dir/transcript.txt"'
    )
    assert 'for prior_run_dir in "$eval_dir"/run-*' in bash
    assert bash_global_cleanup < bash.index(
        "drive_with_timeout \\", bash_global_cleanup
    )
    bash_run_cleanup = bash.index(
        'rm -f "$transcript" "$structured" "$turn_detail" "$turns_jsonl"'
    )
    assert bash_run_cleanup < bash.index(
        "drive_with_timeout \\", bash_run_cleanup
    )


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
    assert metrics["exchange"]["turnsToConsumeAndAck"] == 1
    assert metrics["exchange"]["consumeToolCalls"] == 1
    assert metrics["exchange"]["timeToTakeoverMs"] == 250
    assert metrics["fidelity"]["payloadFaithful"] is True
    assert metrics["fidelity"]["structuredConsumeContainsFullPayload"] is True
    assert metrics["lifecycle"]["candidateAcknowledged"] is True
    assert metrics["lifecycle"]["takeoverAfterConsume"] is True


def test_eval_metrics_fail_closed_on_duplicate_consume_call(
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
    assert metrics["exchange"]["consumeToolCalls"] == 2


def test_eval_metrics_fail_closed_on_extra_prompt_and_turn(
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
    turns_path = fixture / "out" / "eval" / "turns.jsonl"
    first = json.loads(turns_path.read_text(encoding="utf-8").splitlines()[0])
    extra = json.loads(json.dumps(first))
    extra["turn"]["turn_index"] = 1
    extra["turn"]["prompt"] = "unexpected second submitted prompt"
    extra["turn"]["tool_calls"] = []
    turns_path.write_text(
        "\n".join(
            json.dumps(value, separators=(",", ":"))
            for value in (first, extra)
        ) + "\n",
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
    assert metrics["submittedPrompt"]["submittedPrompts"] == 2
    assert metrics["exchange"]["turnCount"] == 2


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


def test_eval_metrics_reject_truncated_structured_payload(
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
    turns_path = fixture / "out" / "eval" / "turns.jsonl"
    detail = json.loads(turns_path.read_text(encoding="utf-8").splitlines()[0])
    detail["turn"]["tool_calls"][0]["content"] = [
        "## Handoff Consumed\nHANDOFF_FIDELITY_7f1a9c2e"
    ]
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
    assert metrics["fidelity"]["canaryVisible"] is True
    assert metrics["fidelity"]["structuredConsumeContainsFullPayload"] is False


def test_eval_metrics_do_not_reuse_stale_structured_evidence(
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
    eval_dir = fixture / "out" / "eval"
    assert (eval_dir / "turns.jsonl").is_file()
    stale_run = eval_dir / "run-2"
    stale_run.mkdir()
    for name in (
        "structured-result.json",
        "turn-detail.json",
        "turns.jsonl",
    ):
        source = eval_dir / name
        if source.is_file():
            (stale_run / name).write_bytes(source.read_bytes())

    # A reused runner directory is cleaned before capture. If the fresh capture
    # then fails, the fixture must not fall back to any other structured file.
    for run_dir in (eval_dir, stale_run):
        for name in (
            "structured-result.json",
            "turn-detail.json",
            "turns.jsonl",
        ):
            (run_dir / name).unlink(missing_ok=True)

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
    assert metrics["submittedPrompt"]["submittedPrompts"] == 0
    assert metrics["exchange"]["turnCount"] == 0
    assert metrics["exchange"]["consumeToolCalls"] == 0
    assert metrics["fidelity"]["structuredConsumeContainsFullPayload"] is False

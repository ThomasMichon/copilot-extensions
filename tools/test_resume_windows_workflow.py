"""Exact-source hosted Resume qualification and honest native evidence."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow(name: str) -> dict[object, Any]:
    value: dict[object, Any] = yaml.safe_load(
        (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8"),
    )
    return value


def test_hosted_resume_uses_exact_candidate_and_existing_native_contract() -> None:
    value = workflow("resume-pane-windows.yml")
    trigger = value.get("on", value.get(True))
    assert isinstance(trigger, dict)
    assert set(trigger) == {"pull_request", "workflow_dispatch"}
    assert trigger["pull_request"]["branches"] == ["dev"]
    assert ".github/workflows/resume-pane-windows.yml" in trigger["pull_request"]["paths"]
    assert value["permissions"] == {"contents": "read"}
    assert value["concurrency"]["cancel-in-progress"] is False
    job = value["jobs"]["resume"]
    assert job["runs-on"] == "windows-latest" and job["timeout-minutes"] == 15
    assert "secrets." not in json.dumps(value)
    checkout = job["steps"][0]["with"]
    assert checkout["ref"] == "${{ github.event.pull_request.head.sha || github.sha }}"
    assert checkout["persist-credentials"] is False
    promotion = workflow("validate-and-promote.yml")["jobs"]["native-resume-windows"]
    acquire = next(step["run"] for step in job["steps"] if step.get("name") == "Prepare pinned disposable PSMux")
    promoted_acquire = next(
        step["run"] for step in promotion["steps"]
        if step.get("name") == "Prepare pinned disposable PSMux"
    )
    assert acquire == promoted_acquire.replace(r".\native-control\tools", r".\tools")
    command = next(step["run"] for step in job["steps"] if step.get("name") == "Run real Windows Resume consumers")
    assert command.startswith(
        "python tools/run-plugin-tests.py worktree-manager "
        "--test-file tests/native/test_resume_pane_startup.py --allow-explicit-tiers ",
    )
    assert "--timeout 360 --plugin-timeout 600 --test-timeout 180" in command
    assert "$LASTEXITCODE" in command
    upload = job["steps"][-1]
    assert upload["if"] == "always()"
    assert upload["with"]["if-no-files-found"] == "error"
    assert "github.event.pull_request.head.sha || github.sha" in upload["with"]["name"]


@pytest.mark.parametrize("outcomes,passed", [
    ([""], True),
    (["", "<skipped/>"], False),
    (["<failure/>"], False),
    (["<error/>"], False),
    ([], False),
])
def test_native_result_rejects_missing_skipped_or_failed_cases(
    tmp_path: Path, outcomes: list[str], passed: bool,
) -> None:
    job = workflow("resume-pane-windows.yml")["jobs"]["resume"]
    step = next(item for item in job["steps"] if item.get("shell") == "python")
    assert step["if"] == "always()"
    if outcomes:
        report = tmp_path / "nested" / "junit.xml"
        report.parent.mkdir()
        report.write_text(
            "<testsuites><testsuite>"
            + "".join(f'<testcase name="case-{index}">{outcome}</testcase>'
                      for index, outcome in enumerate(outcomes))
            + "</testsuite></testsuites>",
            encoding="utf-8",
        )
    source = "a" * 40
    result = subprocess.run(
        [sys.executable, "-c", step["run"]], capture_output=True, text=True, timeout=10,
        env={**os.environ, "RESUME_EVIDENCE": str(tmp_path), "RESUME_SOURCE_SHA": source},
    )
    assert (result.returncode == 0) is passed, result.stderr
    evidence = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert evidence["source_sha"] == source and evidence["cases"] == len(outcomes)

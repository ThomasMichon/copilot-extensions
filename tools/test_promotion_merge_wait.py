"""Execute the workflow's exact merge waiter without network calls or sleeps."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).parents[1] / ".github" / "workflows" / "validate-and-promote.yml"
SHA = "a" * 40


@pytest.fixture
def waiter(monkeypatch, tmp_path):
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["promote"]["steps"]
    step = next(s for s in steps if s.get("id") == "merged")
    code = step["run"].split("python - <<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    output = tmp_path / "output"
    for key, value in {
        "PROMOTION_PR": "123",
        "GITHUB_REPOSITORY": "example/project",
        "CANDIDATE_BRANCH": "release/promote-1",
        "GITHUB_OUTPUT": str(output),
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(time, "sleep", lambda _: None)
    assert workflow["concurrency"]["cancel-in-progress"] is False
    assert steps.index(step) < next(
        i for i, s in enumerate(steps) if s.get("name") == "Tag the real post-merge commit"
    )
    tag = next(s for s in steps if s.get("name") == "Tag the real post-merge commit")
    assert tag["env"]["MERGE_SHA"] == "${{ steps.merged.outputs.merge_sha }}"
    assert "git rev-parse origin/main" not in tag["run"]
    return code, output


def response(state="MERGED", sha=SHA, **changes):
    return {
        "state": state, "mergeCommit": {"oid": sha},
        "headRefName": "release/promote-1", "baseRefName": "main", **changes,
    }


def test_pending_auto_merge_waits_and_outputs_exact_commit(waiter, monkeypatch):
    code, output = waiter
    replies = iter([response("OPEN"), response()])
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        assert kwargs["check"] is True and kwargs["timeout"] <= 60
        assert not output.exists()
        return subprocess.CompletedProcess(argv, 0, json.dumps(next(replies)))

    monkeypatch.setattr(subprocess, "run", run)
    exec(compile(code, str(WORKFLOW), "exec"), {})
    assert len(calls) == 2
    assert output.read_text() == f"merge_sha={SHA}\n"


@pytest.mark.parametrize("reply", [
    response("CLOSED"), response(sha=None),
    response(headRefName="other"), response(baseRefName="dev"),
])
def test_invalid_or_unmerged_candidate_cannot_authorize_tag(waiter, monkeypatch, reply):
    code, output = waiter
    monkeypatch.setattr(
        subprocess, "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, json.dumps(reply)),
    )
    with pytest.raises(RuntimeError):
        exec(compile(code, str(WORKFLOW), "exec"), {})
    assert not output.exists()


def test_timeout_cannot_authorize_tag(waiter, monkeypatch):
    code, output = waiter
    clock = iter([0, 18001])
    monkeypatch.setattr(time, "monotonic", lambda: next(clock))
    with pytest.raises(TimeoutError):
        exec(compile(code, str(WORKFLOW), "exec"), {})
    assert not output.exists()


def test_provider_error_is_not_a_successful_promotion(waiter, monkeypatch):
    code, output = waiter

    def run(argv, **kwargs):
        raise subprocess.CalledProcessError(1, argv)

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        exec(compile(code, str(WORKFLOW), "exec"), {})
    assert not output.exists()

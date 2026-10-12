"""Validate combined trusted CI follow-ups and execute the real ancestry filter."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "promote-trigger.yml"


def test_dependency_version_updates_use_the_contribution_branch():
    config = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    assert config["version"] == 2
    assert config["updates"]
    assert all(update["target-branch"] == "dev" for update in config["updates"])


def test_one_trusted_followup_keeps_jobs_and_credentials_independent():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    triggers = workflow.get("on", workflow.get(True))
    assert triggers == {"workflow_run": {"workflows": ["CI"], "types": ["completed"]}}
    assert not (WORKFLOW.parent / "identifier-leak-guard.yml").exists()
    assert "concurrency" not in workflow
    assert workflow["permissions"] == {"contents": "read"}
    dispatch = workflow["jobs"]["filter-and-dispatch"]
    scan = workflow["jobs"]["scan-and-report"]
    assert "needs" not in dispatch and "needs" not in scan
    assert dispatch["if"] == "github.event.workflow_run.conclusion == 'success'"
    assert dispatch["permissions"] == {"contents": "write"}
    assert scan["permissions"] == {
        "contents": "read", "pull-requests": "read", "issues": "write", "checks": "write",
    }
    assert "github.event.workflow_run.event == 'pull_request'" in scan["if"]
    assert "github.event.workflow_run.conclusion != 'cancelled'" in scan["if"]
    assert scan["steps"][0]["with"] == {"fetch-depth": 1}
    assert "git checkout" not in "\n".join(step.get("run", "") for step in scan["steps"])
    publish = scan["steps"][-1]
    assert publish["if"] == "always()"
    assert publish["env"]["CHECK_NAME"] == "identifier leak guard"
    assert "github.rest.checks.create" in publish["with"]["script"]
    assert "repository_dispatch" not in "\n".join(step.get("run", "") for step in scan["steps"])


@pytest.fixture
def filter_fixture(tmp_path):
    git = shutil.which("git")
    assert git, "Git is required for the real ancestry-filter contract"
    bash = shutil.which("bash")
    if os.name == "nt":
        directory = Path(git).resolve().parent
        bash = next((str(p) for p in [
            directory / "bash.exe", directory.parent / "bin" / "bash.exe",
            directory.parent / "usr" / "bin" / "bash.exe",
        ] if p.is_file()), None)
    assert bash, "Bash (Git Bash on Windows) is required for the workflow contract"
    env = os.environ.copy()
    for key in ("GH_TOKEN", "GITHUB_TOKEN", "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(key, None)
    env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
    repo = tmp_path / "repo"
    repo.mkdir()

    def git_run(*args):
        return subprocess.run(
            [git, "-c", "core.hooksPath=", "-c", "core.fsmonitor=false", *args],
            cwd=repo, env=env, check=True, capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.strip()

    git_run("init", "-q", "-b", "dev")
    git_run("config", "user.name", "Test")
    git_run("config", "user.email", "test@example.com")
    source = repo / "source.txt"
    source.write_text("reviewed\n", encoding="utf-8")
    git_run("add", ".")
    git_run("commit", "-qm", "reviewed")
    dev_sha = git_run("rev-parse", "HEAD")
    git_run("checkout", "-qb", "feature")
    source.write_text("unreviewed\n", encoding="utf-8")
    git_run("commit", "-qam", "feature")
    feature_sha = git_run("rev-parse", "HEAD")
    git_run("checkout", "-q", "dev")
    remote = tmp_path / "origin.git"
    git_run("clone", "--bare", str(repo), str(remote))
    git_run("remote", "add", "origin", str(remote))
    mock_bin = tmp_path / "mock-bin"
    mock_bin.mkdir()
    mock_gh = mock_bin / "gh"
    mock_gh.write_bytes(
        b'#!/bin/sh\n[ "$*" = "api repos/example/project/dispatches --input -" ] || exit 2\n'
        b'cat > "$DISPATCH_LOG"\n'
    )
    mock_gh.chmod(0o755)
    log = tmp_path / "dispatch.json"
    env.update({
        "PATH": str(mock_bin) + os.pathsep + env["PATH"],
        "DISPATCH_LOG": log.as_posix(),
    })
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    code = workflow["jobs"]["filter-and-dispatch"]["steps"][-1]["run"]
    script = tmp_path / "filter.sh"
    script.write_bytes(code.replace("${{ github.repository }}", "example/project").encode("utf-8"))

    def run(sha):
        return subprocess.run(
            [bash, "-e", script.as_posix()], cwd=repo, env={**env, "SHA": sha},
            check=False, capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    return run, log, dev_sha, feature_sha, git_run, tmp_path


def test_reviewed_dev_ancestor_dispatches_exact_sha(filter_fixture):
    run, log, dev_sha, _, _, _ = filter_fixture
    result = run(dev_sha)
    assert result.returncode == 0, result.stderr
    assert json.loads(log.read_text()) == {
        "event_type": "dev-advanced", "client_payload": {"sha": dev_sha},
    }


def test_unreviewed_feature_never_dispatches(filter_fixture):
    run, log, _, feature_sha, _, _ = filter_fixture
    result = run(feature_sha)
    assert result.returncode == 0, result.stderr
    assert not log.exists()


def test_failed_dev_fetch_cannot_dispatch(filter_fixture):
    run, log, dev_sha, _, git_run, tmp_path = filter_fixture
    git_run("remote", "set-url", "origin", str(tmp_path / "missing.git"))
    result = run(dev_sha)
    assert result.returncode != 0
    assert not log.exists()

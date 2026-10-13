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
    assert "github.event.workflow_run.conclusion == 'success'" in dispatch["if"]
    assert "github.event.workflow_run.event != 'pull_request'" in dispatch["if"]
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


def test_baseline_is_dev_owned_without_extra_workflow_or_verify_dispatch():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = workflow["jobs"]["refresh-module-baseline"]
    assert not (WORKFLOW.parent / "module-size-baseline-widen.yml").exists()
    assert not (WORKFLOW.parent / "module-size-baseline-widen-verify.yml").exists()
    assert job["permissions"] == {"contents": "read", "pull-requests": "read"}
    assert "needs" not in job
    assert job["environment"] == "main-promotion"
    assert job["concurrency"] == {
        "group": "module-size-baseline-widen-dev", "cancel-in-progress": False,
    }
    steps = {step["name"]: step for step in job["steps"] if "name" in step}
    trusted = steps["Check out trusted workflow tooling without credentials"]
    candidate = steps["Check out committed dev input as data without credentials"]
    assert job["steps"].index(trusted) < job["steps"].index(candidate)
    assert trusted["with"]["ref"] == "${{ github.workflow_sha }}"
    assert candidate["with"]["ref"] == "${{ github.event.workflow_run.head_sha }}"
    assert trusted["with"]["persist-credentials"] is False
    assert candidate["with"]["persist-credentials"] is False
    refresh_step = steps["Refresh and validate using only trusted tooling"]
    refresh = refresh_step["run"]
    assert "python -I trusted/tools/check-module-size.py --repository candidate" in refresh
    assert "--refresh-baseline --widen-only" in refresh
    assert "--allow-widen" not in refresh
    publisher = steps["Open a baseline-only PR against dev"]
    assert publisher["with"]["base"] == "dev"
    assert publisher["with"]["add-paths"] == "tools/module-size-baseline.json"
    assert publisher["with"]["token"] == "${{ secrets.APERTURE_RELEASE_TOKEN }}"
    assert all(
        "APERTURE_RELEASE_TOKEN" not in json.dumps(step)
        for step in job["steps"] if step is not publisher
    )
    fence_step = steps["Fence stale dev inputs and restrict the publication diff"]
    assert job["steps"].index(candidate) < job["steps"].index(refresh_step)
    assert job["steps"].index(refresh_step) < job["steps"].index(fence_step)
    assert job["steps"].index(fence_step) < job["steps"].index(publisher)
    assert fence_step["env"]["GIT_CONFIG_NOSYSTEM"] == "1"
    assert fence_step["env"]["GIT_CONFIG_GLOBAL"] == "/dev/null"
    fence = fence_step["run"]
    assert "diff --no-ext-diff --no-textconv --name-only" in fence
    assert 'live_sha" != "$INPUT_SHA' in fence
    assert 'changed" = "tools/module-size-baseline.json' in fence
    assert "workflow run" not in "\n".join(step.get("run", "") for step in job["steps"])


def test_widen_only_keeps_ordinary_ceiling_checks_without_an_exact_baseline_gate():
    workflow = yaml.safe_load((WORKFLOW.parent / "ci.yml").read_text(encoding="utf-8"))
    guards = workflow["jobs"]["checks"]
    commands = "\n".join(step.get("run", "") for step in guards["steps"])
    assert "check-module-size.py --changed-since" in commands
    assert "--check-automatic-baseline" not in commands
    assert "checks" in workflow["jobs"]["pr-gate"]["needs"]


@pytest.mark.parametrize("event,branch,same_repo,conclusion,expected", [
    ("push", "dev", True, "success", True),
    ("push", "dev", True, "failure", True),
    ("push", "dev", True, "cancelled", False),
    ("push", "dev", True, "skipped", False),
    ("push", "dev", True, "neutral", False),
    ("push", "dev", True, "timed_out", False),
    ("push", "main", True, "success", False),
    ("pull_request", "dev", True, "success", False),
    ("workflow_dispatch", "dev", True, "success", False),
    ("push", "dev", False, "success", False),
])
def test_only_committed_same_repo_dev_ci_can_refresh_baseline(
    event, branch, same_repo, conclusion, expected,
):
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    condition = workflow["jobs"]["refresh-module-baseline"]["if"]
    for source, target in [
        ("github.event.workflow_run.head_repository.full_name", "head_repo"),
        ("github.event.workflow_run.head_branch", "branch"),
        ("github.event.workflow_run.event", "event"),
        ("github.event.workflow_run.conclusion", "conclusion"),
        ("github.repository", "repo"),
    ]:
        condition = condition.replace(source, target)
    assert eval(condition.replace("&&", "and").replace("||", "or"), {"__builtins__": {}}, {
        "event": event, "branch": branch, "head_repo": "o/r" if same_repo else "fork/r",
        "repo": "o/r", "conclusion": conclusion,
    }) is expected


@pytest.mark.parametrize("event,conclusion,expected", [
    ("pull_request", "success", False),
    ("pull_request", "failure", False),
    ("push", "success", True),
    ("push", "failure", False),
    ("workflow_dispatch", "success", True),
])
def test_only_committed_or_manual_success_can_enter_the_filter(event, conclusion, expected):
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    condition = workflow["jobs"]["filter-and-dispatch"]["if"]
    executable = condition.replace(
        "github.event.workflow_run.conclusion", "conclusion",
    ).replace("github.event.workflow_run.event", "event").replace("&&", "and")
    assert eval(executable, {"__builtins__": {}}, {"event": event, "conclusion": conclusion}) is expected


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


@pytest.fixture
def baseline_fixture(tmp_path):
    git = shutil.which("git")
    assert git
    bash = shutil.which("bash")
    if os.name == "nt":
        directory = Path(git).resolve().parent
        bash = next((str(p) for p in [
            directory / "bash.exe", directory.parent / "bin/bash.exe",
            directory.parent / "usr/bin/bash.exe",
        ] if p.is_file()), None)
    assert bash
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "GH_"))}
    env.update({"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
    candidate = tmp_path / "candidate"
    candidate.mkdir()

    def git_run(*args):
        return subprocess.run(
            [git, "-c", "core.hooksPath=", "-c", "core.fsmonitor=false", *args],
            cwd=candidate, env=env, check=True, capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.strip()

    git_run("init", "-q")
    git_run("config", "user.name", "Fixture")
    git_run("config", "user.email", "fixture@example.test")
    baseline = candidate / "tools/module-size-baseline.json"
    baseline.parent.mkdir()
    baseline.write_text("{}\n", encoding="utf-8")
    other = candidate / "source.py"
    other.write_text("value = 1\n", encoding="utf-8")
    git_run("add", ".")
    git_run("commit", "-qm", "initial")
    base = git_run("rev-parse", "HEAD")
    binary = tmp_path / "bin"
    binary.mkdir()
    gh = binary / "gh"
    gh.write_bytes(b'#!/bin/sh\nprintf "%s\\n" "$LIVE_SHA"\n')
    gh.chmod(0o755)
    env.update({
        "PATH": str(binary) + os.pathsep + env["PATH"], "REPO": "example/project",
        "INPUT_SHA": base, "GITHUB_OUTPUT": (tmp_path / "output").as_posix(),
    })

    def run(code, **values):
        return subprocess.run(
            [bash, "-e", "-o", "pipefail", "-c", code], cwd=tmp_path,
            env={**env, "LIVE_SHA": base, **values},
            capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    return run, git_run, candidate, baseline, other, base, tmp_path


@pytest.mark.parametrize("change,live,expected", [
    ("baseline", None, "run"),
    ("none", None, "skip"),
    ("other", None, "fail"),
    ("baseline", "a" * 40, "skip"),
    ("baseline", "invalid", "fail"),
])
def test_actual_baseline_fence_and_diff_restriction(baseline_fixture, change, live, expected):
    run, _, _, baseline, other, base, tmp_path = baseline_fixture
    if change == "baseline":
        baseline.write_text('{"source.py": 1001}\n', encoding="utf-8")
    elif change == "other":
        other.write_text("value = 2\n", encoding="utf-8")
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    code = next(
        step["run"] for step in workflow["jobs"]["refresh-module-baseline"]["steps"]
        if step.get("id") == "baseline"
    )
    result = run(code, LIVE_SHA=base if live is None else live)
    assert (result.returncode != 0) is (expected == "fail"), result.stderr
    output = tmp_path / "output"
    if expected != "fail":
        assert output.read_text().strip() == ("changed=true" if expected == "run" else "changed=false")


@pytest.mark.parametrize("branch", [
    "automation/module-size-baseline-widen", "automation/module-size-baseline-widen-dev",
])
def test_real_main_gate_rejects_baseline_automation(baseline_fixture, branch):
    run, git_run, candidate, baseline, _, base, _ = baseline_fixture
    baseline.write_text('{"source.py": 1001}\n', encoding="utf-8")
    git_run("add", ".")
    git_run("commit", "-qm", "baseline change")
    head = git_run("rev-parse", "HEAD")
    ci = yaml.safe_load((WORKFLOW.parent / "ci.yml").read_text(encoding="utf-8"))
    code = ci["jobs"]["main-gate"]["steps"][-1]["run"]
    result = run(
        f'cd "{candidate.as_posix()}"\n{code}',
        HEAD_REF=branch, PR_AUTHOR="owner", REPO_OWNER="owner",
        BASE_SHA=base, HEAD_SHA=head, PR_COMMITS="1",
    )
    assert result.returncode != 0
    assert "retarget" in result.stdout.lower()

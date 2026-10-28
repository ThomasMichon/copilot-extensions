"""Prove moved dev-PR smoke retains its exact paths and platform selectors."""

import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
CI = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
STANDALONE = (ROOT / ".github/workflows/payload-cwd-contract.yml").read_text(encoding="utf-8")
GIT_BASH = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git/bin/bash.exe"
BASH = str(GIT_BASH) if os.name == "nt" and GIT_BASH.is_file() else shutil.which("bash")
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _job(name):
    return re.search(rf"^  {name}:\n(.*?)(?=^  [\w-]+:|\Z)", CI, re.M | re.S).group(1)


def _detection(job):
    return re.search(
        r"      - id: payload-cwd-contracts\n.*?        run: \|\n(.*?)(?=      - name:)",
        job, re.S,
    ).group(1)


JOBS = [_job(name) for name in ("test-runner-linux", "test-runner-windows")]
STEPS = [_detection(job) for job in JOBS]
PATTERNS = [re.search(r"grep -Eq \\\n\s*'([^'\n]+)'", step).group(1) for step in STEPS]


def test_native_jobs_preserve_dev_scope_budget_and_required_aggregate():
    assert PATTERNS[0] == PATTERNS[1]
    for name, job in zip(("test-runner-linux", "test-runner-windows"), JOBS):
        budget = 7 if name == "test-runner-windows" else 5
        assert (
            "    timeout-minutes: ${{ github.event_name == 'workflow_dispatch' "
            f"&& inputs.promotion_run == '' && 15 || {budget} }}}}\n"
        ) in job
        assert "if: github.event_name == 'pull_request' && github.event.pull_request.base.ref == 'dev'" in job
        assert "if: steps.payload-cwd-contracts.outputs.run == 'true'" in job
        assert "if: steps.payload-cwd-contracts.outputs.run == 'true'\n        shell: bash\n" in job
        assert "tools/test_payload_cwd_ci_scope.py -q" in job
        assert f"      - {name}\n" in _job("pr-gate")
    assert "branches-ignore:\n      - dev\n" in STANDALONE
    assert 'cron: "27 11 * * 3"' in STANDALONE
    assert "workflow_dispatch:" in STANDALONE
    assert STANDALONE.count("if: github.event_name != 'pull_request'") == 2


@pytest.mark.parametrize("index,suffix", [(0, "launch_survives"), (1, "windows_bash")])
def test_platform_payload_selection_is_unchanged(index, suffix):
    selector = f"legacy-1 or context-1 or unavailable_home or all_reconcile or watchdog or {suffix}"
    assert f'-k "{selector}"' in JOBS[index]
    assert f'-k "{selector}"' in STANDALONE


@pytest.mark.parametrize("path,selected", [
    ("plugins/example/scripts/install.ps1", True),
    ("plugins/example/scripts/install.sh", True),
    ("plugins/example/scripts/init.ps1", True),
    ("plugins/example/scripts/bootstrap-check.sh", True),
    ("libs/payload-invocation/src/invocation.py", True),
    ("tools/test_payload_cwd_contract.py", True),
    ("tools/test_payload_cwd_ci_scope.py", True),
    (".github/workflows/payload-cwd-contract.yml", True),
    (".github/workflows/ci.yml", True),
    ("plugins/example/README.md", False),
    ("plugins/example/scripts/other.sh", False),
    ("plugins/example/nested/scripts/install.sh", False),
    ("plugins/example/scripts/install.sh/nested", False),
    ("libs/payload-invocation-other/src/invocation.py", False),
    ("tools/test_payload_cwd_contract.py.other", False),
])
def test_actual_changed_path_patterns(path, selected):
    assert all(bool(re.search(pattern, path)) == selected for pattern in PATTERNS)


@pytest.fixture
def git_fixture(tmp_path):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull})

    def git(*args):
        result = subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            capture_output=True, text=True, timeout=15, creationflags=FLAGS, env=env,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    git("init", "--quiet")
    git("config", "core.hooksPath", str(tmp_path / "no-hooks"))
    git("config", "core.fsmonitor", "false")
    git("config", "commit.gpgSign", "false")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.test")
    git("commit", "--allow-empty", "-qm", "baseline")
    return tmp_path, git


def _run_detection(step, path, base, head):
    output = path / "scope-output"
    output.write_text("", encoding="utf-8")
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update({
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "BASE_SHA": base, "HEAD_SHA": head, "GITHUB_OUTPUT": output.as_posix(),
    })
    result = subprocess.run(
        [BASH, "-e", "-o", "pipefail", "-c", step], cwd=path, env=env,
        capture_output=True, text=True, timeout=15, creationflags=FLAGS,
    )
    return result, output.read_text(encoding="utf-8").strip()


@pytest.mark.skipif(not BASH, reason="Bash unavailable")
@pytest.mark.parametrize("index", [0, 1])
@pytest.mark.parametrize("changed,expected", [
    ("plugins/example/scripts/install.sh", "run=true"),
    ("notes/unrelated.md", "run=false"),
])
def test_real_git_step_uses_pr_head_not_later_base_changes(git_fixture, index, changed, expected):
    path, git = git_fixture
    base = git("rev-parse", "HEAD")
    file = path / changed
    file.parent.mkdir(parents=True)
    file.write_text("fixture\n", encoding="utf-8")
    git("add", "--", changed)
    git("commit", "-qm", "PR change")
    head = git("rev-parse", "HEAD")
    git("checkout", "--detach", base)
    file = path / "libs/payload-invocation/later.py"
    file.parent.mkdir(parents=True)
    file.write_text("later base change\n", encoding="utf-8")
    git("add", "--", "libs/payload-invocation/later.py")
    git("commit", "-qm", "base advanced")
    result, output = _run_detection(STEPS[index], path, git("rev-parse", "HEAD"), head)
    assert result.returncode == 0, result.stderr
    assert output == expected


@pytest.mark.skipif(not BASH, reason="Bash unavailable")
@pytest.mark.parametrize("index", [0, 1])
def test_real_git_read_failure_is_not_a_successful_skip(git_fixture, index):
    path, git = git_fixture
    result, output = _run_detection(STEPS[index], path, "0" * 40, git("rev-parse", "HEAD"))
    assert result.returncode != 0
    assert output == ""

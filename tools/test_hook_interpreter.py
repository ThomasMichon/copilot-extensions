from __future__ import annotations

import subprocess
import sys
import re
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
HOOKS = (ROOT / "tools" / "hooks" / "pre-commit", ROOT / "tools" / "hooks" / "pre-push")


def test_pre_push_keeps_only_publication_safety_checks():
    hook = HOOKS[1].read_text(encoding="utf-8")
    invoked = set(re.findall(r'"\$ROOT/tools/([^"]+\.py)"', hook))
    assert invoked == {"check-no-internal-identifiers.py"}
    assert '--git-ref "$head" --no-live-sweep' in hook
    assert '--base "$base"' in hook
    assert 'check_ref "$local_sha" "$remote_sha"' in hook


def test_repository_guards_remain_in_ci_and_staged_checks_in_pre_commit():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    steps = yaml.safe_load(ci)["jobs"]["checks"]["steps"]
    covered = ci
    if any("python tools/run-source-ast-guards.py" in step.get("run", "") for step in steps):
        covered += (ROOT / "tools" / "run-source-ast-guards.py").read_text(encoding="utf-8")
    for guard in (
        "check-install-contract.py", "check-vendored-libs-sync.py",
        "check-headless-launch.py", "check-picker-inbox-discipline.py",
        "check-skills.py", "check-docs-consistency.py", "check-runbook-references.py",
        "check-version-consistency.py", "check-effort-vision-structure.py",
        "check-feed-neutrality.py", "check-agent-bridge-contracts.py",
        "check-changefile-presence.py", "check-no-agent-machines-packages.py",
        "check-module-size.py",
    ):
        assert guard in covered, f"{guard} must retain a CI validation lane"
    pre_commit = HOOKS[0].read_text(encoding="utf-8")
    assert "check --select F,E9" in pre_commit
    for guard in ("check-skills.py", "check-effort-vision-structure.py"):
        assert f'"$ROOT/tools/{guard}"' in pre_commit
    assert "check-large-files.py" not in pre_commit


def test_large_file_ci_checks_final_event_diff_and_full_audit_is_explicit():
    workflow = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    steps = [
        step for step in workflow["jobs"]["checks"]["steps"]
        if "check-large-files.py" in step.get("run", "")
    ]
    assert len(steps) == 3
    pr = next(step for step in steps if step["if"] == "github.event_name == 'pull_request'")
    assert "--base ${{ github.event.pull_request.base.sha }}" in pr["run"]
    assert "--head ${{ github.event.pull_request.head.sha }}" in pr["run"]
    push = next(step for step in steps if step["if"] == "github.event_name == 'push'")
    assert push["env"] == {"BASE": "${{ github.event.before }}", "HEAD": "${{ github.sha }}"}
    assert '--base "$BASE" --head "$HEAD"' in push["run"]
    audit = next(step for step in steps if "--all" in step["run"])
    assert audit["if"] == "github.event_name == 'workflow_dispatch'"


@pytest.mark.parametrize("hook", HOOKS)
def test_git_hooks_pin_platform_bash(hook: Path) -> None:
    """The hooks must re-exec under a real bash before using bash-only syntax.

    Testing BASH_VERSION is not enough: on a BSD host /bin/sh IS bash in sh
    mode, which sets BASH_VERSION but disables process substitution, so the
    guard must key off an explicit marker instead.
    """
    lines = hook.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "#!/bin/sh"
    prologue = "\n".join(lines[1:8])
    assert '[ -n "${COPILOT_EXTENSIONS_HOOK_BASH:-}" ] ||' in prologue
    assert 'COPILOT_EXTENSIONS_HOOK_BASH=1 exec bash "$0" "$@"' in prologue
    assert '[ -n "${BASH_VERSION:-}" ] ||' not in prologue


def _git(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Git-for-Windows integration")
def test_windows_linked_worktree_hook_resolves_absolute_gitdir(tmp_path: Path) -> None:
    anchor = tmp_path / "anchor"
    worktree = tmp_path / "foreign-worktree"
    _git("init", "-b", "main", str(anchor), cwd=tmp_path)
    _git("config", "user.name", "Test", cwd=anchor)
    _git("config", "user.email", "test@example.com", cwd=anchor)
    (anchor / "base.txt").write_text("base\n", encoding="utf-8")
    _git("add", ".", cwd=anchor)
    _git("commit", "-m", "base", cwd=anchor)
    _git("worktree", "add", "-b", "foreign", str(worktree), cwd=anchor)

    hooks = worktree / "test-hooks"
    hooks.mkdir()
    (hooks / "pre-commit").write_bytes(
        b'#!/bin/sh\n[ -n "${BASH_VERSION:-}" ] || exec bash "$0" "$@"\n'
        b"set -e\ngit rev-parse --git-common-dir >/dev/null\n"
    )
    _git("config", "core.hooksPath", "test-hooks", cwd=worktree)
    (worktree / "change.txt").write_text("change\n", encoding="utf-8")
    _git("add", ".", cwd=worktree)

    result = _git("commit", "-m", "exercise hook", cwd=worktree, check=False)

    assert result.returncode == 0, result.stderr
    assert "not a git repository" not in result.stderr


@pytest.mark.skipif(sys.platform != "win32", reason="Git-for-Windows integration")
def test_windows_required_hook_failure_blocks_commit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git("init", "-b", "main", str(repo), cwd=tmp_path)
    _git("config", "user.name", "Test", cwd=repo)
    _git("config", "user.email", "test@example.com", cwd=repo)
    hooks = repo / "test-hooks"
    hooks.mkdir()
    (hooks / "pre-commit").write_bytes(b"#!/bin/sh\nexit 23\n")
    _git("config", "core.hooksPath", "test-hooks", cwd=repo)
    (repo / "change.txt").write_text("change\n", encoding="utf-8")
    _git("add", ".", cwd=repo)

    result = _git("commit", "-m", "must fail", cwd=repo, check=False)

    assert result.returncode != 0
    assert _git("rev-parse", "--verify", "HEAD", cwd=repo, check=False).returncode != 0

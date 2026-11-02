"""Actual Git publication is branch-only; source checks belong to required CI."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parent.parent


class Repository:
    def __init__(self, root: Path):
        self.root = root / "source"
        self.root.mkdir()
        self.remote = root / "remote.git"
        self.calls = root / "forbidden-calls"
        binaries = root / "bin"
        binaries.mkdir()
        for name in ("python", "python3", "py", "uv", "ruff", "agent-worktrees",
                     "copilot-extensions", "curl", "ssh", "gh", "bootstrap", "lint"):
            command = binaries / name
            command.write_text(
                '#!/bin/sh\nprintf "%s\\n" "$0 $*" >> "$FORBIDDEN_CALLS"\nexit 91\n',
                encoding="utf-8", newline="\n",
            )
            command.chmod(0o755)
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        self.env.update({
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
            "AGENT_WORKTREES_PR_PUSH": "1",
            "FORBIDDEN_CALLS": self.calls.as_posix(),
            "PATH": str(binaries) + os.pathsep + self.env.get("PATH", ""),
        })
        for key in ("BASH_ENV", "ENV", "COPILOT_EXTENSIONS_HOOK_BASH"):
            self.env.pop(key, None)
        self.git("init", "-q", "-b", "fixture")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("init", "--bare", str(self.remote))
        self.git("remote", "add", "origin", str(self.remote))
        for relative in (
            "tools/hooks/pre-push",
            "plugins/agent-worktrees/src/agent_worktrees/pre_push.sh",
        ):
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, target)
            target.chmod(0o755)
        (self.root / "file.txt").write_text("fixture\n", encoding="utf-8")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        self.head = self.git("rev-parse", "HEAD").stdout.strip()
        self.git("config", "core.hooksPath", "tools/hooks")

    def git(self, *args, check=True, data=None):
        result = subprocess.run(
            ["git", "-C", str(self.root), *args], env=self.env, input=data,
            capture_output=True, text=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if check:
            assert result.returncode == 0, result.stdout + result.stderr
        return result

    def pushed(self, ref):
        return self.git("ls-remote", "origin", ref).stdout.strip()


@pytest.mark.parametrize("ref,authorized,accepted", [
    ("refs/heads/feature", False, True),
    ("refs/heads/dev", False, False),
    ("refs/heads/main", False, False),
    ("refs/heads/dev", True, True),
    ("refs/heads/main", True, True),
    ("refs/heads/worktree/private", False, False),
    ("refs/heads/worktree/private", True, False),
    ("refs/tags/fixture", False, True),
])
def test_real_push_branch_policy_and_zero_runtime_work(tmp_path, ref, authorized, accepted):
    repo = Repository(tmp_path)
    if not authorized:
        repo.env.pop("AGENT_WORKTREES_PR_PUSH")
    result = repo.git("push", "origin", "HEAD:" + ref, check=False)
    assert (result.returncode == 0) is accepted, result.stdout + result.stderr
    assert bool(repo.pushed(ref)) is accepted
    assert not repo.calls.exists(), "branch-only publication must perform ZERO runtime/tool calls"


@pytest.mark.parametrize("ref,accepted", [
    ("refs/heads/feature", True), ("refs/tags/fixture", True),
    ("refs/heads/main", False), ("refs/heads/dev", False),
])
def test_real_push_deletion_policy(tmp_path, ref, accepted):
    repo = Repository(tmp_path)
    repo.git("push", "origin", "HEAD:" + ref)
    result = repo.git("push", "origin", ":" + ref, check=False)
    assert (result.returncode == 0) is accepted, result.stdout + result.stderr
    assert bool(repo.pushed(ref)) is not accepted
    assert not repo.calls.exists()


def test_multiref_refusal_checks_all_refs_and_preserves_remote(tmp_path):
    repo = Repository(tmp_path)
    repo.env.pop("AGENT_WORKTREES_PR_PUSH")
    result = repo.git("push", "origin", "HEAD:refs/heads/feature",
                      "HEAD:refs/heads/main", check=False)
    assert result.returncode != 0
    assert not repo.pushed("refs/heads/feature") and not repo.pushed("refs/heads/main")
    assert not repo.calls.exists()


def test_worktree_requires_managed_publication_without_runtime(tmp_path):
    repo = Repository(tmp_path)
    worktree = tmp_path / "worktree"
    repo.git("worktree", "add", "-b", "worktree/local", str(worktree))
    repo.root = worktree
    repo.env.pop("AGENT_WORKTREES_PR_PUSH")
    refused = repo.git("push", "origin", "HEAD:refs/heads/feature", check=False)
    assert refused.returncode != 0 and not repo.pushed("refs/heads/feature")
    repo.env["AGENT_WORKTREES_PR_PUSH"] = "1"
    repo.git("push", "origin", "HEAD:refs/heads/feature")
    assert repo.pushed("refs/heads/feature")
    assert not repo.calls.exists()


def test_source_validation_is_not_a_transport_gate(tmp_path):
    repo = Repository(tmp_path)
    # Both checks remain enforceable explicitly and in CI, not before transport.
    (repo.root / "large.txt").write_text("x" * (1024 * 1024 + 1), encoding="utf-8")
    repo.git("add", "large.txt")
    repo.git("commit", "-qm", "oversized candidate for CI")
    repo.env["COPILOT_EXTENSIONS_FORBIDDEN_IDS"] = "fixture"
    repo.git("push", "origin", "HEAD:refs/heads/feature")
    assert not repo.calls.exists()

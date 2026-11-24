from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from agent_worktrees import git_longpaths, git_ops

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is required")


@pytest.fixture
def repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "global.gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setattr(git_longpaths, "applies", lambda: True)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return repo


def _local(repo: Path) -> str:
    result = subprocess.run(
        ["git", "config", "--local", "--get", "core.longpaths"], cwd=repo,
        capture_output=True, text=True, check=False,
    )
    return result.stdout.strip()


def test_sets_local_value_when_unset(repository: Path) -> None:
    assert git_longpaths.ensure_repository(repository) == "set"
    assert _local(repository) == "true"
    assert git_longpaths.ensure_repository(repository) == "present"


def test_preserves_explicit_global_false(repository: Path, tmp_path: Path) -> None:
    (tmp_path / "global.gitconfig").write_text("[core]\n\tlongpaths = false\n", encoding="utf-8")
    assert git_longpaths.ensure_repository(repository) == "present"
    assert _local(repository) == ""


def test_does_not_write_into_enclosing_repository(repository: Path) -> None:
    child = repository / "sub"
    child.mkdir()
    assert git_longpaths.ensure_repository(child) == "missing"
    assert _local(repository) == ""


def test_non_windows_is_a_no_op(repository: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(git_longpaths, "applies", lambda: False)
    assert git_longpaths.invocation_args() == []
    assert git_longpaths.ensure_repository(repository) == "not-windows"
    assert _local(repository) == ""


def test_create_worktree_enables_long_paths(repository: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Any, ...]] = []

    def ensure(path: str | Path) -> str:
        calls.append(("ensure", path))
        return "set"

    def fake_git(*args: str, **_kwargs: Any) -> None:
        calls.append(args)

    monkeypatch.setattr(git_longpaths, "ensure_repository", ensure)
    monkeypatch.setattr(git_ops, "git", fake_git)
    git_ops.create_worktree(str(repository), "wt", "branch", "origin/main")
    assert calls[0] == ("ensure", str(repository))
    assert calls[1][:4] == ("-c", "core.longpaths=true", "worktree", "add")
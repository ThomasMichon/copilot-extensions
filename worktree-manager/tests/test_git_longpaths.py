from __future__ import annotations

import shutil
import subprocess

import pytest

from worktree_manager import git_longpaths

GIT = shutil.which("git")


def _global_value(path) -> str:
    result = subprocess.run(
        ["git", "config", "--file", str(path), "--get", "core.longpaths"],
        capture_output=True, text=True, check=False,
    )
    return result.stdout.strip()


@pytest.fixture
def global_config(tmp_path, monkeypatch):
    path = tmp_path / "global.gitconfig"
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(path))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    return path


@pytest.mark.skipif(GIT is None, reason="git is required")
def test_sets_unset_value_once(global_config):
    messages: list[str] = []
    assert git_longpaths.ensure_global(windows=True, report=messages.append) == "set"
    assert _global_value(global_config) == "true"
    assert git_longpaths.ensure_global(windows=True, report=messages.append) == "present"
    assert len(messages) == 1


@pytest.mark.skipif(GIT is None, reason="git is required")
def test_keeps_and_reports_explicit_false(global_config):
    global_config.write_text("[core]\n\tlongpaths = false\n", encoding="utf-8")
    messages: list[str] = []
    assert git_longpaths.ensure_global(windows=True, report=messages.append) == "explicit-false"
    assert _global_value(global_config) == "false"
    assert "left unchanged" in messages[0]


def test_non_windows_and_missing_git_do_nothing(monkeypatch):
    def forbidden(_arguments):
        raise AssertionError("git must not run")

    assert git_longpaths.ensure_global(windows=False, run=forbidden) == "not-windows"
    monkeypatch.setattr(git_longpaths.shutil, "which", lambda _name: None)
    assert git_longpaths.ensure_global(windows=True, run=forbidden) == "no-git"


def test_read_or_write_failure_never_raises():
    def broken(_arguments):
        raise OSError("boom")

    assert git_longpaths.ensure_global(windows=True, git="git", run=broken) == "failed"

    def refuse_write(arguments):
        code = 1 if "--get" in arguments else 255
        return subprocess.CompletedProcess(arguments, code, "", "locked")

    messages: list[str] = []
    result = git_longpaths.ensure_global(windows=True, git="git", run=refuse_write, report=messages.append)
    assert result == "failed" and "locked" in messages[0]

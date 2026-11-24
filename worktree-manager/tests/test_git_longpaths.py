from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from worktree_manager import toolchain_config

GIT = shutil.which("git")


def _global_value(path: Path) -> str:
    result = subprocess.run(
        ["git", "config", "--file", str(path), "--get", "core.longpaths"],
        capture_output=True, text=True, check=False,
    )
    return result.stdout.strip()


@pytest.fixture
def global_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "global.gitconfig"
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(path))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    return path


@pytest.mark.skipif(GIT is None, reason="git is required")
def test_sets_unset_value_once(global_config: Path) -> None:
    messages: list[str] = []
    assert toolchain_config.ensure_git_longpaths(windows=True, report=messages.append) == "set"
    assert _global_value(global_config) == "true"
    assert toolchain_config.ensure_git_longpaths(windows=True, report=messages.append) == "present"
    assert len(messages) == 1


@pytest.mark.skipif(GIT is None, reason="git is required")
def test_keeps_and_reports_explicit_false(global_config: Path) -> None:
    global_config.write_text("[core]\n\tlongpaths = false\n", encoding="utf-8")
    messages: list[str] = []
    assert toolchain_config.ensure_git_longpaths(windows=True, report=messages.append) == "explicit-false"
    assert _global_value(global_config) == "false"
    assert "left unchanged" in messages[0]


def _forbidden(_arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
    raise AssertionError("git must not run")


def test_non_windows_and_missing_git_do_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    assert toolchain_config.ensure_git_longpaths(windows=False, run=_forbidden) == "not-windows"
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    assert toolchain_config.ensure_git_longpaths(windows=True, run=_forbidden) == "no-git"


def test_read_or_write_failure_never_raises() -> None:
    def broken(_arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
        raise OSError("boom")

    assert toolchain_config.ensure_git_longpaths(windows=True, git="git", run=broken) == "failed"

    def refuse_write(arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
        code = 1 if "--get" in arguments else 255
        return subprocess.CompletedProcess(list(arguments), code, "", "locked")

    messages: list[str] = []
    result = toolchain_config.ensure_git_longpaths(
        windows=True, git="git", run=refuse_write, report=messages.append,
    )
    assert result == "failed" and "locked" in messages[0]


def test_suite_install_configuration_applies_long_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def record() -> str:
        calls.append("ensure")
        return "set"

    monkeypatch.setattr(toolchain_config, "ensure_git_longpaths", record)
    monkeypatch.delenv(toolchain_config.CONFIGURATION_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    toolchain_config.configure_selected(root=tmp_path, apply=False)
    assert calls == []
    toolchain_config.configure_selected(root=tmp_path, apply=True)
    assert calls == ["ensure"]
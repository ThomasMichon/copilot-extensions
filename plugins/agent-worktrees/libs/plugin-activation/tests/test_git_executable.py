"""Shared dependency discovery controls through its activation-library consumer."""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from collections.abc import Iterator

import pytest

from plugin_resolve import git


@pytest.fixture(autouse=True)
def clear_cache() -> Iterator[None]:
    git._cache.clear()
    yield
    git._cache.clear()


def executable(tmp_path: Path, name: str = "git.exe") -> str:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    path.chmod(0o755)
    return str(path)


def test_explicit_selection_wins_and_missing_explicit_never_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    chosen = executable(tmp_path)
    monkeypatch.setattr(git, "_which", lambda name, path: chosen if name == chosen else None)
    monkeypatch.setattr(git, "fresh_user_environment", lambda: pytest.fail("unexpected fallback"))
    assert git.resolve_git(chosen, env={"PATH": "old"}) == chosen
    with pytest.raises(FileNotFoundError, match="Explicit"):
        git.resolve_git("missing", env={"PATH": "old"})


def test_positive_cache_reuses_discovery_but_absence_is_not_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    chosen = executable(tmp_path)
    available: list[bool] = []
    calls: list[str] = []

    def which(name: str, path: str) -> str | None:
        calls.append(name)
        return chosen if available else None

    monkeypatch.setattr(git, "_windows", lambda: False)
    monkeypatch.setattr(git, "_which", which)
    with pytest.raises(FileNotFoundError):
        git.resolve_git(env={"PATH": "portable"})
    available.append(True)
    assert git.resolve_git(env={"PATH": "portable"}) == chosen
    assert git.resolve_git(env={"PATH": "portable"}) == chosen
    assert len(calls) == 2


def test_confirmed_cached_disappearance_rediscovers_before_next_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = executable(tmp_path, "old/git.exe")
    second = executable(tmp_path, "new/git.exe")
    choices = iter((first, second))
    monkeypatch.setattr(git, "_which", lambda *args: next(choices))
    assert git.resolve_git(env={}) == first
    Path(first).unlink()
    assert git.resolve_git(env={}) == second


def test_discovered_child_hint_is_not_a_permanent_explicit_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = executable(tmp_path, "portable/git.exe")
    second = executable(tmp_path, "replacement/git.exe")
    monkeypatch.setattr(git, "_which", lambda *args: second)
    env = {"AGENT_GIT_DISCOVERED_EXECUTABLE": first}
    assert git.resolve_git(env=env) == first
    Path(first).unlink()
    assert git.resolve_git(env=env) == second


def test_absolute_selection_survives_missing_caller_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    chosen = executable(tmp_path)
    monkeypatch.setattr(os, "getcwd", lambda: (_ for _ in ()).throw(FileNotFoundError()))
    assert git.resolve_git(chosen, env={"PATH": ""}) == chosen


def test_unset_path_uses_default_but_empty_path_searches_caller_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    chosen = executable(tmp_path)
    paths: list[str] = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(git, "_windows", lambda: False)
    default_paths = [Path(entry).absolute() for entry in os.defpath.split(os.pathsep)]

    def which(name: str, path: str) -> str | None:
        paths.append(path)
        return chosen if [Path(entry) for entry in path.split(os.pathsep)] == default_paths else None

    monkeypatch.setattr(git, "_which", which)
    assert git.resolve_git(env={}) == chosen
    with pytest.raises(FileNotFoundError):
        git.resolve_git(env={"PATH": ""})
    assert [Path(entry) for entry in paths[0].split(os.pathsep)] == default_paths
    assert paths[1:] == [str(tmp_path)]


@pytest.mark.parametrize("source", ["fresh-path", "registry"])
def test_stale_windows_path_uses_fresh_os_or_nondefault_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source: str,
) -> None:
    chosen = executable(tmp_path, "nondefault/git.exe")
    calls: list[str] = []
    monkeypatch.setattr(git, "_windows", lambda: True)
    monkeypatch.setattr(git, "fresh_user_environment", lambda: {"Path": "fresh"})
    monkeypatch.setattr(git, "_windows_registry_candidates", lambda: iter([chosen]))

    def which(name: str, path: str) -> str | None:
        calls.append(path)
        return chosen if source == "fresh-path" and path == "fresh" else None

    monkeypatch.setattr(git, "_which", which)
    assert git.resolve_git(env={"Path": "stale"}) == chosen
    assert calls == ["stale", "fresh"]


def test_cached_permission_failure_is_not_misclassified_as_disappearance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    chosen = executable(tmp_path)
    monkeypatch.setattr(git, "_which", lambda *args: chosen)
    git.resolve_git(env={})
    monkeypatch.setattr(os, "stat", lambda *_args, **_kw: (_ for _ in ()).throw(PermissionError()))
    with pytest.raises(PermissionError):
        git.resolve_git(env={})


def test_registry_reads_installation_metadata_at_nondefault_location(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "portable-location"
    monkeypatch.setattr(sys, "platform", "win32")
    reads: list[tuple[int, str]] = []

    class Key:
        def __enter__(self) -> "Key":
            return self

        def __exit__(self, *args: object) -> None:
            pass

    def open_key(hive: int, name: str, reserved: int, flags: int) -> Key:
        reads.append((hive, name))
        if hive == 1 and name == r"SOFTWARE\GitForWindows":
            return Key()
        raise FileNotFoundError()

    monkeypatch.setitem(sys.modules, "winreg", SimpleNamespace(
        HKEY_CURRENT_USER=1, HKEY_LOCAL_MACHINE=2,
        KEY_WOW64_64KEY=256, KEY_WOW64_32KEY=512, KEY_READ=1,
        OpenKey=open_key, QueryValueEx=lambda *args: (str(root), 1),
    ))
    candidates = list(git._windows_registry_candidates())
    assert str(root / "cmd" / "git.exe") in candidates
    assert any(hive == 2 for hive, _ in reads)


def test_real_child_uses_absolute_git_when_child_path_is_empty(tmp_path: Path) -> None:
    selected = shutil.which("git")
    if selected is None:
        pytest.skip("Git consumer prerequisite unavailable")
    resolved = git.resolve_git(selected, env={"PATH": ""})
    env = {**os.environ, "PATH": ""}
    result = subprocess.run(
        [resolved, "--version"], env=env, cwd=tmp_path,
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    assert result.stdout.startswith("git version ")
    with pytest.raises((FileNotFoundError, NotADirectoryError)):
        subprocess.run(
            [resolved, "--version"], cwd=tmp_path / "missing", env=env, timeout=10,
        )
    assert git.resolve_git(selected, env={"PATH": ""}) == resolved

"""Checkout-local test venvs during whole-checkout disposal.

Recognized, untracked and Git-ignored venv roots are removed first, while the
checkout's Git root is intact. Anything else in a test root is preserved and
holds the checkout.
"""

from __future__ import annotations

import os
import stat
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_worktrees import cleanup_reap, git_ops, procs, tracking, venv_reclaim


def _venv(path: Path) -> Path:
    (path / "Lib" / "site-packages").mkdir(parents=True)
    (path / "Scripts").mkdir()
    (path / "pyvenv.cfg").write_text("home = x\n", encoding="utf-8")
    (path / "Lib" / "site-packages" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    return path


def _repo(path: Path, ignore: str = ".test-venvs/\n.test-runs/\n") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    (path / ".gitignore").write_text(ignore, encoding="utf-8")
    return path


def _commit(repo: Path, *paths: str) -> None:
    subprocess.run(["git", "-C", str(repo), "add", "-f", *paths], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "commit", "-q", "-m", "fixture"],
        check=True,
    )


def test_recognition_accepts_venvs_groups_and_runner_metadata(tmp_path: Path) -> None:
    root = tmp_path / ".test-venvs"
    first = _venv(root / "plugin-a")
    second = _venv(root / "windows" / "plugin-b")
    (root / ".gitignore").write_text("*\n", encoding="utf-8")
    assert sorted(venv_reclaim.disposable_venvs(root) or []) == sorted([first, second])
    assert venv_reclaim.recognized_roots(tmp_path) == [root]
    assert venv_reclaim.data_blocker(tmp_path) is None


@pytest.mark.parametrize("intruder", [
    "loose.xml", "reports", "not-a-venv/data.txt", "env/logs",
    "env/capture.json", "env/Lib/reports/session.json", "env/notes",
])
def test_recognition_refuses_anything_that_is_not_a_venv(tmp_path: Path, intruder: str) -> None:
    root = tmp_path / ".test-venvs"
    _venv(root / "env")
    target = root / intruder
    if target.suffix:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("evidence", encoding="utf-8")
    else:
        target.mkdir(parents=True, exist_ok=True)
    assert venv_reclaim.disposable_venvs(root) is None
    assert venv_reclaim.recognized_roots(tmp_path) == []
    assert venv_reclaim.unrecognized_roots(tmp_path) == [root]


def test_preserved_test_run_content_is_a_data_blocker(tmp_path: Path) -> None:
    result = tmp_path / ".test-runs" / "reports" / "result.json"
    result.parent.mkdir(parents=True)
    result.write_text("{}", encoding="utf-8")
    assert "preserved test-root content" in (venv_reclaim.data_blocker(tmp_path) or "")


def test_remove_tree_clears_read_only_files_and_keeps_the_marker_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _venv(tmp_path / ".test-venvs" / "env")
    locked = env / "Lib" / "site-packages" / "mod.py"
    os.chmod(locked, stat.S_IREAD)
    venv_reclaim.remove_tree(env)
    assert not env.exists()

    env = _venv(tmp_path / ".test-venvs" / "env")
    real_remove = venv_reclaim._remove_file

    def locked_module(path: str) -> None:
        if path.endswith("mod.py"):
            raise PermissionError(path)
        real_remove(path)

    monkeypatch.setattr(venv_reclaim, "_remove_file", locked_module)
    with pytest.raises(OSError):
        venv_reclaim.remove_tree(env)
    # pyvenv.cfg goes last, so a partly removed venv stays recognized.
    assert (env / "pyvenv.cfg").exists()
    assert venv_reclaim.recognized_roots(tmp_path) == [tmp_path / ".test-venvs"]


def test_untracked_and_ignored_requires_both(tmp_path: Path) -> None:
    ignored = _repo(tmp_path / "ignored")
    _venv(ignored / ".test-venvs" / "env")
    assert venv_reclaim.untracked_and_ignored(ignored, ".test-venvs")

    tracked = _repo(tmp_path / "tracked")
    _venv(tracked / ".test-venvs" / "env")
    _commit(tracked, ".test-venvs/env/pyvenv.cfg")
    assert not venv_reclaim.untracked_and_ignored(tracked, ".test-venvs")

    unignored = _repo(tmp_path / "unignored", ignore="")
    _venv(unignored / ".test-venvs" / "env")
    assert not venv_reclaim.untracked_and_ignored(unignored, ".test-venvs")

    plain = tmp_path / "plain"
    _venv(plain / ".test-venvs" / "env")
    assert not venv_reclaim.untracked_and_ignored(plain, ".test-venvs")


def test_predelete_removes_only_recognized_ignored_untracked_roots(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    _venv(repo / ".test-venvs" / "env")
    fixture = _venv(repo / ".test-runs" / "env")
    _commit(repo, ".test-runs/env/pyvenv.cfg")
    assert venv_reclaim.predelete_blocker(repo) is None
    assert not (repo / ".test-venvs").exists()
    assert (fixture / "pyvenv.cfg").exists() and (fixture / "Lib" / "site-packages" / "mod.py").exists()


def test_predelete_reports_a_root_it_cannot_remove(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path / "repo")
    env = _venv(repo / ".test-venvs" / "env")

    def locked(path: str | Path) -> None:
        raise PermissionError(str(path))

    monkeypatch.setattr(venv_reclaim, "remove_tree", locked)
    blocker = venv_reclaim.predelete_blocker(repo)
    assert blocker and "could not be removed" in blocker and "Git root intact" in blocker
    assert (env / "pyvenv.cfg").exists()


def _reap(record: tracking.WorktreeRecord, tmp_path: Path) -> tuple[int, list[str]]:
    info: Any = SimpleNamespace(state=SimpleNamespace(value="completed"))
    repo: Any = SimpleNamespace(anchor=str(tmp_path / "anchor"), default_branch="main")
    return cleanup_reap._reap_locked(record, info, repo, tmp_path)


@pytest.fixture
def reapable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sample_record: tracking.WorktreeRecord) -> tracking.WorktreeRecord:
    checkout = _repo(tmp_path / "checkout")
    monkeypatch.setattr(cleanup_reap, "retirement_blocker", lambda record: None)
    monkeypatch.setattr(cleanup_reap, "preservation_reason", lambda path: None)
    monkeypatch.setattr(procs, "checkout_retirement_blocker", lambda path: None)
    monkeypatch.setattr(git_ops, "remove_worktree", lambda *a, **k: pytest.fail("checkout must stay"))
    return replace(sample_record, worktree_path=str(checkout))


def test_cleanup_keeps_checkout_with_preserved_test_run_content(
    reapable: tracking.WorktreeRecord, tmp_path: Path,
) -> None:
    result = Path(reapable.worktree_path) / ".test-runs" / "reports" / "result.json"
    result.parent.mkdir(parents=True)
    result.write_text("{}", encoding="utf-8")
    failures, warnings = _reap(reapable, tmp_path)
    assert failures == 1 and "preserved test-root content" in warnings[0]
    assert result.exists()


def test_cleanup_defers_with_git_root_when_a_venv_cannot_be_removed(
    reapable: tracking.WorktreeRecord, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = Path(reapable.worktree_path)
    env = _venv(checkout / ".test-venvs" / "env")

    def locked(path: str | Path) -> None:
        raise PermissionError(str(path))

    monkeypatch.setattr(venv_reclaim, "remove_tree", locked)
    failures, warnings = _reap(reapable, tmp_path)
    assert failures == 1 and "could not be removed" in warnings[0]
    assert (checkout / ".git").exists() and (env / "pyvenv.cfg").exists()


def test_cleanup_removes_test_venvs_before_the_git_worktree(
    reapable: tracking.WorktreeRecord, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = Path(reapable.worktree_path)
    _venv(checkout / ".test-venvs" / "env")
    seen: list[bool] = []

    class Removed(Exception):
        pass

    def remove(anchor: str, path: str, *args: Any, **kwargs: Any) -> bool:
        seen.append((Path(path) / ".test-venvs").exists())
        raise Removed

    monkeypatch.setattr(git_ops, "remove_worktree", remove)
    with pytest.raises(Removed):
        _reap(reapable, tmp_path)
    assert seen == [False] and (checkout / ".git").exists()

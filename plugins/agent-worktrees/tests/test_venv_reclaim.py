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


@pytest.mark.parametrize("selector", ["GIT_INDEX_FILE", "GIT_DIR"])
def test_untracked_and_ignored_ignores_inherited_git_selectors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selector: str,
) -> None:
    tracked = _repo(tmp_path / "tracked")
    _venv(tracked / ".test-venvs" / "env")
    _commit(tracked, ".test-venvs/env/pyvenv.cfg")
    other = _repo(tmp_path / "other")
    monkeypatch.setenv(selector, str(tmp_path / "empty.index") if selector == "GIT_INDEX_FILE" else str(other / ".git"))
    # The inherited selector hides the tracked venv from an unisolated probe.
    hidden = subprocess.run(["git", "ls-files", "--", ".test-venvs/"], cwd=tracked, capture_output=True, text=True)
    assert hidden.stdout == ""
    assert not venv_reclaim.untracked_and_ignored(tracked, ".test-venvs")


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


def test_predelete_blocks_content_that_appeared_after_the_data_gate(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    env = _venv(repo / ".test-venvs" / "env")
    assert venv_reclaim.data_blocker(repo) is None
    late = repo / ".test-runs" / "reports" / "result.json"
    late.parent.mkdir(parents=True)
    late.write_text("{}", encoding="utf-8")
    blocker = venv_reclaim.predelete_blocker(repo)
    assert blocker and "preserved test-root content" in blocker
    # Nothing is removed, not even the root that is still recognized.
    assert late.exists() and (env / "Lib" / "site-packages" / "mod.py").exists()


@pytest.mark.parametrize("bound", [("MAX_INSPECTED_ENTRIES", 3), ("MAX_INSPECTION_SECS", -1.0)])
def test_inspection_over_its_bound_defers_and_removes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bound: tuple[str, float],
) -> None:
    repo = _repo(tmp_path / "repo")
    env = _venv(repo / ".test-venvs" / "env")
    monkeypatch.setattr(venv_reclaim, *bound)
    assert "exceeded its bound" in (venv_reclaim.data_blocker(repo) or "")
    assert "exceeded its bound" in (venv_reclaim.predelete_blocker(repo) or "")
    assert (env / "Lib" / "site-packages" / "mod.py").exists()


def test_wide_directory_stops_enumerating_at_the_entry_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path / "repo")
    wide = _venv(repo / ".test-venvs" / "env") / "Lib" / "site-packages"
    for index in range(200):
        (wide / f"m{index}.py").write_text("", encoding="utf-8")
    real_scandir, pulled = os.scandir, [0]

    class Counting:
        def __init__(self, path: str) -> None:
            self._entries = real_scandir(path)
            self._wide = os.path.normcase(path).endswith(os.path.normcase(str(wide)))

        def __enter__(self) -> Counting:
            return self

        def __exit__(self, *exc: object) -> None:
            self._entries.close()

        def __iter__(self) -> Counting:
            return self

        def __next__(self) -> os.DirEntry[str]:
            entry = next(self._entries)
            pulled[0] += self._wide
            return entry

    monkeypatch.setattr(venv_reclaim, "MAX_INSPECTED_ENTRIES", 30)
    monkeypatch.setattr(os, "scandir", Counting)
    assert "exceeded its bound" in (venv_reclaim.data_blocker(repo) or "")
    assert 0 < pulled[0] < 200
    assert "exceeded its bound" in (venv_reclaim.predelete_blocker(repo) or "")
    assert (wide / "mod.py").exists()


def test_delayed_final_metadata_lookup_defers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path / "repo")
    env = _venv(repo / ".test-venvs" / "env")
    (repo / ".test-venvs" / "scratch-owner.json").write_text("{}", encoding="utf-8")
    clock, real_lstat = [0.0], venv_reclaim._lstat

    def slow_final_lookup(path: str | Path) -> os.stat_result | None:
        info = real_lstat(path)
        if Path(path).name == "scratch-owner.json":  # sorts after env: the walk's last lookup
            clock[0] += 1000.0
        return info

    monkeypatch.setattr(venv_reclaim, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(venv_reclaim, "_lstat", slow_final_lookup)
    assert "exceeded its bound" in (venv_reclaim.data_blocker(repo) or "")
    assert "exceeded its bound" in (venv_reclaim.predelete_blocker(repo) or "")
    assert (env / "pyvenv.cfg").exists()


def test_slow_git_probe_defers_before_removal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path / "repo")
    env = _venv(repo / ".test-venvs" / "env")
    clock, real_probe = [0.0], venv_reclaim.untracked_and_ignored

    def slow_probe(checkout: str | Path, name: str) -> bool:
        clock[0] += 1000.0
        return real_probe(checkout, name)

    monkeypatch.setattr(venv_reclaim, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(venv_reclaim, "untracked_and_ignored", slow_probe)
    assert "exceeded its bound" in (venv_reclaim.predelete_blocker(repo) or "")
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


def test_cleanup_process_blocker_skips_the_test_root_walk(
    reapable: tracking.WorktreeRecord, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _venv(Path(reapable.worktree_path) / ".test-venvs" / "env")
    monkeypatch.setattr(procs, "checkout_retirement_blocker", lambda path: "pid 7 (python.exe) uses it")
    monkeypatch.setattr(venv_reclaim, "_scan", lambda *a: pytest.fail("walk must not run"))
    failures, warnings = _reap(reapable, tmp_path)
    assert failures == 1 and "pid 7" in warnings[0]


def test_cleanup_stops_before_git_remove_when_content_appears_late(
    reapable: tracking.WorktreeRecord, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = Path(reapable.worktree_path)
    env = _venv(checkout / ".test-venvs" / "env")
    late = checkout / ".test-venvs" / "capture.json"
    real_gate = venv_reclaim.data_blocker

    def gate_then_write(path: str | Path) -> str | None:
        result = real_gate(path)
        late.write_text("{}", encoding="utf-8")
        return result

    monkeypatch.setattr(venv_reclaim, "data_blocker", gate_then_write)
    failures, warnings = _reap(reapable, tmp_path)
    assert failures == 1 and "preserved test-root content" in warnings[0]
    assert late.exists() and (env / "pyvenv.cfg").exists() and (checkout / ".git").exists()


def test_cleanup_defers_when_the_test_root_walk_exceeds_its_bound(
    reapable: tracking.WorktreeRecord, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _venv(Path(reapable.worktree_path) / ".test-venvs" / "env")
    monkeypatch.setattr(venv_reclaim, "MAX_INSPECTED_ENTRIES", 3)
    failures, warnings = _reap(reapable, tmp_path)
    assert failures == 1 and "exceeded its bound" in warnings[0]
    assert (env / "Lib" / "site-packages" / "mod.py").exists()

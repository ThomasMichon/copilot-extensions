"""Legacy per-checkout test-directory reclamation stays fail-closed."""
from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from tools import reclaim_legacy_test_dirs as reclaim


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    (root / ".gitignore").write_text("/.test-venvs/\n.test-runs/\n", encoding="utf-8")
    _git(root, "add", ".gitignore")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@example.invalid",
         "commit", "-q", "-m", "init")
    return root


def _legacy(root, name=".test-venvs", *, age_hours=48.0):
    tree = root / name / "linux" / "plugin"
    tree.mkdir(parents=True)
    payload = tree / "python"
    payload.write_bytes(b"x" * 1024)
    stamp = time.time() - age_hours * 3600
    for path in (payload, tree, tree.parent, root / name):
        os.utime(path, (stamp, stamp))
    return root / name


def _run(root, *args, capsys):
    code = reclaim.main(["--repo", str(root), "--json", *args])
    import json
    return code, json.loads(capsys.readouterr().out)


def test_dry_run_reports_without_deleting(repo, capsys):
    target = _legacy(repo)
    code, report = _run(repo, capsys=capsys)
    assert code == 0
    [candidate] = report["candidates"]
    assert candidate["status"] == "eligible" and candidate["bytes"] == 1024
    assert target.is_dir()


def test_apply_removes_eligible_tree(repo, capsys):
    target = _legacy(repo, ".test-runs")
    code, report = _run(repo, "--apply", capsys=capsys)
    assert code == 0
    assert report["candidates"][0]["status"] == "reclaimed"
    assert not os.path.lexists(target)
    assert not list(repo.glob(".test-runs.reclaiming-*"))


def test_recent_tree_is_refused(repo, capsys):
    target = _legacy(repo, age_hours=1)
    _code, report = _run(repo, "--apply", capsys=capsys)
    assert report["candidates"][0]["reason"] == "recently modified"
    assert target.is_dir()


def test_tracked_content_is_refused(repo, capsys):
    target = _legacy(repo)
    _git(repo, "add", "-f", ".test-venvs/linux/plugin/python")
    _code, report = _run(repo, "--apply", capsys=capsys)
    assert report["candidates"][0]["reason"] == "contains tracked files"
    assert target.is_dir()


def test_unignored_directory_is_refused(repo, capsys):
    (repo / ".gitignore").write_text("", encoding="utf-8")
    target = _legacy(repo)
    _code, report = _run(repo, "--apply", capsys=capsys)
    assert report["candidates"][0]["reason"] == "not ignored by Git"
    assert target.is_dir()


def test_inner_link_is_refused_and_never_followed(repo, tmp_path, capsys, native_file_symlink):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    target = _legacy(repo)
    native_file_symlink(target / "escape", outside, target_is_directory=True)
    stamp = time.time() - 48 * 3600
    os.utime(target, (stamp, stamp))
    _code, report = _run(repo, "--apply", capsys=capsys)
    assert report["candidates"][0]["reason"] == "contains a link or reparse point"
    assert (outside / "keep.txt").is_file() and target.is_dir()


def test_root_link_is_refused(repo, tmp_path, capsys, native_file_symlink):
    outside = tmp_path / "outside"
    outside.mkdir()
    native_file_symlink(repo / ".test-venvs", outside, target_is_directory=True)
    _code, report = _run(repo, "--apply", capsys=capsys)
    assert report["candidates"][0]["reason"] == "not a plain directory"
    assert outside.is_dir()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows rename probe")
def test_open_handle_blocks_reclaim_on_windows(repo, capsys):
    target = _legacy(repo)
    with open(target / "linux" / "plugin" / "python", "rb"):
        _code, report = _run(repo, "--apply", capsys=capsys)
    assert report["candidates"][0]["reason"] == "in use by a live process"
    assert target.is_dir()


def test_live_process_blocks_reclaim_on_posix(repo, capsys, monkeypatch):
    if os.name == "nt":
        pytest.skip("POSIX liveness scan")
    target = _legacy(repo)
    monkeypatch.setattr(reclaim, "_linux_in_use", lambda _path: True)
    _code, report = _run(repo, "--apply", capsys=capsys)
    assert report["candidates"][0]["reason"] == "in use by a live process"
    assert target.is_dir()


def test_unverifiable_liveness_refuses_on_posix(repo, capsys, monkeypatch):
    if os.name == "nt":
        pytest.skip("POSIX liveness scan")
    target = _legacy(repo)
    monkeypatch.setattr(reclaim, "_linux_in_use", lambda _path: None)
    _code, report = _run(repo, "--apply", capsys=capsys)
    assert report["candidates"][0]["reason"] == "process use cannot be verified"
    assert target.is_dir()


def test_sibling_worktrees_are_discovered(repo, tmp_path, capsys):
    sibling = tmp_path / "sibling"
    _git(repo, "worktree", "add", "-q", str(sibling))
    target = _legacy(sibling)
    _code, report = _run(repo, capsys=capsys)
    assert [c["path"] for c in report["candidates"]] == [str(target)]

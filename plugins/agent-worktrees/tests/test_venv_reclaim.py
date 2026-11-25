"""Checkout-local test venvs are rebuildable and must not block retirement."""

from __future__ import annotations

import argparse
import json
import os
import stat
import time
from pathlib import Path

import pytest

from agent_worktrees import cleanup_gc_cli, output, venv_reclaim


def _venv(path: Path) -> Path:
    (path / "Lib" / "site-packages").mkdir(parents=True)
    (path / "pyvenv.cfg").write_text("home = x\n", encoding="utf-8")
    (path / "Lib" / "site-packages" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    return path


@pytest.fixture
def idle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(venv_reclaim, "_holders", lambda root: ([], None))


def test_recognition_accepts_venvs_groups_and_runner_metadata(tmp_path: Path) -> None:
    root = tmp_path / ".test-venvs"
    first = _venv(root / "plugin-a")
    second = _venv(root / "windows" / "plugin-b")
    (root / ".gitignore").write_text("*\n", encoding="utf-8")
    assert sorted(venv_reclaim.disposable_venvs(root) or []) == sorted([first, second])
    assert venv_reclaim.recognized_roots(tmp_path) == [root]


@pytest.mark.parametrize("intruder", ["loose.xml", "reports", "not-a-venv/data.txt", "env/logs"])
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


def test_only_disposable_content_requires_every_entry_to_be_rebuildable(tmp_path: Path) -> None:
    _venv(tmp_path / ".test-venvs" / "env")
    (tmp_path / "src" / "__pycache__").mkdir(parents=True)
    assert venv_reclaim.only_disposable_content(tmp_path)
    (tmp_path / "src" / "kept.py").write_text("", encoding="utf-8")
    assert not venv_reclaim.only_disposable_content(tmp_path)


def test_clean_removes_idle_venvs_and_root_but_preserves_unrecognized(tmp_path: Path, idle: None) -> None:
    _venv(tmp_path / ".test-venvs" / "windows" / "env")
    runs = tmp_path / ".test-runs"
    _venv(runs / "env")
    (runs / "summary.json").write_text("{}", encoding="utf-8")
    report = venv_reclaim.clean_test_venvs(tmp_path)
    assert report["removed"] == 1 and report["remaining"] == 0
    assert not (tmp_path / ".test-venvs").exists()
    assert (runs / "env" / "pyvenv.cfg").exists()
    assert {r["path"]: r["status"] for r in report["roots"]}[str(runs)] == "preserved"


def test_dry_run_removes_nothing(tmp_path: Path, idle: None) -> None:
    env = _venv(tmp_path / ".test-venvs" / "env")
    report = venv_reclaim.clean_test_venvs(tmp_path, dry_run=True)
    assert [v["status"] for v in report["venvs"]] == ["would_remove"]
    assert env.exists()


def test_in_use_and_recent_venvs_are_left_and_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    busy = _venv(tmp_path / ".test-venvs" / "busy")
    fresh = _venv(tmp_path / ".test-venvs" / "fresh")
    old = time.time() - 3600
    for path in (busy, busy / "pyvenv.cfg"):
        os.utime(path, (old, old))
    holder = {"pid": 42, "name": "python", "executable": str(busy / "Scripts" / "python.exe")}
    monkeypatch.setattr(venv_reclaim, "_holders", lambda root: ([holder], None))
    report = venv_reclaim.clean_test_venvs(tmp_path, min_age_secs=60)
    statuses = {Path(v["path"]).name: v["status"] for v in report["venvs"]}
    assert statuses == {"busy": "in_use", "fresh": "recent"}
    assert busy.exists() and fresh.exists()
    assert "python(42)" in (venv_reclaim.blocker(report) or "")


def test_process_working_inside_root_blocks_the_whole_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = _venv(tmp_path / ".test-venvs" / "env")
    monkeypatch.setattr(venv_reclaim, "_holders", lambda root: ([], "live process(es) work inside root: pytest(7)"))
    report = venv_reclaim.clean_test_venvs(tmp_path)
    assert env.exists()
    assert "pytest(7)" in (venv_reclaim.blocker(report) or "")


def test_remove_tree_clears_read_only_files(tmp_path: Path) -> None:
    env = _venv(tmp_path / "env")
    locked = env / "Lib" / "site-packages" / "mod.py"
    os.chmod(locked, stat.S_IREAD)
    assert venv_reclaim.remove_tree(env)
    assert not env.exists()


def test_clean_test_venvs_command_reports_json(
    tmp_path: Path, idle: None,
) -> None:
    _venv(tmp_path / ".test-venvs" / "env")
    args = argparse.Namespace(worktree_dir=str(tmp_path), dry_run=False, json=True, min_age_secs=0.0)
    with output.capture_json_output() as buf:
        assert cleanup_gc_cli.cmd_clean_test_venvs(args) == 0
    payload = json.loads(buf.getvalue())
    data = payload.get("data", payload)
    assert data["removed"] == 1
    assert not (tmp_path / ".test-venvs").exists()

"""Observe the actual captured probes from a fresh consoleless Windows parent."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.skipif(os.name != "nt", reason="Native Windows desktop observation")
def test_guard_and_controller_have_no_visible_windows_or_focus_changes(tmp_path: Path) -> None:
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    assert pythonw.exists(), "Windows validation requires the managed GUI interpreter"
    repository = tmp_path / "repository"
    repository.mkdir()
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True, creationflags=flags)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/example/project.git"],
        cwd=repository, check=True, creationflags=flags,
    )
    report = tmp_path / "observation.json"
    observer = ROOT / "plugins" / "agent-worktrees" / "tests" / "native_pr_rebase_headless.py"
    result = subprocess.run(
        [str(pythonw), "-I", str(observer), "--steward-probes", str(ROOT),
         str(repository), str(report)],
        capture_output=True, text=True, check=False, timeout=20, creationflags=flags,
    )
    assert report.exists(), (result.returncode, result.stdout, result.stderr)
    observation = json.loads(report.read_text(encoding="utf-8"))
    assert result.returncode == 0, observation
    assert observation["cycles"] == 2
    assert observation["observed_descendants"] >= 2
    assert observation["visible_windows"] == observation["new_visible_windows"] == 0
    assert observation["foreground_owned"] == observation["foreground_transitions"] == 0
    assert observation["errors"] == []

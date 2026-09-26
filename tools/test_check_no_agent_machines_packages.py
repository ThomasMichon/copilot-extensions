"""Regression tests for the "no agent-machines consuming-side package in this
repo" structural guard.

``find_violations`` accepts an explicit repo root, so most cases are plain
unit tests against a throwaway directory tree (no subprocess, no git). One
end-to-end smoke test drives the real script as a subprocess against the
actual repo checkout, confirming it stays clean (mirrors
test_check_module_size.py's pattern for the one live-repo assertion).

Run:  python -m pytest tools/test_check_no_agent_machines_packages.py
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "check-no-agent-machines-packages.py"
REPO_ROOT = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location("check_no_agent_machines_packages", SCRIPT)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def test_clean_tree_has_no_violations(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "resources.md").write_text("# docs\n", encoding="utf-8")
    assert guard.find_violations(tmp_path) == []


@pytest.mark.parametrize(
    "rel_root",
    [
        ".copilot-extensions/agent-machines/all",
        ".copilot-extensions/agent-machines/machines/some-machine",
        ".agent-machines",
        ".github/machine-state",
    ],
)
def test_flags_each_forbidden_root(tmp_path, rel_root):
    pkg_dir = tmp_path / rel_root
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "example.yaml").write_text("schema_version: 3\n", encoding="utf-8")
    violations = guard.find_violations(tmp_path)
    assert len(violations) == 1
    assert violations[0] == pkg_dir / "example.yaml"


def test_multiple_files_across_roots_all_reported(tmp_path):
    for rel_root in (".copilot-extensions/agent-machines/all", ".agent-machines"):
        pkg_dir = tmp_path / rel_root
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "pkg.yaml").write_text("schema_version: 3\n", encoding="utf-8")
    assert len(guard.find_violations(tmp_path)) == 2


def test_empty_forbidden_directory_reports_nothing(tmp_path):
    # An empty (or docs-only, e.g. a README) directory at a forbidden root
    # carries no actual package -- only files count as a violation.
    (tmp_path / ".copilot-extensions" / "agent-machines").mkdir(parents=True)
    assert guard.find_violations(tmp_path) == []


def test_real_repo_checkout_is_clean():
    """End-to-end smoke test: the real, live repo tree must never trip this
    guard -- confirms the script (not just find_violations()) exits 0
    against the actual checkout, the same live-repo assertion
    test_check_module_size.py makes for its own guard."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr

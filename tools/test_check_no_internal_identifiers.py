"""Regression tests for the internal-identifier pre-push guard (#541).

The guard used to scan the *entire* tracked tree, so a pre-existing identifier
in an untouched file blocked every unrelated push. It now scans only the push
diff (``<base>...HEAD``) by default, while ``--all`` still audits the whole
tree. These tests drive the real script as a subprocess inside a throwaway git
repo so the git-diff scoping is exercised end-to-end.

Run:  python -m pytest tools/test_check_no_internal_identifiers.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "check-no-internal-identifiers.py"
LEAK = "acme-internal-id"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _write(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A git repo with a simulated ``origin/main`` base carrying a pre-existing
    leak in an untouched file, and a HEAD that changes only a clean file."""
    r = tmp_path / "repo"
    (r / "tools").mkdir(parents=True)
    shutil.copy(SCRIPT, r / "tools" / SCRIPT.name)

    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "Test")
    _git(r, "checkout", "-q", "-b", "main")

    # Base commit: a pre-existing leak in an untouched file.
    _write(r, "plugins/old/legacy.txt", f"this file mentions {LEAK} already\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=r, capture_output=True, text=True, check=True
    ).stdout.strip()
    # Simulate the remote the guard diffs against.
    _git(r, "update-ref", "refs/remotes/origin/main", base_sha)

    # New commit: touch only a clean file.
    _write(r, "plugins/new/clean.txt", "nothing sensitive here\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "clean change")
    return r


def _run(repo: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(repo / "tools" / SCRIPT.name), *extra],
        cwd=repo,
        capture_output=True,
        text=True,
        env={**_base_env(), "COPILOT_EXTENSIONS_FORBIDDEN_IDS": LEAK},
    )


def _base_env() -> dict[str, str]:
    import os

    # Keep PATH/SYSTEMROOT so git + python resolve on every platform.
    keep = ("PATH", "SYSTEMROOT", "SystemRoot", "HOME", "USERPROFILE", "TEMP", "TMP")
    return {k: v for k, v in os.environ.items() if k in keep}


def test_diff_scope_ignores_pre_existing_leak_in_untouched_file(repo: Path):
    # The bug fix: default (push-diff) scope must NOT flag the pre-existing leak
    # in plugins/old/legacy.txt because this push doesn't touch it.
    result = _run(repo)
    assert result.returncode == 0, result.stdout + result.stderr


def test_all_flag_still_audits_whole_tree(repo: Path):
    # --all restores the full-tree sweep and catches the pre-existing leak.
    result = _run(repo, "--all")
    assert result.returncode == 1
    assert LEAK in result.stdout


def test_diff_scope_still_catches_introduced_leak(repo: Path):
    # A leak in a file the push actually changes is still caught in diff scope.
    _write(repo, "plugins/new/clean.txt", f"oops {LEAK} sneaked in\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "introduce leak")
    result = _run(repo)
    assert result.returncode == 1
    assert LEAK in result.stdout

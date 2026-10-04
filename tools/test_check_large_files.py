"""Regression tests for the large-file guard (image/default caps + the
always-blocked source-map/diff/patch denylist).

Drives the real ``tools/check-large-files.py`` as a subprocess inside a
throwaway git repo, the same pattern ``test_check_module_size.py`` uses.

Run:  python -m pytest tools/test_check_large_files.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "check-large-files.py"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _write_bytes(repo: Path, rel: str, size: int) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x" * size)


def _run(repo: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(repo / "tools" / SCRIPT.name), *extra],
        cwd=repo,
        capture_output=True,
        text=True,
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / SCRIPT.name).write_bytes(SCRIPT.read_bytes())
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    return tmp_path


def _commit_all(repo: Path) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "snapshot")


def test_a_small_file_passes(repo: Path):
    _write_bytes(repo, "src/small.txt", 1024)
    _commit_all(repo)

    result = _run(repo, "--all")

    assert result.returncode == 0, result.stdout + result.stderr


def test_an_oversized_non_image_file_fails(repo: Path):
    _write_bytes(repo, "src/big.json", 2 * 1024 * 1024)
    _commit_all(repo)

    result = _run(repo, "--all")

    assert result.returncode == 1
    assert "big.json" in result.stdout
    assert "non-image" in result.stdout


def test_an_image_under_the_image_cap_passes(repo: Path):
    _write_bytes(repo, "docs/assets/screenshot.png", 2 * 1024 * 1024)
    _commit_all(repo)

    result = _run(repo, "--all")

    assert result.returncode == 0, result.stdout + result.stderr


def test_an_image_over_the_image_cap_fails(repo: Path):
    _write_bytes(repo, "docs/assets/screenshot.png", 4 * 1024 * 1024)
    _commit_all(repo)

    result = _run(repo, "--all")

    assert result.returncode == 1
    assert "screenshot.png" in result.stdout
    assert "image" in result.stdout


@pytest.mark.parametrize("ext", [".map", ".diff", ".patch"])
def test_always_blocked_extensions_fail_regardless_of_size(repo: Path, ext: str):
    _write_bytes(repo, f"dist/bundle{ext}", 10)
    _commit_all(repo)

    result = _run(repo, "--all")

    assert result.returncode == 1
    assert f"bundle{ext}" in result.stdout


def test_diff_scoped_mode_ignores_pre_existing_oversized_files(repo: Path):
    # A file already over cap on the base commit must never block an
    # unrelated later change that doesn't touch it -- the same attribution
    # fairness check-module-size.py's --changed-since guarantees.
    _write_bytes(repo, "src/already-big.json", 2 * 1024 * 1024)
    _commit_all(repo)
    _git(repo, "branch", "-f", "base_marker", "HEAD")

    _write_bytes(repo, "src/small.txt", 10)
    _commit_all(repo)

    result = _run(repo, "--base", "base_marker")

    assert result.returncode == 0, result.stdout + result.stderr


def test_diff_scoped_mode_catches_a_newly_added_oversized_file(repo: Path):
    _write_bytes(repo, "src/small.txt", 10)
    _commit_all(repo)
    _git(repo, "branch", "-f", "base_marker", "HEAD")

    _write_bytes(repo, "src/new-big.json", 2 * 1024 * 1024)
    _commit_all(repo)

    result = _run(repo, "--base", "base_marker")

    assert result.returncode == 1
    assert "new-big.json" in result.stdout


def test_explicit_staged_paths_mode(repo: Path):
    _write_bytes(repo, "src/big.json", 2 * 1024 * 1024)
    _commit_all(repo)

    result = _run(repo, "src/big.json")

    assert result.returncode == 1
    assert "big.json" in result.stdout


def test_deleted_file_in_diff_is_skipped(repo: Path):
    _write_bytes(repo, "src/to-delete.json", 2 * 1024 * 1024)
    _commit_all(repo)
    _git(repo, "branch", "-f", "base_marker", "HEAD")

    (repo / "src" / "to-delete.json").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "remove big file")

    result = _run(repo, "--base", "base_marker")

    assert result.returncode == 0, result.stdout + result.stderr

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


def test_renaming_an_oversized_image_to_a_disallowed_extension_is_caught(repo: Path):
    # A 2MB file is within the image cap as a .png but would violate the
    # default (non-image) cap under a renamed, non-image extension -- the
    # rename itself must not let it dodge detection at its new path.
    _write_bytes(repo, "docs/assets/picture.png", 2 * 1024 * 1024)
    _commit_all(repo)
    _git(repo, "branch", "-f", "base_marker", "HEAD")

    _git(repo, "mv", "docs/assets/picture.png", "docs/assets/picture.json")
    _git(repo, "commit", "-q", "-m", "rename to dodge the image cap")

    result = _run(repo, "--base", "base_marker")

    assert result.returncode == 1
    assert "picture.json" in result.stdout


def test_renaming_a_small_file_to_an_always_blocked_extension_is_caught(repo: Path):
    _write_bytes(repo, "notes/small.txt", 10)
    _commit_all(repo)
    _git(repo, "branch", "-f", "base_marker", "HEAD")

    _git(repo, "mv", "notes/small.txt", "notes/small.patch")
    _git(repo, "commit", "-q", "-m", "rename to dodge the denylist")

    result = _run(repo, "--base", "base_marker")

    assert result.returncode == 1
    assert "small.patch" in result.stdout


def test_a_filename_with_special_characters_is_still_checked(repo: Path):
    # Plain (non -z) git diff/ls-files output quotes non-ASCII/whitespace
    # filenames -- the guard must use NUL-delimited output throughout so an
    # oversized file under such a name is never silently treated as missing.
    _write_bytes(repo, "docs/café notes.json", 2 * 1024 * 1024)
    _commit_all(repo)

    result = _run(repo, "--all")

    assert result.returncode == 1
    assert "café notes.json" in result.stdout


def test_staged_mode_checks_the_index_not_the_working_tree(repo: Path):
    # Stage an oversized file, then shrink its working-tree copy WITHOUT
    # re-staging -- the index still holds the oversized blob that would
    # actually be committed, so the check must still fail.
    _write_bytes(repo, "src/staged-big.json", 2 * 1024 * 1024)
    _git(repo, "add", "src/staged-big.json")
    _write_bytes(repo, "src/staged-big.json", 10)  # shrink working tree only

    result = _run(repo, "src/staged-big.json")

    assert result.returncode == 1
    assert "staged-big.json" in result.stdout


def test_staged_mode_does_not_false_positive_on_working_tree_growth(repo: Path):
    # The reverse of the above: a small staged blob whose working-tree copy
    # has since grown past the cap (not yet re-staged) must still pass --
    # the index, not an unstaged edit, is what would actually be committed.
    _write_bytes(repo, "src/staged-small.json", 10)
    _git(repo, "add", "src/staged-small.json")
    _write_bytes(repo, "src/staged-small.json", 2 * 1024 * 1024)  # grow working tree only

    result = _run(repo, "src/staged-small.json")

    assert result.returncode == 0, result.stdout + result.stderr


def test_diff_scoped_mode_catches_an_oversized_file_added_then_deleted_in_range(repo: Path):
    # The oversized blob is still permanently in the pushed history the
    # instant the add commit lands, even though HEAD's own tree no longer
    # references it -- a naive tree-to-tree diff would miss this entirely.
    _write_bytes(repo, "src/small.txt", 10)
    _commit_all(repo)
    _git(repo, "branch", "-f", "base_marker", "HEAD")

    _write_bytes(repo, "src/transient-big.json", 2 * 1024 * 1024)
    _commit_all(repo)
    (repo / "src" / "transient-big.json").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "remove it again, same push")

    result = _run(repo, "--base", "base_marker")

    assert result.returncode == 1
    assert "transient-big.json" in result.stdout


def test_diff_scoped_mode_catches_an_always_blocked_file_added_then_deleted_in_range(repo: Path):
    _write_bytes(repo, "src/small.txt", 10)
    _commit_all(repo)
    _git(repo, "branch", "-f", "base_marker", "HEAD")

    _write_bytes(repo, "dist/transient.patch", 10)
    _commit_all(repo)
    (repo / "dist" / "transient.patch").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "remove it again, same push")

    result = _run(repo, "--base", "base_marker")

    assert result.returncode == 1
    assert "transient.patch" in result.stdout


def test_diff_scoped_mode_catches_a_file_oversized_in_an_earlier_commit_then_shrunk(repo: Path):
    # Same shape as the add-then-delete case, but shrunk back under cap
    # rather than deleted outright -- the earlier, oversized blob is still
    # what got pushed and must still be reported.
    _write_bytes(repo, "src/small.txt", 10)
    _commit_all(repo)
    _git(repo, "branch", "-f", "base_marker", "HEAD")

    _write_bytes(repo, "src/shrinks.json", 2 * 1024 * 1024)
    _commit_all(repo)
    _write_bytes(repo, "src/shrinks.json", 10)
    _commit_all(repo)

    result = _run(repo, "--base", "base_marker")

    assert result.returncode == 1
    assert "shrinks.json" in result.stdout


def test_all_mode_respects_an_explicit_head_other_than_the_checkout(repo: Path):
    _write_bytes(repo, "src/small.txt", 10)
    _commit_all(repo)
    _git(repo, "branch", "other")
    _git(repo, "checkout", "-q", "other")
    _write_bytes(repo, "src/big-on-other.json", 2 * 1024 * 1024)
    _commit_all(repo)
    _git(repo, "checkout", "-q", "-")  # back to the original branch; "other" not checked out

    result = _run(repo, "--all", "--head", "other")

    assert result.returncode == 1
    assert "big-on-other.json" in result.stdout

    # The currently-checked-out branch itself is unaffected.
    result_default = _run(repo, "--all")
    assert result_default.returncode == 0, result_default.stdout + result_default.stderr


def test_explicit_staged_paths_requires_double_dash_for_flag_shaped_names(repo: Path):
    # A staged file literally named "--all" must be checked as a path, not
    # parsed as the --all flag (which would silently switch to full-tree
    # mode against HEAD, never inspecting the actual staged content).
    _write_bytes(repo, "src/small.txt", 10)
    _commit_all(repo)
    _write_bytes(repo, "--all", 2 * 1024 * 1024)
    _git(repo, "add", "--", "--all")

    result = _run(repo, "--", "--all")

    assert result.returncode == 1
    assert "--all" in result.stdout



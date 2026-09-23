"""Regression tests for tools/sync-vendored-libs.py (canonical <-> vendored-copy
sync for shared libs under libs/<lib> and plugins/<plugin>/libs/<lib>).

Drives the real script as a subprocess against a throwaway tree (mirroring
test_check_version_bump.py), since the module name has a hyphen. No git is
needed -- the script is purely filesystem-based.

Run:  python -m pytest tools/test_sync_vendored_libs.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "sync-vendored-libs.py"


def _write(repo: Path, rel: str, text: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _lib_pyproject(repo: Path, rel: str, version: str) -> None:
    _write(repo, rel, f'[project]\nname = "x"\nversion = "{version}"\n')


def _run(repo: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(repo / "tools" / SCRIPT.name), *extra],
        cwd=repo, capture_output=True, text=True, check=False,
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / "tools").mkdir(parents=True)
    (r / "tools" / SCRIPT.name).write_bytes(SCRIPT.read_bytes())
    return r


def _seed_two_copies_in_sync(repo: Path, *, version: str = "0.1.0-dev1") -> None:
    for plugin in ("alpha", "beta"):
        _write(repo, f"plugins/{plugin}/libs/shared-lib/src/shared_lib/__init__.py",
               "shared = 1\n")
        _lib_pyproject(repo, f"plugins/{plugin}/libs/shared-lib/pyproject.toml", version)


def test_check_reports_ok_when_copies_agree_and_no_canonical(repo: Path):
    _seed_two_copies_in_sync(repo)
    result = _run(repo, "--check")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "copies agree" in result.stdout
    # No top-level libs/shared-lib/ yet -- advisory drift note, not a failure.
    assert "no top-level canonical" in result.stdout


def test_check_fails_when_copies_disagree(repo: Path):
    _seed_two_copies_in_sync(repo)
    _write(repo, "plugins/beta/libs/shared-lib/src/shared_lib/__init__.py",
           "shared = 2  # drifted\n")
    result = _run(repo, "--check")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "OUT OF SYNC" in result.stdout


def test_check_reports_canonical_drift_advisory_only(repo: Path):
    _seed_two_copies_in_sync(repo, version="0.1.0-dev21")
    _write(repo, "libs/shared-lib/src/shared_lib/__init__.py", "shared = 999  # stale\n")
    _lib_pyproject(repo, "libs/shared-lib/pyproject.toml", "0.1.0-dev12")
    result = _run(repo, "--check")
    # Copies still agree with each other -> exit 0, even though canonical is stale.
    assert result.returncode == 0, result.stdout + result.stderr
    assert "canonical drift" in result.stdout
    assert "version skew -- canonical=0.1.0-dev12 copies=0.1.0-dev21" in result.stdout


def test_restore_canonical_copies_agreeing_copies_up(repo: Path):
    _seed_two_copies_in_sync(repo, version="0.1.0-dev21")
    _write(repo, "libs/shared-lib/src/shared_lib/__init__.py", "shared = 999  # stale\n")
    _lib_pyproject(repo, "libs/shared-lib/pyproject.toml", "0.1.0-dev12")

    result = _run(repo, "--restore-canonical")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "canonical restored" in result.stdout

    restored = (repo / "libs/shared-lib/src/shared_lib/__init__.py").read_text()
    assert restored == "shared = 1\n"
    restored_pp = (repo / "libs/shared-lib/pyproject.toml").read_text()
    assert '"0.1.0-dev21"' in restored_pp

    # Now a --check should show zero canonical drift.
    check = _run(repo, "--check")
    assert check.returncode == 0
    assert "shared-lib: canonical drift" not in check.stdout


def test_restore_canonical_skips_when_copies_disagree(repo: Path):
    _seed_two_copies_in_sync(repo)
    _write(repo, "plugins/beta/libs/shared-lib/src/shared_lib/__init__.py",
           "shared = 2  # drifted\n")
    _write(repo, "libs/shared-lib/src/shared_lib/__init__.py", "shared = 1\n")
    _lib_pyproject(repo, "libs/shared-lib/pyproject.toml", "0.1.0-dev1")

    result = _run(repo, "--restore-canonical")
    assert result.returncode == 0  # advisory tool; reports, doesn't fail the run
    assert "SKIPPED" in result.stdout
    # Canonical must be untouched since copies disagreed.
    assert (repo / "libs/shared-lib/src/shared_lib/__init__.py").read_text() == "shared = 1\n"


def test_materialize_refuses_when_canonical_is_drifted(repo: Path):
    _seed_two_copies_in_sync(repo, version="0.1.0-dev21")
    _write(repo, "libs/shared-lib/src/shared_lib/__init__.py", "shared = 999  # stale\n")
    _lib_pyproject(repo, "libs/shared-lib/pyproject.toml", "0.1.0-dev12")

    result = _run(repo, "--materialize")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "Refused to materialize" in result.stderr
    # Copies must be untouched.
    copy = repo / "plugins/alpha/libs/shared-lib/src/shared_lib/__init__.py"
    assert copy.read_text() == "shared = 1\n"


def test_materialize_after_restore_round_trips_cleanly(repo: Path):
    _seed_two_copies_in_sync(repo, version="0.1.0-dev21")
    _write(repo, "libs/shared-lib/src/shared_lib/__init__.py", "shared = 999  # stale\n")
    _lib_pyproject(repo, "libs/shared-lib/pyproject.toml", "0.1.0-dev12")

    assert _run(repo, "--restore-canonical").returncode == 0

    # Simulate a real canonical-only change (the DRY workflow this effort wants):
    _write(repo, "libs/shared-lib/src/shared_lib/__init__.py", "shared = 2\n")
    _lib_pyproject(repo, "libs/shared-lib/pyproject.toml", "0.1.0-dev22")

    result = _run(repo, "--materialize")
    assert result.returncode == 0, result.stdout + result.stderr
    for plugin in ("alpha", "beta"):
        copy = repo / f"plugins/{plugin}/libs/shared-lib/src/shared_lib/__init__.py"
        assert copy.read_text() == "shared = 2\n"
        pp = (repo / f"plugins/{plugin}/libs/shared-lib/pyproject.toml").read_text()
        assert '"0.1.0-dev22"' in pp

    check = _run(repo, "--check")
    assert check.returncode == 0
    assert "shared-lib: canonical drift" not in check.stdout


def test_materialize_force_overrides_drift_refusal(repo: Path):
    _seed_two_copies_in_sync(repo, version="0.1.0-dev21")
    _write(repo, "libs/shared-lib/src/shared_lib/__init__.py", "shared = 999  # stale\n")
    _lib_pyproject(repo, "libs/shared-lib/pyproject.toml", "0.1.0-dev12")

    result = _run(repo, "--materialize", "--force")
    assert result.returncode == 0, result.stdout + result.stderr
    copy = repo / "plugins/alpha/libs/shared-lib/src/shared_lib/__init__.py"
    assert copy.read_text() == "shared = 999  # stale\n"

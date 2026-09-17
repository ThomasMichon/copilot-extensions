"""Regression tests for the module-size guard (hard cap + shrink-only baseline).

Drives the real ``tools/check-module-size.py`` as a subprocess inside a
throwaway git repo, the same pattern ``test_check_version_bump.py`` and
``test_check_no_internal_identifiers.py`` already use.

Run:  python -m pytest tools/test_check_module_size.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "check-module-size.py"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _write_lines(repo: Path, rel: str, line_count: int) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        "\n".join(f"x{i} = {i}" for i in range(line_count)) + "\n", encoding="utf-8"
    )


def _write_baseline(repo: Path, data: dict[str, int]) -> None:
    p = repo / "tools" / "module-size-baseline.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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
    _write_lines(repo, "src/small.py", 50)
    _commit_all(repo)

    result = _run(repo)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "[OK]" in result.stdout


def test_an_uncapped_new_file_over_the_cap_fails(repo: Path):
    _write_lines(repo, "src/huge.py", 1001)
    _commit_all(repo)

    result = _run(repo)

    assert result.returncode == 1
    assert "src/huge.py" in result.stdout
    assert "1000-line cap" in result.stdout


def test_a_baselined_file_within_its_ceiling_passes(repo: Path):
    _write_lines(repo, "src/legacy.py", 5000)
    _write_baseline(repo, {"src/legacy.py": 5000})
    _commit_all(repo)

    result = _run(repo)

    assert result.returncode == 0, result.stdout + result.stderr


def test_a_baselined_file_that_grows_past_its_ceiling_fails(repo: Path):
    _write_lines(repo, "src/legacy.py", 5001)
    _write_baseline(repo, {"src/legacy.py": 5000})
    _commit_all(repo)

    result = _run(repo)

    assert result.returncode == 1
    assert "src/legacy.py" in result.stdout
    assert "grandfathered ceiling" in result.stdout


def test_a_baselined_file_that_shrinks_still_passes(repo: Path):
    _write_lines(repo, "src/legacy.py", 4000)
    _write_baseline(repo, {"src/legacy.py": 5000})
    _commit_all(repo)

    result = _run(repo)

    assert result.returncode == 0, result.stdout + result.stderr


def test_test_files_are_exempt_from_the_cap(repo: Path):
    _write_lines(repo, "tests/test_something.py", 5000)
    _write_lines(repo, "src/pkg/tests/test_other.py", 5000)
    _write_lines(repo, "src/pkg/conftest.py", 5000)
    _commit_all(repo)

    result = _run(repo)

    assert result.returncode == 0, result.stdout + result.stderr


def test_refresh_baseline_lowers_a_shrunk_entry(repo: Path):
    _write_lines(repo, "src/legacy.py", 4000)
    _write_baseline(repo, {"src/legacy.py": 5000})
    _commit_all(repo)

    result = _run(repo, "--refresh-baseline")
    assert result.returncode == 0, result.stdout + result.stderr

    baseline = json.loads((repo / "tools" / "module-size-baseline.json").read_text())
    assert baseline == {"src/legacy.py": 4000}


def test_refresh_baseline_graduates_a_file_that_dropped_to_or_below_the_cap(
    repo: Path,
):
    _write_lines(repo, "src/legacy.py", 900)
    _write_baseline(repo, {"src/legacy.py": 5000})
    _commit_all(repo)

    result = _run(repo, "--refresh-baseline")
    assert result.returncode == 0, result.stdout + result.stderr

    baseline = json.loads((repo / "tools" / "module-size-baseline.json").read_text())
    assert baseline == {}


def test_refresh_baseline_never_raises_a_ceiling_for_a_grown_file(repo: Path):
    _write_lines(repo, "src/legacy.py", 5001)
    _write_baseline(repo, {"src/legacy.py": 5000})
    _commit_all(repo)

    result = _run(repo, "--refresh-baseline")
    assert result.returncode == 0, result.stdout + result.stderr

    baseline = json.loads((repo / "tools" / "module-size-baseline.json").read_text())
    # Unchanged: growth past an existing ceiling is a `check()` failure to fix,
    # never something --refresh-baseline silently absorbs.
    assert baseline == {"src/legacy.py": 5000}


def test_refresh_baseline_discovers_a_new_offender(repo: Path):
    _write_lines(repo, "src/new_huge.py", 1200)
    _write_baseline(repo, {})
    _commit_all(repo)

    result = _run(repo, "--refresh-baseline")
    assert result.returncode == 0, result.stdout + result.stderr

    baseline = json.loads((repo / "tools" / "module-size-baseline.json").read_text())
    assert baseline == {"src/new_huge.py": 1200}

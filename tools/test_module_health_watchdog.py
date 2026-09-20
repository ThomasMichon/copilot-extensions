"""Regression tests for the module-health watchdog's pure decision logic
(``worst_candidate``). The ``gh`` issue-filing I/O is intentionally left
untested here, mirroring ``tools/rank-module-size.py``'s own convention of no
dedicated test file for its thin reporting wrapper -- only the actual
decision logic (which file wins, and why) is worth pinning down.

Run:  python -m pytest tools/test_module_health_watchdog.py
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "module-health-watchdog.py"
CHECK_MODULE_SIZE = Path(__file__).resolve().parent / "check-module-size.py"


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


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / CHECK_MODULE_SIZE.name).write_bytes(CHECK_MODULE_SIZE.read_bytes())
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    return tmp_path


def _commit_all(repo: Path) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "snapshot")


def _load_watchdog(repo: Path):
    spec = importlib.util.spec_from_file_location("module_health_watchdog", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_worst_candidate_prefers_an_already_failing_file_over_a_merely_near_cap_one(
    repo: Path,
):
    # small.py is comfortably within the cap; legacy.py has already grown
    # past its own baselined ceiling (a real, active violation).
    _write_lines(repo, "src/small.py", 50)
    _write_lines(repo, "src/legacy.py", 5001)
    _write_baseline(repo, {"src/legacy.py": 5000})
    _commit_all(repo)

    watchdog = _load_watchdog(repo)
    cms = watchdog._load_check_module_size()
    # Point the loaded check-module-size module at the throwaway repo, not
    # this real repo's own tree.
    cms.REPO = repo
    cms.BASELINE_PATH = repo / "tools" / "module-size-baseline.json"

    path, lines, ceiling, margin = watchdog.worst_candidate(cms)

    assert path == "src/legacy.py"
    assert lines == 5001
    assert ceiling == 5000
    assert margin == -1


def test_worst_candidate_picks_the_smallest_positive_margin_when_none_fail(repo: Path):
    _write_lines(repo, "src/comfortable.py", 100)
    _write_lines(repo, "src/close.py", 990)  # 10 lines under the 1000 cap
    _commit_all(repo)

    watchdog = _load_watchdog(repo)
    cms = watchdog._load_check_module_size()
    cms.REPO = repo
    cms.BASELINE_PATH = repo / "tools" / "module-size-baseline.json"

    path, lines, ceiling, margin = watchdog.worst_candidate(cms)

    assert path == "src/close.py"
    assert margin == 10


def test_worst_candidate_returns_none_when_nothing_is_tracked(repo: Path):
    _commit_all(repo)
    # The fixture's own tools/check-module-size.py copy is itself a tracked
    # non-test .py file -- untrack it (keep it on disk for the import below)
    # so `git ls-files` genuinely reports zero candidates.
    _git(repo, "rm", "--cached", "-q", "tools/check-module-size.py")
    _git(repo, "commit", "-q", "-m", "untrack the harness copy")

    watchdog = _load_watchdog(repo)
    cms = watchdog._load_check_module_size()
    cms.REPO = repo
    cms.BASELINE_PATH = repo / "tools" / "module-size-baseline.json"

    assert watchdog.worst_candidate(cms) is None


def test_dry_run_cli_reports_without_filing():
    # Runs the real script in place against this repo's own tree -- confirms
    # the default (no --file-issue) path never shells out to `gh` and always
    # exits 0.
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=SCRIPT.parent.parent,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "dry run" in result.stdout

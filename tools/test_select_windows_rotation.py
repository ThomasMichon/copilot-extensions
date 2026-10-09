"""Tests for tools/select_windows_rotation.py."""

from __future__ import annotations

import datetime
import importlib.util
import sys
from pathlib import Path

import yaml

SCRIPT = Path(__file__).resolve().parent / "select_windows_rotation.py"
_previous_path = sys.path.copy()
sys.path.insert(0, str(SCRIPT.parent))
try:
    _spec = importlib.util.spec_from_file_location("select_windows_rotation", SCRIPT)
    assert _spec and _spec.loader
    rotation = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = rotation
    _spec.loader.exec_module(rotation)
finally:
    sys.path[:] = _previous_path


def test_picks_the_expected_count() -> None:
    picks = rotation.select_plugins(datetime.date(2026, 10, 8))
    assert len(picks) == rotation.TIER_A_PICKS_PER_RUN + 1


def test_picks_are_known_and_tier_a_has_no_duplicates() -> None:
    picks = rotation.select_plugins(datetime.date(2026, 10, 8))
    tier_a_picks = picks[: rotation.TIER_A_PICKS_PER_RUN]
    tier_b_pick = picks[rotation.TIER_A_PICKS_PER_RUN]
    assert all(p in rotation.TIER_A for p in tier_a_picks)
    assert len(set(tier_a_picks)) == len(tier_a_picks)
    assert tier_b_pick in rotation.TIER_B


def test_tiers_are_disjoint() -> None:
    assert set(rotation.TIER_A).isdisjoint(rotation.TIER_B)


def test_deterministic_for_the_same_date() -> None:
    date = datetime.date(2026, 11, 3)
    assert rotation.select_plugins(date) == rotation.select_plugins(date)


def test_every_tier_a_plugin_is_reachable_across_the_rotation_cycle() -> None:
    seen: set[str] = set()
    start = datetime.date(2026, 1, 1)
    for offset in range(len(rotation.TIER_A) + 10):
        seen.update(rotation.select_plugins(start + datetime.timedelta(days=offset)))
    assert set(rotation.TIER_A).issubset(seen)


def test_every_tier_b_plugin_is_reachable_across_the_rotation_cycle() -> None:
    seen: set[str] = set()
    start = datetime.date(2026, 1, 1)
    for offset in range(len(rotation.TIER_B) + 5):
        seen.update(rotation.select_plugins(start + datetime.timedelta(days=offset)))
    assert set(rotation.TIER_B).issubset(seen)


def test_cli_json_format(capsys) -> None:
    sys.argv = ["select_windows_rotation.py", "--date", "2026-10-08", "--format", "json"]
    assert rotation.main() == 0
    captured = capsys.readouterr()
    assert captured.out.strip().startswith("[")


def test_cli_github_output_format(capsys) -> None:
    sys.argv = [
        "select_windows_rotation.py",
        "--date",
        "2026-10-08",
        "--format",
        "github-output",
    ]
    assert rotation.main() == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("plugins=[")


def test_windows_workers_are_bounded_and_plugin_scoped() -> None:
    workflow = yaml.safe_load(
        (SCRIPT.parent.parent / ".github" / "workflows" /
         "windows-coverage-rotation.yml").read_text(encoding="utf-8")
    )
    job = workflow["jobs"]["test"]
    options = job["env"]["PYTEST_ADDOPTS"]
    assert options.startswith(
        "${{ matrix.plugin == 'agent-worktrees' && '-n 2 --dist=worksteal' || '' }}"
    )
    assert "inputs.profile_durations" in options
    assert "|| '-v --durations=30 --durations-min=1'" in options
    assert job["steps"][0]["with"]["ref"] == "${{ needs.select.outputs.sha }}"
    assert "inputs.profile_durations && 60 || 30" in job["timeout-minutes"]
    execution = job["steps"][-1]
    assert execution["env"]["WORKTREE_WORKERS"] == "${{ matrix.plugin == 'agent-worktrees' }}"
    assert "--exec-path" in execution["run"]
    assert "$nativeVersion -ne $originalVersion" in execution["run"]
    assert "Get-Command git -CommandType Application | Select-Object -First 1" in execution["run"]

"""HTTP-52 observers keep separate interpreters under one runner lifecycle."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from _pytest.mark.expression import Expression

from plugin_test_selection import plan_test_groups
from test_run_plugin_tests import runner, runner_scratch as runner_scratch

OWNER_FILE = Path("tests/test_http52_owner_capture.py")
REMAINING_FILE = Path("tests/test_http52_capture_probes.py")
OWNERS = (
    "test_retained_acquisition_and_later_lineage_use_actual_owner_processes",
    "test_keyed_creation_uses_actual_retained_serializer_and_preserves_claim",
)


@pytest.mark.parametrize("windows", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_capture_pair_selects_every_case_once_in_separate_observers(
    tmp_path: Path, windows: bool, reverse: bool,
) -> None:
    files = [tmp_path / OWNER_FILE, tmp_path / REMAINING_FILE]
    if reverse:
        files.reverse()
    plan = plan_test_groups("agent-bridge", tmp_path, [files], windows=windows)
    assert len(plan) == 2 and all(len(group) == 1 for group, _ in plan)
    selected: list[str] = []
    for group, selector in plan:
        assert selector is not None
        expression = Expression.compile(selector)
        available = OWNERS if group[0] == tmp_path / OWNER_FILE else (*OWNERS, "test_remaining")
        for name in available:
            def matches(keyword: str, /, **kwargs: str | int | bool | None) -> bool:
                return keyword in name

            if expression.evaluate(matches):
                selected.append(name)
    assert Counter(selected) == Counter((*OWNERS, "test_remaining"))


@pytest.mark.parametrize(("plugin", "files"), [
    ("agent-bridge", [OWNER_FILE]),
    ("agent-bridge", [REMAINING_FILE]),
    ("agent-bridge", [OWNER_FILE, REMAINING_FILE, Path("tests/test_other.py")]),
    ("other-plugin", [OWNER_FILE, REMAINING_FILE]),
])
def test_capture_partition_does_not_change_other_file_selections(
    tmp_path: Path, plugin: str, files: list[Path],
) -> None:
    group = [tmp_path / path for path in files]
    assert plan_test_groups(plugin, tmp_path, [group], windows=True) == [(group, None)]


@pytest.mark.parametrize("expired", [False, True])
def test_capture_cohorts_prepare_once_and_keep_original_deadline_and_resource_caps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, expired: bool,
) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    for relative in (OWNER_FILE, REMAINING_FILE):
        (tmp_path / relative).write_text("def test_fixture(): pass\n", encoding="utf-8")
    prepared: list[str] = []

    def prepare(name: str, *args: Any, **kwargs: Any) -> Path:
        import sys

        prepared.append(name)
        return Path(sys.executable)

    monkeypatch.setattr(runner, "_plugin_dir", lambda name: tmp_path)
    monkeypatch.setattr(runner, "_ensure_venv", prepare)
    clock = iter((0, 0, 30, 241, 241) if expired else (0, 0, 30, 130, 230))
    monkeypatch.setattr(runner, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    budgets: list[float] = []
    reports: list[Path] = []

    def execute(command: list[str], **kwargs: Any) -> int:
        limits = kwargs["limits"]
        budgets.append(limits.wall_seconds)
        assert (limits.max_processes, limits.max_memory_mb, limits.max_temp_mb) == (
            128, 4096, 2048,
        )
        report = Path(next(
            argument.split("=", 1)[1] for argument in command
            if argument.startswith("--junitxml=")
        ))
        reports.append(report)
        report.write_text("<testsuite/>", encoding="utf-8")
        return 0

    monkeypatch.setattr(runner, "run_contained", execute)
    result = runner.run_plugin(
        "agent-bridge", "unused", reinstall=False,
        kexpr=" or ".join((*OWNERS, "test_remaining")),
        limits=runner.Limits(wall_seconds=240), plugin_timeout=240,
        test_timeout=120, max_files_per_subsuite=25,
        test_files=(OWNER_FILE.as_posix(), REMAINING_FILE.as_posix()),
        junit_dir=tmp_path / "reports",
    )
    assert result == (124 if expired else 0)
    assert prepared == ["agent-bridge"]
    assert budgets == ([210] if expired else [210, 110])
    assert len(reports) == len(set(reports)) == (1 if expired else 2)

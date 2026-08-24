"""Repository-wide hook process hygiene guards."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_PLUGINS = _ROOT / "plugins"
_NESTED_POWERSHELL_MARKERS = (
    "Get-Command pwsh",
    "& $p.Source -NoProfile -File",
    "powershell.exe -NoProfile -ExecutionPolicy Bypass -File",
)


def _powershell_hooks() -> list[tuple[Path, str, int, str]]:
    hooks: list[tuple[Path, str, int, str]] = []
    for path in sorted(_PLUGINS.glob("*/hooks.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for event, entries in data.get("hooks", {}).items():
            for index, entry in enumerate(entries):
                command = entry.get("powershell")
                if command:
                    hooks.append((path, event, index, command))
    return hooks


@pytest.mark.guard
def test_powershell_hooks_do_not_spawn_nested_interpreters():
    violations: list[str] = []
    for path, event, index, command in _powershell_hooks():
        markers = [
            marker for marker in _NESTED_POWERSHELL_MARKERS if marker in command
        ]
        if markers:
            violations.append(
                f"{path.relative_to(_ROOT)} {event}[{index}]: {', '.join(markers)}"
            )

    assert not violations, (
        "PowerShell hooks already run inside a PowerShell process; execute .ps1 "
        "targets directly instead of spawning a nested interpreter:\n  "
        + "\n  ".join(violations)
    )


@pytest.mark.guard
def test_agent_worktrees_project_hook_runner_stays_in_process():
    runner = (
        _PLUGINS / "agent-worktrees" / "scripts" / "project-hooks.ps1"
    ).read_text(encoding="utf-8")
    markers = [
        marker for marker in _NESTED_POWERSHELL_MARKERS if marker in runner
    ]
    assert not markers, (
        "project-hooks.ps1 must invoke the project hook in its current PowerShell "
        f"process; found: {', '.join(markers)}"
    )


@pytest.mark.guard
def test_powershell_module_analysis_cache_is_ignored():
    ignore_rules = (_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "ModuleAnalysisCache" in ignore_rules

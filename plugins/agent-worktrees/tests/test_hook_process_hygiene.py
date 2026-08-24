"""Repository-wide hook process hygiene guards."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_PLUGINS = _ROOT / "plugins"
_NESTED_POWERSHELL = re.compile(
    r"(?im)(?:^|[;{}]\s*)(?:&\s*)?"
    r"(?P<interpreter>\$p\.Source|pwsh(?:\.exe)?|powershell(?:\.exe)?)\b"
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
        match = _NESTED_POWERSHELL.search(command)
        if match:
            violations.append(
                f"{path.relative_to(_ROOT)} {event}[{index}]: "
                f"{match.group('interpreter')}"
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
    match = _NESTED_POWERSHELL.search(runner)
    assert match is None, (
        "project-hooks.ps1 must invoke the project hook in its current PowerShell "
        f"process; found: {match.group('interpreter') if match else ''}"
    )


@pytest.mark.guard
def test_powershell_module_analysis_cache_is_ignored():
    ignore_rules = (_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "ModuleAnalysisCache" in ignore_rules

#!/usr/bin/env python3
"""Reject plugin PowerShell hooks that launch another PowerShell interpreter."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGINS = REPO / "plugins"
NESTED_POWERSHELL = re.compile(
    r"(?im)(?:^|[;{}]\s*)(?:&\s*)?"
    r"(?P<interpreter>\$p\.Source|pwsh(?:\.exe)?|powershell(?:\.exe)?)\b"
)


def find_problems() -> list[str]:
    problems: list[str] = []
    for path in sorted(PLUGINS.glob("*/hooks.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        for event, entries in data.get("hooks", {}).items():
            for index, entry in enumerate(entries):
                command = entry.get("powershell") or ""
                match = NESTED_POWERSHELL.search(command)
                if match:
                    problems.append(
                        f"{path.relative_to(REPO)} {event}[{index}] launches "
                        f"`{match.group('interpreter')}`"
                    )

    runner = PLUGINS / "agent-worktrees" / "scripts" / "project-hooks.ps1"
    match = NESTED_POWERSHELL.search(runner.read_text(encoding="utf-8"))
    if match:
        problems.append(
            f"{runner.relative_to(REPO)} launches `{match.group('interpreter')}`"
        )

    ignore_rules = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
    if not any(
        "ModuleAnalysisCache" in rule
        for rule in ignore_rules
        if rule.strip() and not rule.lstrip().startswith("#")
    ):
        problems.append(".gitignore does not ignore PowerShell ModuleAnalysisCache")

    return problems


def main() -> int:
    problems = find_problems()
    if problems:
        print("[FAIL] PowerShell hook process hygiene:")
        for problem in problems:
            print(f"  - {problem}")
        print(
            "\nPowerShell hooks already run inside PowerShell. Execute `.ps1` "
            "targets directly with `& $script`."
        )
        return 1

    print("[OK] PowerShell hooks execute scripts without nested interpreters.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

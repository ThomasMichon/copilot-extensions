"""Collect the scanner's colocated contract tests through the plugin runner."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SOURCE = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "reviewing-customizations"
    / "scripts"
    / "test_scan_customizations.py"
)
SPEC = importlib.util.spec_from_file_location(
    "reviewing_customizations_scanner_tests",
    SOURCE,
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

for name, value in vars(MODULE).items():
    if name.startswith("test_"):
        globals()[name] = value


def test_real_queue_assignment_discovery_requires_the_adopted_queue_flow():
    plugins = Path(__file__).resolve().parents[2]
    skill = plugins / "agent-dispatch/skills/pick-and-claim/SKILL.md"
    parsed = MODULE.scan.split_frontmatter(skill.read_text(encoding="utf-8"))
    assert parsed is not None
    frontmatter, _ = parsed
    assert MODULE.scan.extract_triggers(frontmatter) == [
        "self-dispatch through agent-dispatch", "pick and claim a queue task",
        "resume queue self-dispatch",
    ]

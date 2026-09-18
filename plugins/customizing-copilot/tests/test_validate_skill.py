"""Collect the review skill's loader contracts through the plugin runner."""

import importlib.util
from pathlib import Path


SOURCE = (
    Path(__file__).resolve().parents[1]
    / "skills" / "reviewing-customizations" / "scripts" / "test_validate_skill.py"
)
SPEC = importlib.util.spec_from_file_location("skill_loader_contract_tests", SOURCE)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

ValidateSkillTests = MODULE.ValidateSkillTests
LiveCopilotTests = MODULE.LiveCopilotTests

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

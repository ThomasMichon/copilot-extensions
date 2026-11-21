"""Export the original mocked full-source scanner subprocess controls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_source_ast_guards.py"
_spec = importlib.util.spec_from_file_location("promotion_original_source_ast_controls", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_exact_scanner_commands_budget_and_output_are_preserved",
    "test_timeout_preserves_partial_diagnostics_and_fails",
    "test_missing_scanner_interpreter_fails_explicitly",
)
for _name in _SELECTED:
    globals()[_name] = getattr(_original, _name)

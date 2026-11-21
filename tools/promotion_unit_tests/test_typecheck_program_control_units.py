"""Export the original in-process independent-program typing controls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_typecheck_programs.py"
_spec = importlib.util.spec_from_file_location("promotion_original_typecheck_programs", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)
_isolated_scratch = _original._isolated_scratch

_SELECTED = (
    "test_independent_programs_refuse_source_change_between_programs",
    "test_manifest_retains_optional_program_roots_without_changing_policy",
    "test_manifest_refuses_invalid_or_cross_program_imports",
)
for _name in _SELECTED:
    globals()[_name] = getattr(_original, _name)

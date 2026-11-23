"""Original explicit-file controls, excluding preparation and execution."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_tools = Path(__file__).resolve().parents[1]
if str(_tools) not in sys.path:
    sys.path.insert(0, str(_tools))
_path = _tools / "test_run_plugin_tests.py"
_spec = importlib.util.spec_from_file_location("promotion_original_runner_explicit_selection", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)
runner_scratch = _original.runner_scratch

_SELECTED = (
    "test_explicit_file_selection_avoids_unrelated_collection",
    "test_explicit_file_selection_refuses_invalid_paths",
    "test_explicit_file_selection_refuses_absolute_path",
    "test_explicit_file_selection_requires_one_execution_target",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})

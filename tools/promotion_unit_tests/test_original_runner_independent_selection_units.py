"""Original independent-file grouping controls, without native execution."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_tools = Path(__file__).resolve().parents[1]
if str(_tools) not in sys.path:
    sys.path.insert(0, str(_tools))
_path = _tools / "test_run_plugin_tests.py"
_spec = importlib.util.spec_from_file_location("promotion_original_runner_independent_selection", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)
runner_scratch = _original.runner_scratch

_SELECTED = (
    "test_bridge_native_and_performance_files_have_independent_groups",
    "test_independent_native_groups_preserve_every_collected_file",
    "test_missing_independent_native_group_fails_explicitly",
    "test_explicit_collection_does_not_require_unselected_independent_files",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})

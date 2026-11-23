"""Original private-fixture file selectors, not runner preparation or execution."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_tools = Path(__file__).resolve().parents[1]
if str(_tools) not in sys.path:
    sys.path.insert(0, str(_tools))
_path = _tools / "test_run_plugin_tests.py"
_spec = importlib.util.spec_from_file_location("promotion_original_runner_file_selection", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)
runner_scratch = _original.runner_scratch
selectable_tests = _original.selectable_tests

_SELECTED = (
    "test_default_discovery_honors_pytest_directory_patterns_but_not_file_names",
    "test_interactive_test_file_paths_are_normalized_without_changing_manifest_policy",
    "test_explicit_test_files_reject_unknown_or_nonowned_paths_before_preparation",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})

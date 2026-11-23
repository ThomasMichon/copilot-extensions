"""Original mocked runner admission controls, without real lease acquisition."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_tools = Path(__file__).resolve().parents[1]
if str(_tools) not in sys.path:
    sys.path.insert(0, str(_tools))
_path = _tools / "test_run_plugin_tests.py"
_spec = importlib.util.spec_from_file_location("promotion_original_runner_admission_routing", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)
runner_scratch = _original.runner_scratch

_SELECTED = (
    "test_admission_fails_fast_with_live_holder",
    "test_run_holds_one_environment_lease_at_a_time",
    "test_unexpected_runner_error_does_not_wedge_remaining_plugins",
    "test_guards_also_take_environment_admission",
    "test_collect_only_also_takes_environment_admission",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})

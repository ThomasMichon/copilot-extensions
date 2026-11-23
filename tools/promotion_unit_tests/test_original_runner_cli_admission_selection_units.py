"""Original mocked CLI admission and changed-plugin selection controls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_tools = Path(__file__).resolve().parents[1]
if str(_tools) not in sys.path:
    sys.path.insert(0, str(_tools))
_path = _tools / "test_run_plugin_tests.py"
_spec = importlib.util.spec_from_file_location("promotion_original_runner_cli_admission_selection", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)
runner_scratch = _original.runner_scratch

_SELECTED = (
    "test_prepare_only_takes_environment_admission",
    "test_host_state_requires_explicit_tier_opt_in",
    "test_admission_wait_rejects_infinite_value",
    "test_admission_wait_rejects_nan_value",
    "test_changed_plugins_always_includes_cross_plugin_contract_testers",
    "test_changed_plugins_empty_diff_schedules_nothing",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})

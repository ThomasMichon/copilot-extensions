"""Original runner argument controls, without native or profile execution."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_tools = Path(__file__).resolve().parents[1]
if str(_tools) not in sys.path:
    sys.path.insert(0, str(_tools))
_path = _tools / "test_run_plugin_tests.py"
_spec = importlib.util.spec_from_file_location("promotion_original_runner_profile_argument", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)
runner_scratch = _original.runner_scratch

_SELECTED = (
    "test_native_owner_test_profile_is_scoped_and_explicit_limits_win",
    "test_explicit_file_group_limit_must_remain_positive",
    "test_codespaces_functional_profile_budgets_and_caps",
    "test_codespaces_functional_profile_refuses_unqualified_scope_and_overrides",
    "test_codespaces_functional_profile_refuses_unknown_name",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})

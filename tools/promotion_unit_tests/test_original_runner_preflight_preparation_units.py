"""Original preflight refusal and preparation-only early-return controls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_tools = Path(__file__).resolve().parents[1]
if str(_tools) not in sys.path:
    sys.path.insert(0, str(_tools))
_path = _tools / "test_run_plugin_tests.py"
_spec = importlib.util.spec_from_file_location("promotion_original_runner_preflight_preparation", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)
runner_scratch = _original.runner_scratch

_SELECTED = (
    "test_unsafe_child_cleanup_refuses_before_preparation_or_collection",
    "test_source_preflight_counts_against_plugin_budget_before_preparation",
    "test_run_plugin_prepare_only_never_invokes_pytest",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})

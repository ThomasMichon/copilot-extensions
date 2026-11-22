"""Export original UV-selection fixtures, retaining native retention and links outside."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_dependency_uv_executable.py"
_spec = importlib.util.spec_from_file_location("promotion_original_uv_executable_fixture_units", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_windows_absolute_uv_selection_preserves_the_selected_target",
    "test_missing_selected_uv_is_not_replaced_by_another_resolver",
    "test_other_command_resolution_contracts_are_unchanged",
    "test_an_absolute_non_native_wrapper_is_not_reinterpreted",
    "test_sdk_projection_keeps_the_canonical_nontraversing_primitive",
    "test_projection_uses_the_canonical_function_without_changing_other_configuration",
    "test_projection_refuses_incomplete_or_reordered_markers",
    "test_named_windows_uv_uses_native_lookup_then_the_same_physical_target",
    "test_native_lookup_failure_is_explicit_and_never_selects_another_uv",
    "test_native_lookup_refuses_an_oversized_path",
    "test_named_non_native_wrappers_do_not_enter_native_executable_lookup",
)
for _name in _SELECTED:
    assert _name not in globals(), f"duplicate original UV-executable control: {_name}"
    globals()[_name] = getattr(_original, _name)

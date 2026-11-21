"""Export the original in-process dependency release-age policy controls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_dependency_age.py"
_spec = importlib.util.spec_from_file_location("promotion_original_dependency_age_release", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_inclusive_age_boundary",
    "test_unknown_timestamps",
    "test_newest_file_missing_and_yanked_fail_closed",
    "test_python_normalized_literal_floor_and_compatible_specifier",
    "test_python_literals_and_unsatisfiable_specs",
    "test_unbounded_upper_and_wildcard_declarations",
    "test_npm_controlled_metadata",
    "test_npm_yanked_missing_and_incompatible",
    "test_native_uv_seven_day_boundary_and_stronger_settings",
    "test_native_uv_missing_disabled_weaker_or_unknown_policy",
    "test_native_uv_package_override_cannot_disable_or_weaken",
    "test_native_uv_stronger_global_and_pip_policy_cannot_be_weakened",
    "test_index_specific_cutoffs_rejected_even_if_nominally_stronger",
)
for _name in _SELECTED:
    globals()[_name] = getattr(_original, _name)

"""Export original cutoff-projection controls without altering their witnesses."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_installer_dependency_policy.py"
_spec = importlib.util.spec_from_file_location("promotion_original_dependency_cutoff_controls", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_exact_floor_and_reinstall",
    "test_stricter_global_is_honored",
    "test_weaker_global_is_strengthened",
    "test_stricter_relative_is_honored",
    "test_structured_uv_cutoff_preserves_the_strongest_boundary",
    "test_structured_uv_package_cutoff_cannot_weaken_seasoning",
    "test_structured_uv_cutoff_unknown_or_malformed_fields_refuse",
    "test_structured_package_comparison_preserves_stricter_timestamps",
    "test_stricter_package_is_preserved",
    "test_every_effective_package_override_is_frozen_to_its_admitted_absolute_cutoff",
    "test_package_bypass_refused",
    "test_index_override_cannot_assign_unknown_upload_age",
    "test_missing_unknown_or_truncated_configuration_refuses",
)
for _name in _SELECTED:
    assert _name not in globals(), f"duplicate original cutoff control: {_name}"
    globals()[_name] = getattr(_original, _name)

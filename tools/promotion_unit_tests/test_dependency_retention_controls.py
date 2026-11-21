"""Export original retention controls without copied-module qualification."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_installer_dependency_policy.py"
_spec = importlib.util.spec_from_file_location("promotion_original_dependency_retention_controls", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_install_execution_uses_budget_remaining_after_retention",
    "test_generated_local_exclusions_refuse_invalid_names_before_uv",
    "test_generated_local_exclusions_canonicalize_owned_temp_aliases",
    "test_absolute_cutoff_fraction_is_python310_compatible_and_conservative",
)
for _name in _SELECTED:
    assert _name not in globals(), f"duplicate original retention control: {_name}"
    globals()[_name] = getattr(_original, _name)

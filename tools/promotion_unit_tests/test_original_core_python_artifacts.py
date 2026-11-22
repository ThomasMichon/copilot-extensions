"""Export original synthetic core-artifact controls without replacing witnesses."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_core_python_artifacts.py"
_spec = importlib.util.spec_from_file_location("promotion_original_core_python_artifacts", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_core_preserves_only_the_existing_exact_manager_wheel_exception",
    "test_core_builds_each_canonical_project_once_and_roundtrips",
    "test_core_source_binding_refuses_incomplete_or_changed_composition",
    "test_core_uses_existing_marketplace_version_and_hashes_selection",
    "test_core_selection_does_not_depend_on_build_host",
    "test_core_retains_root_extras_and_optional_environment_boundaries",
    "test_core_rejects_duplicate_canonical_distribution",
    "test_core_release_profile_cannot_omit_any_first_party_wheel",
    "test_promotion_builds_and_requires_the_core_with_the_shared_lock",
    "test_invalid_core_activation_is_not_silently_ignored",
    "test_core_cli_reuses_existing_policy_and_toolchain_arguments",
    "test_core_malformed_manifests_are_structured_refusals",
    "test_real_core_bundle_binds_a_complete_profile_through_existing_release_adapter",
)
for _name in _SELECTED:
    assert _name not in globals(), f"duplicate original core-artifact control: {_name}"
    globals()[_name] = getattr(_original, _name)

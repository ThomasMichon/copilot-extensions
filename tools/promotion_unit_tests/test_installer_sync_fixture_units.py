"""Export original installer-sync controls that use synthetic repository data."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_sync_installer_engine.py"
_spec = importlib.util.spec_from_file_location("promotion_original_installer_sync_fixture_units", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

fake_repo = _original.fake_repo
_SELECTED = (
    "test_evaluator_bootstrap_delivery_is_scoped_and_byte_identical",
    "test_python_engine_ships_and_checks_all_owned_process_files",
    "test_unregistered_python_engine_is_rejected",
    "test_standalone_native_engine_uses_existing_sync_owner",
    "test_verify_passes_when_adopter_is_byte_identical",
    "test_verify_accepts_a_canonical_reference_with_no_local_copy",
    "test_verify_flags_drifted_registered_adopter",
    "test_verify_flags_missing_registered_adopter",
    "test_verify_flags_unregistered_adopter_with_a_stray_copy",
    "test_verify_flags_unregistered_adopter_with_local_ref_and_no_copy",
    "test_verify_flags_unregistered_adopter_with_trailing_comment_ref_and_no_copy",
    "test_verify_ignores_plugin_with_no_installer_engine_file",
    "test_verify_flags_an_escaping_noncanonical_reference",
    "test_verify_rejects_malformed_local_reference_even_when_copies_exist",
    "test_verify_rejects_normalized_local_reference_with_missing_intermediate",
    "test_verify_rejects_mismatched_powershell_quotes",
    "test_verify_rejects_mixed_local_and_canonical_forms",
    "test_verify_flags_duplicate_engine_source_lines",
    "test_sync_repairs_missing_local_copy_when_wrappers_are_local",
    "test_sync_removes_stale_local_copy_when_wrappers_are_canonical",
    "test_sync_repairs_python_support_in_both_wrapper_modes",
    "test_development_layout_is_not_materialized_copy_qualification",
)
for _name in _SELECTED:
    assert _name not in globals(), f"duplicate original installer-sync control: {_name}"
    globals()[_name] = getattr(_original, _name)

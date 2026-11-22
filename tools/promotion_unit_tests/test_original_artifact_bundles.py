"""Export original synthetic bundle controls without changing their witnesses."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_promotion_python_artifacts.py"
_spec = importlib.util.spec_from_file_location("promotion_original_artifact_bundles", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

no_ambient_git_identity = _original.no_ambient_git_identity
_SELECTED = (
    "test_bundle_and_transfer_roundtrip",
    "test_bundle_wheel_read_failures_are_structured",
    "test_build_cli_creates_missing_output_directory",
    "test_build_cli_reports_invalid_output_directory",
    "test_identical_artifact_bundles_ignore_clock_mtimes_permissions_and_wheel_order",
    "test_trusted_validator_rejects_archive_corruption_even_with_valid_outer_digest",
    "test_validator_accepts_declared_vendored_library_wheel",
    "test_schema3_requires_complete_provenance_and_computed_identity",
    "test_forged_content_address_fails_even_when_manifest_index_filename_and_digest_agree",
    "test_trusted_wheel_validation_reads_real_metadata_and_members",
    "test_encoded_runtime_metadata_is_a_structured_refusal",
    "test_digest_matches_sha256_across_multiple_chunks",
    "test_transfer_rejects_another_composition",
    "test_transfer_rejects_corruption",
    "test_bundle_rejects_unsafe_wheels",
    "test_declared_project_manifest_keeps_the_selected_primary_source",
    "test_declared_project_manifest_refuses_unsafe_source_root",
    "test_declared_project_manifest_cannot_select_another_repository_location",
    "test_signer_requires_configured_policy_and_one_shared_lock",
)
for _name in _SELECTED:
    assert _name not in globals(), f"duplicate original bundle control: {_name}"
    globals()[_name] = getattr(_original, _name)

"""Export original checker inventory, manifest and in-process matrix controls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_typecheck.py"
_spec = importlib.util.spec_from_file_location("promotion_original_typecheck_manifest_units", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

fixed_checker_stage = _original.fixed_checker_stage
_isolated_scratch = _original._isolated_scratch
_SELECTED = (
    "test_inventory_rejects_new_or_ambiguous_python",
    "test_directory_prefixes_do_not_claim_neighboring_components",
    "test_paths_cannot_inject_response_file_arguments",
    "test_manifest_rejects_duplicate_keys_and_path_escape",
    "test_additional_versions_preserve_minimum_defaults_and_physical_ownership",
    "test_manifest_rejects_invalid_additional_version_policy",
    "test_optional_constructor_positions_and_combined_manifest",
    "test_combined_matrix_preserves_import_custody_and_failure_collection",
    "test_manifest_optional_import_roots_preserve_old_defaults",
    "test_manifest_refuses_invalid_import_roots",
    "test_import_roots_refuse_native_path_list_separators",
    "test_import_roots_are_ordered_project_local_and_lease_cleaned",
    "test_import_inputs_accept_owned_wip_and_namespace_members_only",
    "test_import_inputs_follow_native_stub_extension_aliases",
    "test_import_inputs_preserve_native_owned_source_extension_aliases",
    "test_import_input_snapshot_refuses_member_and_root_changes",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})

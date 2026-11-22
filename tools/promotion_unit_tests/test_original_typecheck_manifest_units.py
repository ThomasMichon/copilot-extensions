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
    "test_import_input_drift_is_refused_and_settles_cleanup",
    "test_changed_selection_conservatively_checks_all_admitted_consumers",
    "test_missing_preparation_is_actionable_and_does_not_create_environment",
    "test_pending_probe_never_certifies_admission",
    "test_dependency_policy_refusal_has_the_setup_error_contract",
    "test_preparation_installs_the_same_lock_bytes_that_keyed_the_cache",
    "test_failed_cache_repair_cannot_preserve_preparation_marker",
    "test_preparation_refuses_unqualified_requirement_includes",
    "test_preparation_refuses_replaced_leased_directory",
    "test_readiness_is_revalidated_under_the_checker_lease",
    "test_checker_timeout_settles_targets_and_environment_lease",
    "test_guard_alias_exact_scope_bytes_full_map_and_current_targets",
    "test_guard_alias_refuses_drift_and_cleans_only_owned_outputs",
    "test_source_view_accounts_for_order_bytes_and_physical_lines",
    "test_native_fragment_loader_remains_source_mapped",
    "test_copied_fragment_view_verifies_its_own_inputs",
    "test_source_view_refuses_unqualified_native_loader_changes",
    "test_source_view_refuses_loader_wide_suppressions",
    "test_source_view_inlines_the_declared_fragment_order",
    "test_source_view_refuses_stale_or_incomplete_composition",
    "test_composition_cannot_drop_code_or_spread_file_wide_suppressions",
    "test_imported_view_refuses_source_drift_and_settles_its_lease",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})

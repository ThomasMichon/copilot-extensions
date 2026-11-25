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
    "test_reviewed_otlp_prerelease_pin_keeps_seasoning",
    "test_reviewed_otlp_pin_does_not_expand_to_other_declarations",
    "test_reviewed_otlp_pin_never_waives_artifact_age_or_yank_checks",
    "test_full_inventory_requirements_npm_and_vendor_exclusion",
    "test_github_action_manifest_inventoried_and_age_audited",
    "test_local_exemption_requires_contained_path_and_audits_closure",
    "test_arbitrary_sources_do_not_exempt",
    "test_name_alone_is_not_repository_source_attribution",
    "test_direct_urls_and_metadata_errors_are_sanitized",
    "test_public_metadata_cached_and_host_markers_not_filtered",
    "test_legacy_uv_dev_declarations_and_npm_audit",
    "test_vendored_path_source_requires_canonical_external_closure",
    "test_includes_cycle_and_escape_are_rejected",
    "test_plugin_source_contract_requires_exact_runner_and_canonical_identity",
    "test_explicit_application_source_requires_canonical_attributable_input",
    "test_declared_project_inventory_audits_dependencies_and_policy_without_core_roots",
    "test_declared_library_cannot_gain_application_source_audit_exemptions",
    "test_standalone_source_contract_revoked_if_paths_or_registration_disappear",
    "test_audit_parses_each_identical_source_contract_once",
    "test_source_evidence_is_reread_within_one_audit",
    "test_later_audit_observes_source_contract_revocation",
    "test_standalone_source_contract_follows_only_selected_core_mapping",
    "test_engine_source_contract_requires_both_platforms_and_canonical_parent",
    "test_unowned_project_copy_is_not_an_attributable_vendor",
    "test_python_proof_independent_of_npm_metadata_failure",
    "test_dynamic_and_unsupported_requirements_fail_closed",
    "test_npm_cli_respects_ca_registry_and_tls",
    "test_npm_insecure_environment_refused",
    "test_npm_metadata_transport_bounded_and_fail_closed",
    "test_npm_ca_transport_skips_urllib_only_for_authoritative_public_url",
    "test_npm_selector_reuses_node_and_attributable_cli_without_cmd_spawn",
    "test_plugin_contract_follows_only_invoked_canonical_preparation",
    "test_local_file_urls_only_exempt_attributable_canonical_sources",
    "test_new_uv_resolution_inputs_cannot_hide_unseasoned_literals",
    "test_unsupported_uv_resolution_input_shapes_fail_closed",
    "test_cli_exit_status_without_network",
    "test_native_policy_enforced_by_live_guard_without_network",
)
for _name in _SELECTED:
    globals()[_name] = getattr(_original, _name)

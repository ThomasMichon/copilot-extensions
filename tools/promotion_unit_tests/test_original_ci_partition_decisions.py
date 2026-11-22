"""Export original pure matrix and backend decisions without runner activation."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_ci_plugin_partitions.py"
_spec = importlib.util.spec_from_file_location("promotion_original_ci_partition_decisions", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

backend_row = _original.backend_row
backend_context = _original.backend_context
_SELECTED = (
    "test_matrix_partitions_only_declared_large_suites",
    "test_single_job_override_preserves_all_plugins_and_default_partition_policy",
    "test_invalid_single_job_override_refuses_instead_of_omitting_work",
    "test_invalid_matrix_refuses_instead_of_omitting_work",
    "test_exact_ready_pr_backend_preserves_the_existing_row",
    "test_non_pr_modes_cannot_withdraw_hosted_coverage",
    "test_unknown_or_untrusted_authority_defaults_hosted",
    "test_incomplete_or_mismatched_readiness_stays_hosted",
    "test_exact_windows_reserve_and_freshness_boundaries",
    "test_native_provider_requires_an_explicit_reserve_declaration_and_complete_readiness",
    "test_only_the_two_existing_pr_recipe_identities_are_eligible",
    "test_backend_selection_cannot_repair_or_repartition_an_invalid_row",
    "test_mismatched_existing_job_field_stays_hosted",
    "test_selection_is_pure_and_has_no_sticky_private_fallback",
    "test_no_reselection_or_green_hosted_fallback_after_execution_starts",
    "test_started_execution_cannot_fall_back_when_row_metadata_is_missing",
    "test_missing_execution_context_is_reasoned_hosted",
    "test_missing_row_identity_or_half_partition_defaults_hosted",
    "test_dispatch_partition_identity_cannot_be_omitted",
    "test_existing_unpartitioned_heavy_job_can_be_selected",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})

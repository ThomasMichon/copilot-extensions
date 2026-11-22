"""Export the unchanged original synthetic reproducer-boundary controls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_reproducer_lab_boundary.py"
_spec = importlib.util.spec_from_file_location("promotion_original_reproducer_static_boundary", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_static_positive_is_not_provisioning_or_capacity_proof",
    "test_invalid_declaration",
    "test_unknown_fields_cannot_supply_effects_or_authority",
    "test_budgets_are_finite_positive_bounded_integers",
    "test_exact_budget_ceiling_and_one_above",
    "test_malformed_declaration_shape",
    "test_diagnostics_do_not_echo_untrusted_payloads",
    "test_git_sha256_declaration",
    "test_unsafe_host_observation_is_rejected",
    "test_missing_required_observation_is_not_a_safe_default",
    "test_incomplete_inspection_never_succeeds",
    "test_mount_inventory_preserves_exact_experiment_scope",
    "test_environment_additions_and_duplicates_are_rejected",
    "test_resource_names_are_disjoint_between_experiments",
    "test_wrong_or_incomplete_physical_identity_is_rejected",
    "test_user_and_same_length_environment_tampering_are_rejected",
    "test_unsupported_quota_claim_cannot_clear_pending_measurement",
    "test_explicit_null_cap_add_is_distinct_from_missing_observation",
    "test_volatile_inventory_is_complete_and_exclusive",
    "test_volatile_flags_and_exact_byte_budgets",
    "test_missing_tmpfs_observation_is_not_an_unbounded_default",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})

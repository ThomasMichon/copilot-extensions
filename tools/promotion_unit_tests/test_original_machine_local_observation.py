"""Export whole original explicit-data local-observation contracts."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_root = Path(__file__).resolve().parents[2]
for _library in ("machine-transport", "remote-login-shell"):
    _source = str(_root / "libs" / _library / "src")
    if _source not in sys.path:
        sys.path.insert(0, _source)
_path = _root / "libs" / "machine-transport" / "tests" / "test_local_execution.py"
_spec = importlib.util.spec_from_file_location("promotion_original_machine_local_observation", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)
pytestmark = _original.pytestmark

_SELECTED = (
    "test_observation_cannot_override_a_conflicting_explicit_hostname",
    "test_physical_family_cannot_override_another_platforms_explicit_hostname",
    "test_local_context_does_not_require_enrollment",
    "test_observed_hostname_is_not_legacy_address_syntax",
    "test_unscoped_literal_host_key_does_not_reclassify_observed_hostname",
    "test_literal_native_and_guest_siblings_keep_the_observed_physical_name",
    "test_observed_parentheses_are_not_legacy_presentation_syntax",
    "test_legacy_presentation_registry_keys_produce_one_term_execution_names",
    "test_literal_hostname_projection_still_rejects_genuine_alias_collision",
    "test_canonicalized_presentation_key_cannot_shadow_another_entry",
    "test_explicit_canonical_key_is_not_presentation_syntax",
    "test_registered_aliases_preserve_the_literal_observed_host",
    "test_literal_host_alias_does_not_override_explicit_native_space",
    "test_derived_guest_does_not_claim_native_customization",
    "test_unscoped_guest_declaration_keeps_legacy_execution_key",
    "test_current_guest_can_use_physical_customization_without_native_authority",
    "test_guest_only_entry_does_not_require_optional_host_metadata",
    "test_execution_whitespace_is_normalized_before_collision_validation",
    "test_normalized_execution_checks_every_colliding_entry",
    "test_execution_normalization_keeps_original_customization_key",
    "test_registry_whitespace_cannot_change_a_literal_observed_identity",
    "test_different_physical_entries_cannot_share_a_canonicalized_execution_key",
    "test_structured_physical_names_are_not_execution_reference_tokens",
    "test_missing_customization_keeps_structured_physical_label_and_clean_local_key",
    "test_structured_physical_fallback_still_checks_execution_collisions",
    "test_execution_reference_tokens_reject_unclean_or_multiterm_names",
    "test_existing_clean_registered_tokens_are_not_reinterpreted",
    "test_same_platform_customizations_require_unambiguous_current_selection",
    "test_missing_sibling_physical_spelling_is_deterministic",
    "test_generated_guest_key_cannot_shadow_native_entry",
    "test_generated_guest_key_cannot_shadow_identity_labels",
    "test_unscoped_guest_sibling_precedes_unscoped_native_fallback",
    "test_unscoped_logical_key_is_not_a_conflicting_os_hostname_observation",
    "test_generated_execution_cannot_shadow_legacy_hostname_addresses",
    "test_selected_unscoped_keys_are_not_regenerated_from_metadata",
    "test_observed_compatibility_name_keeps_the_matching_customization",
    "test_invalid_current_names_refuse",
    "test_invalid_customizations_are_not_absence",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})

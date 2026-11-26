"""Export whole original explicit-data local-binding contracts."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_root = Path(__file__).resolve().parents[2]
for _library in ("machine-transport", "remote-login-shell"):
    _source = str(_root / "libs" / _library / "src")
    if _source not in sys.path:
        sys.path.insert(0, _source)
_path = _root / "libs" / "machine-transport" / "tests" / "test_local_execution_bindings.py"
_spec = importlib.util.spec_from_file_location("promotion_original_machine_local_bindings", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)
pytestmark = _original.pytestmark

_SELECTED = (
    "test_unmapped_literal_hostname_can_remain_the_local_binding",
    "test_configured_hostname_forms_retain_matching_friendly_customizations",
    "test_unique_display_binding_preserves_the_literal_observed_host",
    "test_unrelated_display_metadata_cannot_replace_an_established_local_binding",
    "test_locally_associated_display_binding_retains_matching_customization",
    "test_explicit_legacy_display_selector_can_resolve_without_hostname_metadata",
    "test_literal_display_name_cannot_shadow_an_explicit_identity",
    "test_literal_display_name_binding_must_be_unique",
    "test_configured_legacy_presentation_resolves_to_one_execution_term",
    "test_unrelated_legacy_display_like_key_does_not_require_local_enrollment",
    "test_existing_configured_binding_remains_distinct_from_observation",
    "test_configured_guest_does_not_require_optional_guest_entry",
    "test_configured_legacy_native_binding_selects_its_actual_guest",
    "test_missing_guest_alias_fallback_preserves_its_resolved_physical_family",
    "test_missing_guest_metadata_does_not_hide_ambiguous_physical_families",
    "test_inferred_local_binding_cannot_hide_a_conflicting_platform",
    "test_registered_literal_binding_precedes_inferred_platform",
    "test_exact_unscoped_display_binding_is_not_an_inferred_qualifier",
    "test_normalized_native_key_binding_keeps_original_customization_key",
    "test_normalized_key_keeps_precedence_over_a_conflicting_profile",
    "test_normalized_binding_refuses_multiple_equivalent_registry_keys",
    "test_guest_candidate_cannot_override_an_already_selected_physical_family",
    "test_unrelated_scoped_entry_does_not_remove_native_display_customization",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})

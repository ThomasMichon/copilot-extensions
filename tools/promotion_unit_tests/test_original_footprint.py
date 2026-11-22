"""Export original footprint-history witnesses without replacing their assertions."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_installer_engine_footprint.py"
_spec = importlib.util.spec_from_file_location("promotion_original_footprint", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_current_document_preserves_real_v2_effective_contract",
    "test_repeated_source_hash_refreshes_do_not_grow_the_contract",
    "test_semantic_changes_require_an_exact_deliberate_transition",
    "test_retirement_requires_explicit_preservation_not_an_arbitrary_target",
    "test_equivalent_legacy_effect_can_move_to_an_explicit_named_rule",
    "test_unrelated_producer_scope_is_not_retained_coverage",
    "test_retiring_a_layout_selects_fresh_without_discarding_the_incumbent",
    "test_unknown_or_malformed_incoming_semantics_are_refused",
    "test_exact_read_boundaries",
    "test_complete_history_uses_manifest_budget_without_expanding_marker_budget",
    "test_oversized_manifest_is_refused_before_decode",
    "test_source_manifest_does_not_inherit_the_installed_marker_budget",
    "test_recognition_is_read_only_and_never_discards_reset_inventory",
    "test_historical_match_is_not_compared_to_the_incoming_revision",
    "test_construction_plan_uses_fresh_without_rewriting_unusable_inputs",
    "test_recognized_replacement_reuses_the_same_qualified_target",
    "test_failed_fresh_integration_cannot_become_incremental_on_retry",
    "test_occupied_incomplete_fallback_selects_distinct_construction",
    "test_invalid_history_or_protection_is_rejected",
)
for _name in _SELECTED:
    assert _name not in globals(), f"duplicate original footprint control: {_name}"
    globals()[_name] = getattr(_original, _name)

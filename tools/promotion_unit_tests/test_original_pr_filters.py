"""Original pure filter parsing, matching and acquisition-planning contracts."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_root = Path(__file__).resolve().parents[2]
_library = _root / "libs" / "pr-filters"
if str(_library / "src") not in sys.path:
    sys.path.insert(0, str(_library / "src"))
_path = _library / "tests" / "test_filters.py"
_spec = importlib.util.spec_from_file_location("promotion_original_pr_filters", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_acquisition_admission_shares_bounded_cover_and_checks_residual_identities",
    "test_six_fields_share_exact_conjunction_and_driver_identity",
    "test_composition_preserves_unknown",
    "test_partial_group_membership_never_proves_negative",
    "test_read_only_driver_and_group_mappings_preserve_positive_evidence",
    "test_age_rechecks_clock_without_a_new_head_and_preserves_future_unknown",
    "test_identity_alternatives_and_explicit_tag_any",
    "test_numeric_overflow_preserves_unknown_age_and_rejects_now",
    "test_malformed_expressions_refuse",
    "test_scope_and_driver_capabilities_are_enforced_in_every_any_branch",
    "test_canonical_intent_is_detached_and_definition_revision_is_content_bound",
    "test_json_and_complexity_bounds_refuse_before_evaluation",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})

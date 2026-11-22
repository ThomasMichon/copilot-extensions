"""Export original promotion inventory and admission decisions."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_promotion_lifecycle.py"
_spec = importlib.util.spec_from_file_location("promotion_original_lifecycle_admission_units", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_complete_empty_inventory_is_the_only_new_generation_admission",
    "test_producing_run_terminal_state_cannot_release_an_open_pr",
    "test_only_exact_existing_candidate_can_be_reused_after_qualification",
    "test_nonpromotion_and_foreign_prs_cannot_claim_the_release_namespace",
    "test_unknown_same_repository_publisher_cannot_be_silently_ignored",
    "test_invalid_or_incomplete_inventory_never_defaults_to_idle",
    "test_invalid_page_shape_is_not_empty_inventory",
    "test_all_pages_are_read_before_admission",
    "test_bounded_pagination_refuses_instead_of_silently_truncating",
    "test_invalid_repository_refuses_before_any_api_call",
    "test_closing_an_earlier_pr_between_cursor_pages_cannot_hide_a_surviving_promotion",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})

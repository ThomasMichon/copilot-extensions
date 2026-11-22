"""Export original non-CLI promotion dispatch decisions."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_promotion_dispatch.py"
_spec = importlib.util.spec_from_file_location("promotion_original_dispatch_noncli_decision_units", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

_SELECTED = (
    "test_current_source_ci_uses_direct_run_evidence_without_index_lag",
    "test_terminal_promoter_reconciles_actual_merge_even_after_failed_confirmation",
    "test_failed_promoter_without_a_candidate_or_current_green_ci_cannot_retry",
    "test_successful_noop_without_a_merged_pr_cannot_create_a_dispatch_loop",
    "test_stale_completion_cannot_retry_a_newer_failed_generation",
    "test_closed_event_does_not_duplicate_another_generation_but_can_drain_its_own_retirement",
    "test_paused_or_already_current_source_has_no_duplicate_generation",
    "test_older_ci_completion_coalesces_to_the_current_successful_dev_source",
    "test_multiple_source_ci_runs_use_explicit_latest_run_order",
    "test_only_a_newer_admitted_generation_suppresses_a_delayed_terminal_wake",
    "test_resume_merge_queues_behind_a_finishing_paused_noop_without_recursing",
    "test_retirement_history_applies_to_every_automatic_wake_and_queued_admission",
    "test_automatic_source_rejects_invalid_input_without_api_calls",
    "test_deleted_fork_cannot_block_other_sources_but_owner_provenance_stays_strict",
    "test_exact_source_and_base_ownership_governs_every_automatic_wake",
    "test_deferred_manual_ci_can_supersede_failed_push_ci_without_another_push",
    "test_newer_failed_ci_still_blocks_an_older_successful_direct_event",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})

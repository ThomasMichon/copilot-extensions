"""Export original synthetic CI-watchdog cases and their unchanged fixture."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_ci_failure_watchdog.py"
_spec = importlib.util.spec_from_file_location("promotion_original_ci_watchdog", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

watchdog = _original.watchdog
_SELECTED = (
    "test_extract_failed_test_ids_finds_the_node_id",
    "test_extract_failed_test_ids_matches_a_timestamp_prefixed_line",
    "test_extract_failed_test_ids_dedupes_and_preserves_order",
    "test_extract_failed_test_ids_preserves_spaces_in_parametrized_ids",
    "test_extract_failed_test_ids_with_no_reason_suffix",
    "test_extract_failed_test_ids_reason_containing_a_hyphen_separator",
    "test_build_signatures_one_per_failing_test",
    "test_build_signatures_falls_back_to_whole_job_when_no_test_id",
    "test_extract_failed_test_ids_ignores_run_plugin_tests_wrapper_summary",
    "test_build_signatures_falls_back_to_whole_job_for_the_wrapper_summary_alone",
    "test_signature_key_fallback_is_stable_across_different_timestamps",
    "test_signature_key_is_stable_for_the_same_test_id",
    "test_signature_key_differs_for_different_test_ids",
    "test_signature_key_whole_job_incorporates_job_name",
    "test_signature_key_cross_job_same_test_id_produces_different_keys",
    "test_is_rate_limited_true_within_the_window",
    "test_is_rate_limited_false_outside_the_window",
    "test_existing_issue_raises_lookup_failed_on_a_nonzero_exit",
    "test_existing_issue_returns_none_on_a_clean_no_match",
    "test_existing_issue_returns_the_match",
    "test_fetch_job_log_passes_allow_escape_sequences",
    "test_fetch_job_log_falls_back_for_a_gh_version_without_the_flag",
    "test_fetch_job_log_strips_ansi_escape_sequences",
    "test_fetch_job_log_still_raises_lookup_failed_on_a_nonzero_exit",
    "test_process_signature_dry_run_never_shells_out",
    "test_process_signature_aborts_without_filing_when_lookup_fails",
    "test_process_signature_files_when_no_existing_issue",
    "test_file_issue_records_the_filed_issue_number",
    "test_comment_occurrence_never_records_a_filed_issue_number",
    "test_main_writes_filed_issue_numbers_to_github_output",
    "test_main_writes_nothing_to_github_output_when_no_issue_was_filed",
    "test_process_signature_skips_comment_when_rate_limited",
    "test_process_signature_comments_when_outside_rate_limit",
    "test_main_skips_control_jobs_and_reports_dry_run",
    "test_main_returns_zero_when_nothing_failed",
    "test_main_reports_a_timed_out_job_not_just_failure",
    "test_main_returns_nonzero_when_job_list_fetch_fails_in_file_issue_mode",
    "test_main_stays_zero_when_job_list_fetch_fails_in_dry_run_mode",
    "test_main_returns_nonzero_when_a_job_log_fetch_fails_in_file_issue_mode",
    "test_reverify_signature_returns_the_matching_signature",
    "test_reverify_signature_raises_when_no_job_matches",
    "test_reverify_signature_skips_control_jobs_and_non_reportable_conclusions",
    "test_reverify_signature_includes_every_artifact_promotion_failure",
    "test_reverify_signature_propagates_lookup_failed_from_jobs_fetch",
    "test_main_reverify_signature_mode_prints_json_and_ignores_other_flags",
    "test_main_reverify_signature_mode_returns_nonzero_on_mismatch",
    "test_main_requires_sha_when_not_reverifying",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})

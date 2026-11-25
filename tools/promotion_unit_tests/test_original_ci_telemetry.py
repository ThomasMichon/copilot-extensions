"""Export original pure telemetry cases and their unchanged module fixture."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

_path = Path(__file__).resolve().parents[1] / "test_ci_telemetry.py"
_spec = importlib.util.spec_from_file_location("promotion_original_ci_telemetry", _path)
assert _spec is not None and _spec.loader is not None
_original = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _original
_spec.loader.exec_module(_original)

telemetry = _original.telemetry
_SELECTED = (
    "test_compute_flaky_shas_marks_a_commit_that_failed_then_succeeded",
    "test_compute_flaky_shas_excludes_a_commit_that_only_ever_failed",
    "test_compute_flaky_shas_excludes_a_commit_that_only_ever_succeeded",
    "test_compute_blocking_impact_counts_runs_stalled_until_next_success",
    "test_compute_blocking_impact_a_trailing_failure_with_nothing_after_it_blocks_nothing",
    "test_compute_signature_stats_aggregates_occurrences_recovery_and_blocking",
    "test_render_report_known_noisy_nonblocking_checks_section_is_empty",
    "test_compute_flaky_shas_detects_a_same_run_rerun_recovery",
    "test_compute_blocking_impact_ignores_pr_sourced_runs",
    "test_compute_blocking_impact_does_not_double_count_a_same_run_failed_rerun",
    "test_compute_blocking_impact_a_same_run_successful_rerun_still_ends_the_interval",
)
globals().update({name: getattr(_original, name) for name in _SELECTED})

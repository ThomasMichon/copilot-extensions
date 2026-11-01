"""Collect the original fake owner controls without native integration cases."""

from __future__ import annotations

from tools import test_native_qualification_owner as original

for name in (
    "test_assignment_setup_crossing_work_deadline_cannot_release_readiness",
    "test_held_lifecycle_outcome_blocks_an_otherwise_settled_native_phase",
    "test_one_original_job_owns_receiver_and_settles_before_lifecycle_audit",
    "test_original_job_forced_drain_remains_nonpositive",
    "test_qualification_sink_is_one_shot_even_after_a_failed_setup",
    "test_reap_timeout_uses_only_remaining_original_whole_bound",
    "test_receiver_zero_exit_without_work_phase_is_not_positive",
):
    globals()[name] = getattr(original, name)

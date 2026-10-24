"""Run the exact capture portfolio with per-interpreter guards, without a capture.

Conftest admits source-only binding validation for this explicit test target.
These are the same test bodies and parametrizations used by the capture driver.
"""

from __future__ import annotations

from test_http47_capture_probes import (
    test_http47_capture_actual_observations as test_http47_capture_actual_observations,
)
from test_cold_drain import (
    test_cold_drain_default_client_automatic_and_strict_cutover_precedes_effects as
    test_cold_drain_default_client_automatic_and_strict_cutover_precedes_effects,
)
from test_cold_recovery_races import (
    workers as workers,
    test_startup_cold_outage_after_initial_scan_waits_for_predecessor_exit as
    test_startup_cold_outage_after_initial_scan_waits_for_predecessor_exit,
    test_heartbeat_partial_cold_replacement_exit_preserves_delivery_fence as
    test_heartbeat_partial_cold_replacement_exit_preserves_delivery_fence,
    test_cold_recovery_two_successors_exclude_before_native_spawn as
    test_cold_recovery_two_successors_exclude_before_native_spawn,
    test_cold_recovery_registration_crash_adopts_existing_host as
    test_cold_recovery_registration_crash_adopts_existing_host,
    test_cold_recovery_prepared_and_failed_reconcile_only_exact_exit as
    test_cold_recovery_prepared_and_failed_reconcile_only_exact_exit,
    test_cold_recovery_unknown_admission_or_launch_is_never_replayed as
    test_cold_recovery_unknown_admission_or_launch_is_never_replayed,
    test_cold_recovery_provider_failure_is_not_confirmation as
    test_cold_recovery_provider_failure_is_not_confirmation,
    test_cold_recovery_resync_routes_same_native_without_rebuild as
    test_cold_recovery_resync_routes_same_native_without_rebuild,
)

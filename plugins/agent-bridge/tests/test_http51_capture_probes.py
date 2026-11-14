"""Actual standard-send parser/ASGI/journal evidence, not native SDK proof."""

import json
import io
import os
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.parse import urlsplit

import pytest
import test_host_status as host_status
import test_host_status_remote as host_status_remote
import test_live_questions as live_question_controls
import test_runtime_serving as serving_controls
import test_live_inbox_runtime as inbox_controls
import test_native_send_admission as native_controls
import test_gc as gc_controls
from test_receiving_bundle import (
    test_actual_sdk_facade_loads_the_core_without_suite_services
    as test_actual_sdk_facade_loads_the_core_without_suite_services,
)
from test_plugin_readiness_extension import (
    test_core_extension_helper_contracts as test_core_extension_helper_contracts,
    test_readiness_and_existing_delivery_node_contracts
    as test_readiness_and_existing_delivery_node_contracts,
)
from test_native_send_unavailable import (
    test_real_cli_reports_unavailable_before_constructing_native_admission
    as test_real_cli_reports_unavailable_before_constructing_native_admission,
    test_other_commands_keep_existing_unavailable_client_behavior
    as test_other_commands_keep_existing_unavailable_client_behavior,
    test_transport_distinguishes_preflight_from_a_lost_post_response
    as test_transport_distinguishes_preflight_from_a_lost_post_response,
)
from test_worktree_source_evidence import (
    test_reporter_naming_overrides_stored_names_without_retargeting_agent
    as test_reporter_naming_overrides_stored_names_without_retargeting_agent,
    test_invalid_reporting_context_does_not_replace_last_good_inventory
    as test_invalid_reporting_context_does_not_replace_last_good_inventory,
    test_real_source_stdout_refresh_renames_and_withdraws_without_row_changes
    as test_real_source_stdout_refresh_renames_and_withdraws_without_row_changes,
)
from test_dispatch_origin_cli import (
    test_origin_cli_reaches_actual_keyed_owner_without_acquisition
    as test_origin_cli_reaches_actual_keyed_owner_without_acquisition,
    test_origin_creation_client_composes_required_selected_peer_capabilities
    as test_origin_creation_client_composes_required_selected_peer_capabilities,
    test_origin_creation_client_rechecks_after_cutover_without_baseline_replay
    as test_origin_creation_client_rechecks_after_cutover_without_baseline_replay,
    test_origin_creation_client_invalid_scope_refuses_before_peer_discovery
    as test_origin_creation_client_invalid_scope_refuses_before_peer_discovery,
    test_origin_cli_invalid_forms_refuse_before_connect
    as test_origin_cli_invalid_forms_refuse_before_connect,
)
from test_dispatch_cli_origin import (
    test_origin_client_negotiates_mode_capability_before_acquisition
    as test_origin_client_negotiates_mode_capability_before_acquisition,
    test_connected_cli_creation_persists_before_delivery_and_replays
    as test_connected_cli_creation_persists_before_delivery_and_replays,
    test_unconfirmed_first_save_keeps_pending_effect_without_prompt_or_relaunch
    as test_unconfirmed_first_save_keeps_pending_effect_without_prompt_or_relaunch,
    test_original_snapshot_survives_restart_heartbeat_alias_and_mutable_targeting
    as test_original_snapshot_survives_restart_heartbeat_alias_and_mutable_targeting,
    test_resume_into_existing_conversation_preserves_its_stamp_or_absence
    as test_resume_into_existing_conversation_preserves_its_stamp_or_absence,
    test_resume_gate_never_stamps_provisional_or_legacy_identity
    as test_resume_gate_never_stamps_provisional_or_legacy_identity,
    test_incoherent_origin_refuses_registration_without_claiming_reservation
    as test_incoherent_origin_refuses_registration_without_claiming_reservation,
    test_foreign_driver_cannot_claim_origin_and_failed_fold_rolls_back_first_save
    as test_foreign_driver_cannot_claim_origin_and_failed_fold_rolls_back_first_save,
    test_legacy_and_opaque_storage_remain_unattributed
    as test_legacy_and_opaque_storage_remain_unattributed,
    test_schema45_upgrade_preserves_legacy_absence_and_requires_writable_preparation
    as test_schema45_upgrade_preserves_legacy_absence_and_requires_writable_preparation,
    test_current_schema_without_origin_storage_is_not_silently_repaired
    as test_current_schema_without_origin_storage_is_not_silently_repaired,
)
from test_dispatch_session_origin import (
    boundary as boundary,
    singleton_boundary as singleton_boundary,
    test_origin_rejects_malformed_or_cross_assignment_evidence
    as test_origin_rejects_malformed_or_cross_assignment_evidence,
    test_origin_detaches_nested_input_and_legacy_request_digest_is_unchanged
    as test_origin_detaches_nested_input_and_legacy_request_digest_is_unchanged,
    test_acquisition_refuses_unqualified_modes_and_other_assignments
    as test_acquisition_refuses_unqualified_modes_and_other_assignments,
    test_acquisition_first_save_replay_restart_and_reassignment_keep_birth
    as test_acquisition_first_save_replay_restart_and_reassignment_keep_birth,
    test_legacy_acquisition_reuse_never_backfills_origin
    as test_legacy_acquisition_reuse_never_backfills_origin,
    test_keyed_http_creation_retains_origin_and_rejects_changed_or_dropped_stamp
    as test_keyed_http_creation_retains_origin_and_rejects_changed_or_dropped_stamp,
    test_public_origin_requires_keyed_prepared_workspace
    as test_public_origin_requires_keyed_prepared_workspace,
    test_unsupported_target_refuses_before_worktree_effects
    as test_unsupported_target_refuses_before_worktree_effects,
    test_acquisition_venue_refusal_is_terminal_before_launch_gating
    as test_acquisition_venue_refusal_is_terminal_before_launch_gating,
    test_worktree_resume_responses_keep_known_origin
    as test_worktree_resume_responses_keep_known_origin,
    test_real_resolver_refuses_elevation_before_relay_start
    as test_real_resolver_refuses_elevation_before_relay_start,
)
from fastapi.testclient import TestClient

host_status_api = host_status.api
host_status_provider = host_status_remote.provider
api = live_question_controls.api
captured = serving_controls.captured
installation = inbox_controls.installation
serving = inbox_controls.serving
native_send = native_controls.native_send


@pytest.mark.parametrize("status", ["stopped", "ended"])
@pytest.mark.parametrize("state", [
    "preparing", "invoking", "unknown", "complete", "not-started", "malformed", "gate-only",
])
def test_retained_recovery_gc_preserves_original_inspection_handles(tmp_db, status, state):
    gc_controls.TestDbGcPrimitives().test_retained_recovery_survives_selection_deletion_startup_and_periodic_gc(
        tmp_db, status, state,
    )


@pytest.mark.parametrize("state", ["preparing", "unknown"])
def test_successor_recovery_journal_retains_original_handle(tmp_db, tmp_path, state):
    gc_controls.TestDbGcPrimitives().test_successor_recovery_journal_retains_original_handle(
        tmp_db, tmp_path, state,
    )


@pytest.mark.parametrize("query", ["retained", "eligible"])
@pytest.mark.parametrize("pending", [False, True])
def test_retention_query_work_scales_with_history(tmp_db, query, pending):
    gc_controls.TestDbGcPrimitives().test_retention_query_work_scales_with_history(tmp_db, query, pending)


@pytest.mark.parametrize("state", ["complete", "not-started"])
@pytest.mark.parametrize("malformed", [
    "missing-request", "extra-field", "invalid-request", "different-operation",
])
def test_malformed_terminal_recovery_retains_original_and_successor(tmp_db, state, malformed):
    gc_controls.TestDbGcPrimitives().test_malformed_terminal_recovery_retains_original_and_successor(
        tmp_db, state, malformed,
    )


def test_existing_v44_database_prepares_retained_recovery_without_rewriting_guards(tmp_db):
    gc_controls.TestDbGcPrimitives().test_existing_v44_database_prepares_retained_recovery_without_rewriting_guards(
        tmp_db,
    )


def test_host_status_coalesced_flow(host_status_api, tmp_db):
    host_status.test_real_http_inbox_consumer_gets_one_notice_and_consumption_is_not_ack(host_status_api, tmp_db)


def test_host_status_shared_host_stream(tmp_db):
    host_status_remote.test_two_watchers_and_two_aliases_share_one_stream_and_resume_after_restart(tmp_db)


def test_host_status_prior_and_rollback_refusal(host_status_provider):
    for capability in (None, False, 0, 2, "1"):
        host_status_remote.test_prior_missing_invalid_and_rollback_provider_refuse_before_stream(
            host_status_provider, capability,
        )


def test_host_status_legacy_timeout_compatibility(host_status_api, monkeypatch):
    host_status.test_status_runtime_handles_distinct_legacy_asyncio_timeout(host_status_api, monkeypatch)

import bridge_http52_evidence as evidence
from test_execution_context import (
    test_rename_is_visible_on_next_request_without_restarting_or_rewriting_records
    as test_rename_is_visible_on_next_request_without_restarting_or_rewriting_records,
    test_standalone_uses_shared_unmapped_host_semantics
    as test_standalone_uses_shared_unmapped_host_semantics,
    test_unavailable_or_ambiguous_config_never_uses_the_daemons_cached_roster
    as test_unavailable_or_ambiguous_config_never_uses_the_daemons_cached_roster,
    test_current_context_route_retains_bridge_authentication
    as test_current_context_route_retains_bridge_authentication,
    test_explicit_hostname_conflict_is_not_resolved_by_the_machine_key
    as test_explicit_hostname_conflict_is_not_resolved_by_the_machine_key,
    test_legacy_profiles_without_ssh_aliases_keep_native_guest_naming
    as test_legacy_profiles_without_ssh_aliases_keep_native_guest_naming,
    test_machine_rename_rollover_retains_alias_and_cli_claim
    as test_machine_rename_rollover_retains_alias_and_cli_claim,
)
from bridge_http_retained_probes import capture_request, record_observations
from http51_receipt_probes import observe as observe_receipt
from http51_live_cli_probes import observe as observe_live_cli


def test_execution_context_extension_controls():
    from agent_procutil.owned_process import run_caller_owned

    node = shutil.which("node")
    assert node is not None, "execution-context capture requires Node"
    tests = Path(__file__).parent
    result = run_caller_owned(
        [node, "--test", str(tests / "metadata.test.mjs"), str(tests / "bridge-endpoint.test.mjs"),
         str(tests / "delivery.test.mjs")],
        timeout=60, max_stdout_bytes=256 * 1024, max_stderr_bytes=64 * 1024,
    )
    assert result.returncode == 0, result.stdout + result.stderr


from test_live_delivery_state import (
    delivery_api as delivery_api,
    test_accepted_behind_question_is_explicit_not_failed_and_keyed_retry_is_one_row
    as test_accepted_behind_question_is_explicit_not_failed_and_keyed_retry_is_one_row,
    test_consumption_releases_wait_without_turn_end_or_reply
    as test_consumption_releases_wait_without_turn_end_or_reply,
    test_missing_partial_stale_or_reincarnated_observer_is_unknown
    as test_missing_partial_stale_or_reincarnated_observer_is_unknown,
    test_receipt_loss_after_admission_cannot_be_reported_as_refused
    as test_receipt_loss_after_admission_cannot_be_reported_as_refused,
    test_invalid_wait_refuses_before_admission as test_invalid_wait_refuses_before_admission,
    test_combined_reply_or_startup_wait_is_refused_before_admission
    as test_combined_reply_or_startup_wait_is_refused_before_admission,
    test_current_client_refuses_unsupported_provider_before_send
    as test_current_client_refuses_unsupported_provider_before_send,
    test_invalid_cli_timeout_is_parser_error_before_client_creation
    as test_invalid_cli_timeout_is_parser_error_before_client_creation,
    test_observation_rejects_duplicate_categories as test_observation_rejects_duplicate_categories,
    test_real_cli_parser_send_and_existing_receipt_share_one_http_ledger
    as test_real_cli_parser_send_and_existing_receipt_share_one_http_ledger,
    test_module_cli_observes_over_loopback_without_starting_service
    as test_module_cli_observes_over_loopback_without_starting_service,
    test_extension_pending_snapshot_and_capability_wire
    as test_extension_pending_snapshot_and_capability_wire,
)
from test_mux_questions import (
    mux_api as mux_api,
    test_http_choice_and_freeform_are_verified_audited_and_single_use
    as test_http_choice_and_freeform_are_verified_audited_and_single_use,
    test_terminal_first_and_changed_inputs_never_send_keys
    as test_terminal_first_and_changed_inputs_never_send_keys,
    test_restart_expiry_and_other_session_tokens_have_no_input_authority
    as test_restart_expiry_and_other_session_tokens_have_no_input_authority,
    test_key_failure_is_unknown_and_blocks_reinspection_after_restart
    as test_key_failure_is_unknown_and_blocks_reinspection_after_restart,
    test_pane_replacement_after_navigation_stops_before_enter
    as test_pane_replacement_after_navigation_stops_before_enter,
    test_preissued_inspection_cannot_bypass_an_uncertain_sibling_attempt
    as test_preissued_inspection_cannot_bypass_an_uncertain_sibling_attempt,
    test_same_screen_has_one_admission_even_with_two_inspection_tokens
    as test_same_screen_has_one_admission_even_with_two_inspection_tokens,
    test_authentication_and_malformed_permission_mixed_answers_have_no_effect
    as test_authentication_and_malformed_permission_mixed_answers_have_no_effect,
    test_native_answering_and_multifield_or_permission_evidence_prevent_fallback
    as test_native_answering_and_multifield_or_permission_evidence_prevent_fallback,
    test_ambiguous_rendering_never_parses_as_an_answerable_form
    as test_ambiguous_rendering_never_parses_as_an_answerable_form,
    test_completion_requires_a_new_exact_answer_echo_not_old_history
    as test_completion_requires_a_new_exact_answer_echo_not_old_history,
    test_completion_accepts_only_right_aligned_native_duration_annotations
    as test_completion_accepts_only_right_aligned_native_duration_annotations,
    test_only_known_native_decorations_are_volatile
    as test_only_known_native_decorations_are_volatile,
    test_http_clock_ticks_preserve_the_same_one_use_inspected_screen
    as test_http_clock_ticks_preserve_the_same_one_use_inspected_screen,
)
from test_mux_question_client import (
    test_backend_uses_selected_owner_and_exact_confirmed_native_pane
    as test_backend_uses_selected_owner_and_exact_confirmed_native_pane,
    test_old_provider_never_receives_mux_mutation_and_writes_are_not_retried
    as test_old_provider_never_receives_mux_mutation_and_writes_are_not_retried,
    test_cli_mux_does_not_start_a_daemon_or_touch_native_or_permission_paths
    as test_cli_mux_does_not_start_a_daemon_or_touch_native_or_permission_paths,
)
from test_db_adoption import (
    test_mux_storage_requires_writable_preparation_before_passive_adoption
    as test_mux_storage_requires_writable_preparation_before_passive_adoption,
)
from test_runtime_serving import (
    test_foreground_serves_real_authenticated_routes_only_after_fenced_commit
    as test_foreground_serves_real_authenticated_routes_only_after_fenced_commit,
)
from test_live_questions import (
    test_live_question_provider_advertises_actual_capability
    as test_live_question_provider_advertises_actual_capability,
    test_passive_native_correlation_is_exact_and_charged_to_its_budget
    as test_passive_native_correlation_is_exact_and_charged_to_its_budget,
    test_current_native_roundtrip_and_durable_outcome
    as test_current_native_roundtrip_and_durable_outcome,
    test_authentication_mismatch_and_single_use_refusals
    as test_authentication_mismatch_and_single_use_refusals,
    test_terminal_completion_restart_and_process_replacement_revoke_observation
    as test_terminal_completion_restart_and_process_replacement_revoke_observation,
    test_question_answers_do_not_follow_session_aliases
    as test_question_answers_do_not_follow_session_aliases,
    test_native_rejection_and_uncertainty_are_not_acceptance
    as test_native_rejection_and_uncertainty_are_not_acceptance,
    test_unclaimed_timeout_withdraws_and_missing_required_field_refuses
    as test_unclaimed_timeout_withdraws_and_missing_required_field_refuses,
    test_old_provider_refuses_before_mutation_and_new_provider_gets_pinned_shape
    as test_old_provider_refuses_before_mutation_and_new_provider_gets_pinned_shape,
    test_cli_live_branch_preserves_owned_answer_and_permission_boundaries
    as test_cli_live_branch_preserves_owned_answer_and_permission_boundaries,
)
from test_live_exact_rollback import (
    test_interrupted_pre30_upgrade_reopens_every_legacy_guard_checkpoint
    as test_interrupted_pre30_upgrade_reopens_every_legacy_guard_checkpoint,
)
from agent_bridge import __main__ as core
from agent_bridge.models import SessionStatus
from agent_bridge.protocol import HTTP_PROTOCOL_VERSION
from agent_bridge.client import BridgeClient
from agent_bridge.events import EventLog
from agent_bridge.session_manager import Session
from agent_bridge.transport import SpawnTarget
from test_native_send_admission import _command
from test_native_send_admission import (
    test_concurrent_stopped_retries_reconcile_after_actual_resume_state_race
    as test_concurrent_stopped_retries_reconcile_after_actual_resume_state_race,
    test_failed_resume_without_a_matching_receipt_remains_a_failure
    as test_failed_resume_without_a_matching_receipt_remains_a_failure,
)
from test_session_observations import (
    source as source,
    test_contribution_projection_retains_only_bounded_reported_names
    as test_contribution_projection_retains_only_bounded_reported_names,
    test_observation_cancellation_preserves_other_subscription
    as test_observation_cancellation_preserves_other_subscription,
    test_observation_http_resource_boundary as test_observation_http_resource_boundary,
    test_live_waiters_change_without_changing_event_positions
    as test_live_waiters_change_without_changing_event_positions,
    test_waiter_capture_before_wait_race_does_not_wait_for_backstop
    as test_waiter_capture_before_wait_race_does_not_wait_for_backstop,
    test_live_qualification_replacement_terminal_and_reader_cleanup
    as test_live_qualification_replacement_terminal_and_reader_cleanup,
    test_replaced_same_log_handle_keeps_exact_subscriber_and_producer_custody
    as test_replaced_same_log_handle_keeps_exact_subscriber_and_producer_custody,
    test_optional_block_reserves_space_before_event_tail_and_preserves_page_ends
    as test_optional_block_reserves_space_before_event_tail_and_preserves_page_ends,
    test_closing_one_current_follower_preserves_other_follower_push
    as test_closing_one_current_follower_preserves_other_follower_push,
    test_statistics_cover_owner_range_not_page_or_repeated_delivery
    as test_statistics_cover_owner_range_not_page_or_repeated_delivery,
    test_statistics_empty_zero_and_ancillary_failure_keep_source_records
    as test_statistics_empty_zero_and_ancillary_failure_keep_source_records,
    test_statistics_atomic_cut_and_captured_gap_do_not_claim_full_history
    as test_statistics_atomic_cut_and_captured_gap_do_not_claim_full_history,
    test_statistics_large_range_uses_only_bounded_event_projection
    as test_statistics_large_range_uses_only_bounded_event_projection,
    test_statistics_only_qualification_change_pushes_on_existing_follower
    as test_statistics_only_qualification_change_pushes_on_existing_follower,
    test_statistics_parser_rejects_malformed_or_cross_cut_claims
    as test_statistics_parser_rejects_malformed_or_cross_cut_claims,
)
from test_session_observation_transport import (
    endpoint as endpoint,
    test_observer_cli_http_eof as test_observer_cli_http_eof,
    test_actual_local_callback_future_reaches_http_without_history_inference
    as test_actual_local_callback_future_reaches_http_without_history_inference,
    test_optional_wire_is_explicit_and_never_added_to_old_or_history_reads
    as test_optional_wire_is_explicit_and_never_added_to_old_or_history_reads,
    test_optional_cli_negotiates_actual_provider_before_sending_new_query
    as test_optional_cli_negotiates_actual_provider_before_sending_new_query,
    test_actual_cli_optional_parser_declaration_and_http_frame_join
    as test_actual_cli_optional_parser_declaration_and_http_frame_join,
    test_statistics_wire_is_optional_and_independent_of_current_requests
    as test_statistics_wire_is_optional_and_independent_of_current_requests,
    test_statistics_cli_checks_live_support_without_losing_supported_waiters
    as test_statistics_cli_checks_live_support_without_losing_supported_waiters,
    test_statistics_actual_cli_parser_http_join_and_strict_legacy
    as test_statistics_actual_cli_parser_http_join_and_strict_legacy,
)
from test_owner_membership import (
    test_adopted_writable_connection_preserves_membership_commits
    as _adopted_writable_connection_preserves_membership_commits,
)
from test_owner_membership_budget import (
    test_selected_owner_capture_does_not_scan_unrelated_registries
    as test_selected_owner_capture_does_not_scan_unrelated_registries,
    test_only_writable_bootstrap_prepares_lookup_indexes
    as test_only_writable_bootstrap_prepares_lookup_indexes,
    test_private_deadline_exhaustion_is_a_typed_gap_without_wallclock_sleep
    as test_private_deadline_exhaustion_is_a_typed_gap_without_wallclock_sleep,
    test_private_deadline_uses_precise_elapsed_time_not_coarse_ticks
    as test_private_deadline_uses_precise_elapsed_time_not_coarse_ticks,
    test_unknown_owner_large_history_has_actual_private_vm_cutoff
    as test_unknown_owner_large_history_has_actual_private_vm_cutoff,
)
from test_inbox_consumer_enrollment import (
    enrolled as enrolled, portable as portable,
    test_portable_scope_never_leaks_a_seed_to_the_legacy_inbox_before_enrollment
    as test_portable_scope_never_leaks_a_seed_to_the_legacy_inbox_before_enrollment,
    test_portable_reload_keeps_its_native_binding_and_uses_the_existing_cas
    as test_portable_reload_keeps_its_native_binding_and_uses_the_existing_cas,
    test_remote_enrollment_requires_the_original_portable_reservation
    as test_remote_enrollment_requires_the_original_portable_reservation,
)
from test_live_inbox_producer import (
    test_retained_startup_admission_requires_its_original_ready_input
    as test_retained_startup_admission_requires_its_original_ready_input,
)
from test_plugin_readiness import (
    test_readonly_poll_never_claims_or_expires_mutation_controls
    as test_readonly_poll_never_claims_or_expires_mutation_controls,
)
from test_portable_mux_joined import (
    test_bridge_provider_receiver_and_extensions_share_one_conversation
    as test_bridge_provider_receiver_and_extensions_share_one_conversation,
    test_carrier_failures_settle_owned_tree_and_streams
    as test_carrier_failures_settle_owned_tree_and_streams,
)
from test_receiving_bundle import (
    test_provider_owner_uses_the_actual_client_interface
    as test_provider_owner_uses_the_actual_client_interface,
)
from test_worktree_writer_observation import (
    test_health_observes_canonical_writer_selection_without_launch
    as test_health_observes_canonical_writer_selection_without_launch,
    test_repeated_health_reuses_real_resolver_but_refreshes_selected_slot
    as test_repeated_health_reuses_real_resolver_but_refreshes_selected_slot,
    test_repeated_health_retains_packaged_validator_but_rechecks_cell_receipts
    as test_repeated_health_retains_packaged_validator_but_rechecks_cell_receipts,
    test_unknown_or_changed_slot_never_certifies_a_build
    as test_unknown_or_changed_slot_never_certifies_a_build,
    test_same_cell_health_rechecks_peer_selection_without_launch
    as test_same_cell_health_rechecks_peer_selection_without_launch,
)
from test_acquisition_runtime import (
    rig as rig, writer_rig as writer_rig,
    test_required_writer_round_trips_through_the_existing_http_owner
    as test_required_writer_round_trips_through_the_existing_http_owner,
    test_required_writer_survives_stopped_resume_and_later_acquisition
    as test_required_writer_survives_stopped_resume_and_later_acquisition,
    test_required_handoff_retry_reuses_original_successor_after_lineage_failure
    as test_required_handoff_retry_reuses_original_successor_after_lineage_failure,
    test_required_handoff_retains_unknown_effect_without_replacement
    as test_required_handoff_retains_unknown_effect_without_replacement,
    test_required_writer_refuses_provider_type_change_before_persist_or_launch
    as test_required_writer_refuses_provider_type_change_before_persist_or_launch,
    test_required_writer_resume_exhaustion_cannot_recreate
    as test_required_writer_resume_exhaustion_cannot_recreate,
    test_required_handoff_retries_registration_on_original_native
    as test_required_handoff_retries_registration_on_original_native,
    test_required_registration_failure_detaches_before_normal_send
    as test_required_registration_failure_detaches_before_normal_send,
    test_required_queued_send_refusal_keeps_startup_recovery_dormant
    as test_required_queued_send_refusal_keeps_startup_recovery_dormant,
    test_required_writer_refuses_before_native_effects
    as test_required_writer_refuses_before_native_effects,
    test_retained_acquisition_and_later_lineage_use_actual_owner_processes
    as test_retained_acquisition_and_later_lineage_use_actual_owner_processes,
)
from test_worktree_lineage_authority import (
    test_retained_session_receiver_refuses_changed_scope_without_resident_fallback
    as test_retained_session_receiver_refuses_changed_scope_without_resident_fallback,
)
from test_acquisition_writer_inspection import (
    rig as _inspection_fixture,
    test_cli_negotiates_and_round_trips_optional_inspection_through_http as _inspect_cli_http,
    test_unavailable_retained_command_is_a_structured_read_only_refusal as _inspect_unavailable,
)
from test_full_session_status_cli import (
    test_writer_status_requires_full_exact_session_before_discovery
    as test_writer_status_requires_full_exact_session_before_discovery,
    test_writer_status_uses_negotiated_original_observation
    as test_writer_status_uses_negotiated_original_observation,
)
from test_creation_worktree_writer import (
    test_ordinary_resume_preserves_completed_recovery_permission_hold
    as test_ordinary_resume_preserves_completed_recovery_permission_hold,
    test_retained_recovery_resumes_same_native_once_without_prompt_drain
    as test_retained_recovery_resumes_same_native_once_without_prompt_drain,
    test_uncertain_retained_recovery_never_relaunches_or_falls_back
    as test_uncertain_retained_recovery_never_relaunches_or_falls_back,
    test_retained_recovery_round_trips_through_actual_http_native_owner
    as test_retained_recovery_round_trips_through_actual_http_native_owner,
    test_native_stop_race_retains_hold_and_records_no_native_entry
    as test_native_stop_race_retains_hold_and_records_no_native_entry,
    test_retained_recovery_denies_unconfirmed_native_owners_before_invocation
    as test_retained_recovery_denies_unconfirmed_native_owners_before_invocation,
    test_retained_recovery_cli_negotiates_and_never_replays_native_request
    as test_retained_recovery_cli_negotiates_and_never_replays_native_request,
    test_terminal_creation_writer_remains_inspectable_without_reopening
    as test_terminal_creation_writer_remains_inspectable_without_reopening,
    test_required_creation_resume_refuses_changed_custody_before_effect
    as test_required_creation_resume_refuses_changed_custody_before_effect,
    test_required_creation_writer_cli_carries_requirement_to_actual_client
    as test_required_creation_writer_cli_carries_requirement_to_actual_client,
    test_required_creation_writer_cli_invalid_input_refuses_before_connect
    as test_required_creation_writer_cli_invalid_input_refuses_before_connect,
    test_required_creation_writer_cli_rejects_remote_before_connect
    as test_required_creation_writer_cli_rejects_remote_before_connect,
    creation_rig as creation_rig, writer_creation as writer_creation,
    test_creation_writer_preparation_preserves_unpinned_rows_and_readonly_refuses
    as test_creation_writer_preparation_preserves_unpinned_rows_and_readonly_refuses,
    test_keyed_creation_writer_retains_original_scope_and_strict_resume
    as test_keyed_creation_writer_retains_original_scope_and_strict_resume,
    test_keyed_creation_writer_rehydrates_same_native_without_reselection
    as test_keyed_creation_writer_rehydrates_same_native_without_reselection,
    test_keyed_creation_writer_concurrent_replay_and_changed_key_do_not_reallocate
    as test_keyed_creation_writer_concurrent_replay_and_changed_key_do_not_reallocate,
    test_keyed_creation_writer_reconciles_lost_completion_without_new_native
    as test_keyed_creation_writer_reconciles_lost_completion_without_new_native,
    test_keyed_creation_writer_refuses_changed_custody_before_effects
    as test_keyed_creation_writer_refuses_changed_custody_before_effects,
    test_keyed_creation_writer_cannot_retrofit_or_replace_original_journal
    as test_keyed_creation_writer_cannot_retrofit_or_replace_original_journal,
    test_required_creation_writer_parser_rejects_unsupported_forms
    as test_required_creation_writer_parser_rejects_unsupported_forms,
    test_required_creation_writer_client_refuses_unsupported_active_provider
    as test_required_creation_writer_client_refuses_unsupported_active_provider,
    test_required_creation_writer_rechecks_capability_after_provider_cutover
    as test_required_creation_writer_rechecks_capability_after_provider_cutover,
    test_required_creation_writer_round_trips_through_actual_http_owner
    as test_required_creation_writer_round_trips_through_actual_http_owner,
    test_creation_writer_inspection_refuses_changed_owner_without_effects
    as test_creation_writer_inspection_refuses_changed_owner_without_effects,
    test_required_creation_writer_inspection_rejects_ignored_legacy_query
    as test_required_creation_writer_inspection_rejects_ignored_legacy_query,
    test_keyed_creation_handoff_retains_original_successor_and_seed
    as test_keyed_creation_handoff_retains_original_successor_and_seed,
    test_keyed_creation_registration_failure_retains_native_and_dormancy
    as test_keyed_creation_registration_failure_retains_native_and_dormancy,
    test_keyed_creation_uses_actual_retained_serializer_and_preserves_claim
    as test_keyed_creation_uses_actual_retained_serializer_and_preserves_claim,
)
from test_retained_host_recovery import (
    test_real_host_boundary_retains_stop_and_uncertain_custody
    as test_real_host_boundary_retains_stop_and_uncertain_custody,
    test_retained_recovery_cannot_enter_while_exact_session_end_is_active
    as test_retained_recovery_cannot_enter_while_exact_session_end_is_active,
)

from test_creation_writer_resume import (
    test_required_resume_real_parser_client_rechecks_provider
    as test_required_resume_real_parser_client_rechecks_provider,
    test_required_resume_invalid_form_refuses_before_discovery
    as test_required_resume_invalid_form_refuses_before_discovery,
    test_required_resume_worktree_selector_refuses_before_discovery
    as test_required_resume_worktree_selector_refuses_before_discovery,
    test_required_resume_cannot_fallback_or_accept_replacement
    as test_required_resume_cannot_fallback_or_accept_replacement,
    test_required_resume_pins_capability_peer_during_observer_cutover
    as test_required_resume_pins_capability_peer_during_observer_cutover,
)

ROOT = Path(__file__).resolve().parents[3]
inspection_rig = _inspection_fixture


@pytest.mark.parametrize("supported", ["current", "prior", "rollback"])
def test_retained_writer_inspection_cli_http(inspection_rig, monkeypatch, capsys, supported):
    _inspect_cli_http(inspection_rig, monkeypatch, capsys, supported)


@pytest.mark.parametrize("operation", ["resource-relationship", "record"])
@pytest.mark.parametrize("failure", [FileNotFoundError, PermissionError])
def test_retained_writer_inspection_unavailable_http(inspection_rig, monkeypatch, operation, failure):
    _inspect_unavailable(inspection_rig, monkeypatch, operation, failure)


def _membership_transcript_clock(monkeypatch):
    from agent_bridge import owner_membership_read as reader

    # Transcript assertions use a virtual reader clock, not a host scheduling guarantee.
    # Separate probes exercise deadline expiry and actual SQLite VM exhaustion.
    budget_type = reader.ReadBudget
    monkeypatch.setattr(reader, "time", SimpleNamespace(
        monotonic=lambda: 0.0, perf_counter=lambda: 0.0,
    ))
    monkeypatch.setattr(reader, "ReadBudget", lambda: budget_type(started=0.0))


def test_adopted_writable_connection_preserves_membership_commits(tmp_path, monkeypatch):
    _membership_transcript_clock(monkeypatch)
    _adopted_writable_connection_preserves_membership_commits(tmp_path)


def test_http51_capture_actual_observations(
    serving, native_send, request, monkeypatch, capsys,
) -> None:
    bound = capture_request(evidence, ROOT)
    receipt_client_factory = core._get_client
    assert HTTP_PROTOCOL_VERSION == evidence.GENERATION
    vector = json.loads(evidence.safe(ROOT, evidence.INPUT).read_bytes())
    app, client, db, _ = serving
    manager, session, original = native_send
    app.state.session_manager = manager
    native_send = manager, session, original.model_copy(update={"prompt": vector["requests"]["prompt"]})
    # Keep ordinary middleware/installation binding while allowing an actual HTTP 500 response.
    api = TestClient(app, headers=client.headers, raise_server_exceptions=False)
    assert api.get("/health").json()["capabilities"]["bridge-session-tool-statistics-v1"] == 1
    observed = {"advertised": {
        key: api.get("/health").json()[key]
        for key in ("protocol_version", "min_protocol_version")
    }}
    health = api.get("/health").json()
    observed["live_question_answers"] = health["capabilities"]["live_question_answers"]
    observed["mux_question_answers"] = health["capabilities"]["mux_question_answers"]
    observed["dispatch_acp_session_origin"] = health["capabilities"]["dispatch_acp_session_origin"]
    observed["dispatch_cli_session_origin"] = health["capabilities"]["dispatch_cli_session_origin"]
    observed["portable_capabilities"] = {
        key: health["capabilities"][key] for key in vector["expected"]["portable_capabilities"]
    }
    observed["worktree_record_delegation"] = health["capabilities"]["worktree_record_delegation"]
    observed["retained_acquisition_worktree_writer"] = (
        health["capabilities"]["retained_acquisition_worktree_writer"]
    )
    observed["retained_acquisition_writer_inspection"] = (
        health["capabilities"]["retained_acquisition_writer_inspection"]
    )
    observed["retained_workspace_creation_writer"] = (
        health["capabilities"]["retained_workspace_creation_writer"]
    )
    observed["retained_workspace_creation_writer_inspection"] = (
        health["capabilities"]["retained_workspace_creation_writer_inspection"]
    )
    observed["retained_native_session_recovery"] = health["capabilities"]["retained_native_session_recovery"]
    assert set(health["worktree_record_writer"]) == {"installation_root", "runtime_generation"}
    args, _ = _command(native_send, monkeypatch, api=api)
    args.func(args)
    receipt = json.loads(capsys.readouterr().out)
    observed["receipt"] = {key: receipt[key] for key in vector["expected"]["receipt"]}
    args.func(args)
    observed["same_retry"] = json.loads(capsys.readouterr().out) == receipt
    path = "/api/v1/sessions/bridge-a/turns/native-bound/send"
    body = native_send[2].model_dump()
    observed["changed_input"] = api.post(path, json={**body, "prompt": "changed"}).status_code
    observed["wrong_native"] = api.post(path, json={
        **body, "expected_native_session_id": "replacement",
    }).status_code
    session.status = SessionStatus.RUNNING
    observed["busy"] = api.post(path, json={**body, "invocation_key": "busy"}).status_code
    old, calls = _command(native_send, monkeypatch, generation=50, api=api)
    with pytest.raises(SystemExit) as refused:
        old.func(old)
    result = json.loads(capsys.readouterr().out)
    observed["old_receiver"] = {
        "exit": refused.value.code, "outcome": result["outcome"], "posts": len(calls),
    }

    session.status = SessionStatus.IDLE
    saved_update = manager.db.update_session_status

    def fail_after_commit(*args, **kwargs):
        raise RuntimeError("synthetic response failure after journal commit")

    lost, _ = _command(native_send, monkeypatch, api=api)
    lost.idempotency_key = "lost-reply"
    monkeypatch.setattr(manager.db, "update_session_status", fail_after_commit)
    with pytest.raises(SystemExit) as unknown:
        lost.func(lost)
    result = json.loads(capsys.readouterr().out)
    observed["post_commit_failure"] = {
        "exit": unknown.value.code,
        **{key: result[key] for key in ("outcome", "reason", "idempotency_key")},
    }
    monkeypatch.setattr(manager.db, "update_session_status", saved_update)
    lost.func(lost)
    observed["post_commit_failure"].update({
        "recovered_turn": json.loads(capsys.readouterr().out)["turn_index"],
        "turns": len(db.get_turns(session.session_id)),
    })
    malformed = [True]
    invalid, _ = _command(
        native_send, monkeypatch, api=api,
        transform=lambda value: {} if malformed[0] else value,
    )
    with pytest.raises(SystemExit) as unknown:
        invalid.func(invalid)
    result = json.loads(capsys.readouterr().out)
    malformed[0] = False
    invalid.func(invalid)
    observed["malformed_receipt"] = {
        "exit": unknown.value.code, "outcome": result["outcome"],
        "recovered_turn": json.loads(capsys.readouterr().out)["turn_index"],
        "turns": len(db.get_turns(session.session_id)),
    }
    event, = [
        event for event in session.event_log.get_events(0) if event.event == "user_message"
    ]
    observed["caller_provenance"] = event.data
    observed["no_inbox"] = db.execute_read("SELECT * FROM live_messages") == []
    observed["receipt_only"] = observe_receipt(
        api, manager, session, body, vector, monkeypatch, capsys,
        client_factory=receipt_client_factory,
    )
    observed["live_cli"] = observe_live_cli(
        api, db, vector, monkeypatch, capsys, client_factory=receipt_client_factory,
    )
    observed["observations"] = observe_sessions(
        api, manager, db, vector, monkeypatch, capsys,
        client_factory=receipt_client_factory,
    )
    observed["owner_membership"] = observe_owner_membership(
        app, api, manager, db, vector, monkeypatch, capsys,
        client_factory=receipt_client_factory,
    )
    api.headers.pop("Authorization")
    observed["unauthenticated"] = api.post(path, json=body).status_code
    api.close()
    assert observed == vector["expected"]
    if bound is not None:
        record_observations(evidence, bound, request.node.callspec.params["installation"], observed)


def observe_sessions(api, manager, db, vector, monkeypatch, capsys, *, client_factory):
    """Execute real root/client/frame parsers against private authenticated ASGI."""
    spec = vector["requests"]["observations"]
    capabilities = (
        "bridge-session-observations-v1", "bridge-session-observation-history-v1",
    )
    session = Session(
        spec["session_id"], "observation-fixture",
        SpawnTarget(
            type="local", cwd=".", project=spec["project"], worktree_id=spec["worktree_id"],
        ), preserve_target=True,
    )
    session.acp_session_id = spec["alias"]
    session.event_log = EventLog(db=db, session_id=session.session_id)
    db.create_session(session.session_id, "observation-fixture", None, ".", "local", "idle", 1.0)
    db.create_session(spec["unadmitted"], "unadmitted-fixture", None, ".", "local", "idle", 1.0)
    manager._sessions[session.session_id] = session
    session.event_log.append("permission_request", {"request_id": "observation-permission"})
    session.event_log.append("permission_resolved", {"request_id": "observation-permission"})
    session.event_log.append("compaction_start", {"current_tokens": 35, "token_limit": 100})
    db.flush()
    before = {
        table: db.execute_read("SELECT * FROM " + table)
        for table in ("sessions", "events", "turns", "pending_prompts", "live_messages")
    }
    observed = {"capabilities": {
        name: api.get("/health").json()["capabilities"][name] for name in capabilities
    }}
    reads = []
    old_capability = [False]

    class Response(io.BytesIO):
        def __init__(self, response):
            payload = response.content
            if old_capability[0] and response.request.url.path == "/health":
                body = response.json()
                for name in capabilities:
                    body["capabilities"].pop(name, None)
                payload = json.dumps(body).encode()
            super().__init__(payload)
            self.status = response.status_code
            self.headers = response.headers

    def open_private(request, **_kwargs):
        selected = urlsplit(request.full_url)
        assert selected.netloc == "testserver"
        assert request.get_method() == "GET"
        if selected.path != "/health":
            reads.append(selected.path)
        response = api.get(
            selected.path + ("?" + selected.query if selected.query else ""),
            headers=dict(request.header_items()),
        )
        assert response.status_code == 200
        return Response(response)

    def configured_client(_cls):
        token = api.headers["Authorization"].split(" ", 1)[1]
        return BridgeClient("http://testserver", token, connect_grace=0)

    def command(argv):
        capsys.readouterr()
        try:
            core.main(list(argv))
            code = 0
        except SystemExit as exc:
            code = exc.code
        captured = capsys.readouterr()
        lines = captured.out.splitlines()
        assert len(lines) == 1
        return code, json.loads(lines[0])

    with monkeypatch.context() as patch:
        factory = Mock(wraps=client_factory)
        ensure = Mock(side_effect=AssertionError("observer ensured a daemon"))
        start = Mock(side_effect=AssertionError("observer started a daemon"))
        actor = AsyncMock(side_effect=AssertionError("observer started an actor"))
        resume = AsyncMock(side_effect=AssertionError("observer resumed an actor"))
        handoff = AsyncMock(side_effect=AssertionError("observer handed off an actor"))
        patch.setattr(core, "_get_client", factory)
        patch.setattr(core, "_ensure_daemon", ensure)
        patch.setattr(core, "_launch_bridge_daemon", start)
        patch.setattr(BridgeClient, "from_config", classmethod(configured_client))
        patch.setattr("urllib.request.urlopen", open_private)
        patch.setattr(manager, "start_session", actor)
        patch.setattr(manager, "resume_session", resume)
        patch.setattr(manager, "handoff_session", handoff)
        try:
            code, snapshot = command(spec["snapshot_command"])
            assert snapshot["source"]["session_id"] == session.session_id
            assert snapshot["source"]["project"] == spec["project"]
            assert snapshot["source"]["worktree_id"] == spec["worktree_id"]
            assert snapshot["source"]["journal_incarnation"] == session.event_log.continuity_id
            compaction = snapshot["entries"][-1]["details"]["compaction_evidence"]["pre_usage"]
            observed["snapshot"] = {
                "exit": code, "kind": snapshot["kind"], "registry": snapshot["source"]["registry"],
                "entry_kinds": [item["kind"] for item in snapshot["entries"]],
                "head_session_id": snapshot["source"]["head_session_id"],
                "current_requests": snapshot["current_requests"],
                **{key: compaction[key] for key in (
                    "used_tokens", "active_capacity_tokens", "qualification",
                )},
            }
            code, abbreviated = command(spec["abbreviated_snapshot_command"])
            observed["abbreviated_snapshot"] = {
                "exit": code, "kind": abbreviated["kind"],
                "same_source": abbreviated["source"] == snapshot["source"],
            }
            code, history = command(spec["history_command"])
            observed["history"] = {
                "exit": code, "kind": history["kind"], "entries": len(history["entries"]),
                "has_more": history["has_more"],
            }
            for name in ("unknown", "alias", "unadmitted"):
                argv = [
                    spec[name] if part == spec["session_id"] else part
                    for part in spec["snapshot_command"]
                ]
                code, refused = command(argv)
                observed[name] = {"exit": code, "code": refused["code"]}
            reads.clear()
            old_capability[0] = True
            code, refused = command(spec["snapshot_command"])
            observed["old_capability"] = {
                "exit": code, "code": refused["code"], "source_reads": len(reads),
            }
            old_capability[0] = False
            query = {
                "contract_version": "1", "project": spec["project"],
                "worktree_id": spec["worktree_id"],
            }
            path = "/api/v1/session-observations/" + spec["session_id"]
            from agent_bridge.session_observation_contract import decode

            for name, changes in (
                ("wrong_project", {"project": "foreign"}),
                ("wrong_worktree", {"worktree_id": "foreign"}),
                ("unproven_head", {"head_session_id": "unproven"}),
            ):
                response = api.get(path, params={**query, **changes})
                body = decode(response.content.split(b"data: ", 1)[1].strip())
                observed[name] = body["code"]
            observed["history_follow"] = api.get(
                "/api/v1/session-observation-history/" + spec["session_id"],
                params={**query, "follow": "true"},
            ).status_code
            observed["unauthenticated"] = api.get(
                path, params=query, headers={"Authorization": ""},
            ).status_code
            assert factory.call_count == 7
            assert all(call.args == () and call.kwargs == {"ensure": False}
                       for call in factory.call_args_list)
            ensure.assert_not_called()
            start.assert_not_called()
            actor.assert_not_awaited()
            resume.assert_not_awaited()
            handoff.assert_not_awaited()
            observed["no_ensure"] = ensure.call_count == start.call_count == 0
            observed["no_actor_start"] = (
                actor.await_count == resume.await_count == handoff.await_count == 0
            )
        finally:
            core._set_project_override(None)
    after = {table: db.execute_read("SELECT * FROM " + table) for table in before}
    observed["read_only"] = after == before and session.subscriber_count == 0
    return observed


def test_http51_observation_boundaries(serving, native_send, monkeypatch, capsys):
    """Both source installation modes execute the owning passive read path."""
    app, api, db, _ = serving
    manager, _session, _request = native_send
    app.state.session_manager = manager
    vector = json.loads(evidence.safe(ROOT, evidence.INPUT).read_bytes())
    assert observe_sessions(
        api, manager, db, vector, monkeypatch, capsys, client_factory=core._get_client,
    ) == vector["expected"]["observations"]


@pytest.mark.parametrize("mode", ["legacy", "namespaced"])
def test_http51_observer_payload_abbreviations(tmp_path, mode):
    """Run the actual payload gate with inert resolution/publication/install spies."""
    from test_payload_invocation import test_passive_observer_attributed_payload_never_provisions

    shell = (
        "powershell" if shutil.which("powershell") else "pwsh"
    ) if os.name == "nt" else "bash"
    assert shutil.which(shell), "the capture requires its native fixture shell"
    vector = json.loads(evidence.safe(ROOT, evidence.INPUT).read_bytes())
    for index, argv in enumerate(vector["requests"]["observations"]["payload_commands"]):
        test_passive_observer_attributed_payload_never_provisions(
            tmp_path / str(index), shell, mode, argv,
        )


def test_http51_owner_membership_observations(serving, native_send, monkeypatch, capsys):
    """Canonical SQLite producer -> generation journal -> authenticated HTTP/root CLI."""
    app, api, db, _ = serving
    manager, _session, _request = native_send
    app.state.session_manager = manager
    vector = json.loads(evidence.safe(ROOT, evidence.INPUT).read_bytes())
    assert observe_owner_membership(
        app, api, manager, db, vector, monkeypatch, capsys,
        client_factory=core._get_client,
    ) == vector["expected"]["owner_membership"]


def observe_owner_membership(app, api, manager, db, vector, monkeypatch, capsys, *, client_factory):
    """Produce the independently expected membership section of the shared report."""
    from agent_bridge.owner_membership import CAPABILITY
    from agent_bridge.session_observation_contract import decode

    _membership_transcript_clock(monkeypatch)
    assert client_factory.__module__ == core.__name__ and client_factory.__name__ == "_get_client"
    spec = vector["requests"]["owner_membership"]
    private = "fixture-protected-token"
    target = {
        "caller_owner_ref": spec["owner_ref"], "project": "demo", "worktree_id": "fixture-child",
        "cwd": "/private/fixture", "env": {"TOKEN": private},
    }

    def create_member(sid):
        db.create_session(
            sid, "fixture", None, "/private/fixture", "local", "starting", 1.0,
            target_json=json.dumps(target),
        )

    create_member(spec["session_id"])
    query = {"owner_ref": spec["owner_ref"], "contract_version": "1"}
    path = "/api/v1/owner-observations"

    def body(**changes):
        response = api.get(path, params={**query, **changes})
        assert response.status_code == 200
        frame = decode(response.content.split(b"data: ", 1)[1].strip())
        if changes.get("owner_ref", spec["owner_ref"]) == spec["owner_ref"]:
            assert "position" in frame, json.dumps(frame, sort_keys=True)
        return frame

    snapshot = body()
    observed = {"capability": api.get("/health").json()["capabilities"][CAPABILITY]}
    observed["snapshot"] = {
        "kind": snapshot["kind"], "completeness": snapshot["completeness"],
        **{key: snapshot["members"][0][key] for key in (
            "registry", "link_qualification", "head_session_id", "current_answerability",
        )},
    }
    db.update_session_status(spec["session_id"], "idle", 2.0)
    delta = body(after=snapshot["position"])
    observed["delta"] = {
        "kind": delta["kind"], "state": delta["changes"][0]["member"]["state"],
        "evidence_kind": delta["changes"][0]["evidence_kind"],
    }
    reads = []
    old = [False]
    class Response(io.BytesIO):
        def __init__(self, response):
            payload = response.content
            if old[0] and response.request.url.path == "/health":
                value = response.json()
                value["capabilities"].pop(CAPABILITY)
                payload = json.dumps(value).encode()
            super().__init__(payload)
            self.status, self.headers = response.status_code, response.headers

    def transport(request, **_kwargs):
        selected = urlsplit(request.full_url)
        assert selected.netloc == "testserver" and request.get_method() == "GET"
        if selected.path != "/health":
            reads.append(selected.path)
        return Response(api.get(
            selected.path + ("?" + selected.query if selected.query else ""),
            headers=dict(request.header_items()),
        ))

    with monkeypatch.context() as patch:
        factory = Mock(wraps=client_factory)
        ensure = Mock(side_effect=AssertionError("membership ensured a daemon"))
        start = Mock(side_effect=AssertionError("membership started a daemon"))
        actor = AsyncMock(side_effect=AssertionError("membership started an actor"))
        patch.setattr(core, "_get_client", factory)
        patch.setattr(core, "_ensure_daemon", ensure)
        patch.setattr(core, "_launch_bridge_daemon", start)
        patch.setattr(manager, "start_session", actor)
        patch.setattr(BridgeClient, "from_config", classmethod(
            lambda _cls: BridgeClient(
                "http://testserver", api.headers["Authorization"].split(" ", 1)[1], connect_grace=0,
            ),
        ))
        patch.setattr("urllib.request.urlopen", transport)

        def command():
            capsys.readouterr()
            try:
                core.main(list(spec["command"]))
                status = 0
            except SystemExit as exc:
                status = exc.code
            return status, json.loads(capsys.readouterr().out)

        try:
            status, point = command()
            observed["cli"] = {
                "exit": status, "kind": point["kind"], "ensure": factory.call_args.kwargs["ensure"],
            }
            reads.clear()
            old[0] = True
            status, refused = command()
            observed["old_capability"] = {
                "exit": status, "code": refused["code"], "source_reads": len(reads),
            }
            ensure.assert_not_called()
            start.assert_not_called()
            actor.assert_not_called()
            observed["no_ensure"] = ensure.call_count == start.call_count == 0
            observed["no_actor_start"] = actor.call_count == 0
        finally:
            core._set_project_override(None)
    unknown = body(owner_ref="fixture-host/demo/unadmitted")
    observed["unknown"] = {key: unknown[key] for key in ("kind", "code")}
    create_member(spec["session_id"] + "-second")
    before_retire = body()["position"]
    db.update_session_status(spec["session_id"] + "-second", "ended", 3.0)
    retired = body(after=before_retire)["changes"][-1]
    observed["retired"] = {key: retired[key] for key in ("kind", "grants_claim_release")}
    before_gone = body()["position"]
    db.delete_session(spec["session_id"])
    gone = body(after=before_gone)["changes"][-1]
    observed["gone"] = {key: gone[key] for key in ("kind", "grants_claim_release")}
    exposed = json.dumps([snapshot, delta, retired, gone, point])
    observed["token_exclusion"] = private not in exposed and "/private/" not in exposed
    observed["no_idle_pinning"] = all(item.subscriber_count == 0 for item in manager._sessions.values())
    observed["unauthenticated"] = api.get(path, params=query, headers={"Authorization": ""}).status_code
    assert observed == vector["expected"]["owner_membership"]
    return observed

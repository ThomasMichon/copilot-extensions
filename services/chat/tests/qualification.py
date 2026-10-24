"""Exact family membership; every parameter node inherits its family's lane."""

import hashlib
import json
import os
from collections import Counter


FAMILIES = {
    "test_collaboration.py": """
test_metadata_refuses_invalid_fields_and_bounds
test_structured_identity_recovery_and_legacy_shapes
test_relationships_refuse_wrong_targets_without_mutation
test_metadata_indexes_and_quota_rollback_atomically
test_pending_survives_noise_cursors_membership_and_reopen
test_answers_resolution_and_supersession_have_distinct_effects
test_captured_pages_remain_nonconsuming_despite_later_answers
test_byte_bounded_pending_and_recent_pages_never_skip_or_consume
test_plain_thread_root_and_superseding_reply_preserve_relationship
test_concurrent_annotated_retry_commits_one_effect_and_order_is_content
test_annotated_pending_requires_complete_receipt_before_recovery
test_unsupported_structured_service_never_falls_back_to_plaintext
test_client_refuses_invalid_views_without_mutating_semantic_progress
test_empty_inbox_work_does_not_grow_with_unrelated_questions
test_inbox_work_ignores_other_channel_correspondence
test_sparse_thread_work_and_pagination_ignore_unrelated_history
test_inbox_lookahead_work_is_bounded_before_merging_pending_branches
test_pending_question_lookup_work_ignores_other_participants_answers
test_inbox_examination_is_bounded_over_settled_history
test_inbox_scan_continuations_preserve_captured_pending_without_skips
""",
    "test_workspace.py": """
test_worktree_identity_survives_sessions_and_label_changes_without_adoption
test_anchor_segments_startup_session_and_never_queries_a_synthetic_head
test_existing_owner_head_and_execution_tuple_are_not_reassigned
test_invalid_initialization_refuses_before_executing_owner
test_unresolved_or_inconsistent_owner_evidence_refuses_without_fallback
test_workspace_reads_share_one_finite_deadline
test_late_owner_response_cannot_turn_deadline_expiry_into_success
test_workspace_requires_explicit_complete_owner_argv_before_execution
test_owner_failure_is_not_retried_or_downgraded_to_anchor
test_malformed_owner_json_has_the_client_response_error_boundary
test_anchor_source_type_is_checked_before_configured_provenance
test_workspace_cli_requires_exact_explicit_initialization_before_owner
test_installed_workspace_uses_trusted_cwd_and_selected_owner_without_source_or_environment
""",
    "test_catalog.py": """
test_source_version_reads_only_its_own_project_metadata
test_source_version_refuses_invalid_metadata_without_installed_fallback
test_installed_version_uses_distribution_metadata_and_preserves_display
test_exact_current_interpreter_not_path_or_source_selection
test_catalog_refuses_unusable_installed_evidence
test_missing_distribution_is_an_explicit_offline_error
test_catalog_refuses_linked_resource_evidence
test_catalog_includes_the_host_module_in_its_verified_closure
test_catalog_includes_the_workspace_module_in_its_verified_closure
test_catalog_includes_collaboration_in_its_verified_closure
test_catalog_refuses_changed_construction_resources
""",
    "test_cli.py": """
test_real_cli_provider_http_store_and_independent_forward_read
test_cli_dispatch_preserves_exact_client_controls
test_cli_lost_post_response_retains_original_and_explicit_recovery_deduplicates
test_offline_status_and_capability_never_execute_provider_or_network
test_actual_installed_cli_death_releases_reader_lock_before_outer_cleanup
test_http_failures_are_not_retried_or_redirected
test_http_response_bound_is_separate_from_request_bound
test_http_total_deadline_releases_viewer_lock_despite_continuing_traffic
""",
    "test_client.py": """
test_uncertain_post_creates_private_exclusive_temporary_before_atomic_replace
test_posix_uncertain_post_is_private_under_umask_022_without_changing_test_process
test_members_pages_preserve_legacy_shape_without_read_progress
test_invalid_member_controls_refuse_before_transport
test_invalid_member_pages_refuse_progress
test_output_flush_precedes_progress_and_viewers_are_independent
test_failed_output_never_commits_a_cursor
test_flushed_output_with_unconfirmed_progress_is_explicit
test_invalid_pages_do_not_print_or_advance
test_malformed_success_objects_do_not_confirm_an_operation
test_scan_validates_body_free_returned_only_progress_without_reading
test_pending_original_is_retained_and_only_explicit_same_id_recovery_sends
test_pending_capacity_is_exact_and_a_later_refusal_does_not_erase_uncertainty
test_new_definitive_refusal_does_not_leave_an_unknown_post
test_local_reader_lock_is_shared_and_crash_released
test_response_json_can_be_large_without_widening_default_request_bounds
""",
    "test_distribution.py": """
test_source_removed_installed_transport_enforces_raw_budgets_and_strict_text
test_source_independent_wheel
test_wheel_and_independent_sdist_deliver_exact_canonical_construction
test_installed_catalog_worker_preserves_isolation
""",
    "test_host_distribution.py": """
test_fresh_installed_host_source_removed_independent_participants_and_crash_restart
""",
    "test_setup_distribution.py": """
test_independent_sdist_refuses_missing_construction_input
test_installed_constructor_preserves_the_separate_bootstrap_floor
""",
    "test_local_endpoint.py": """
test_local_endpoint_offline_status_never_resolves_or_executes
test_local_endpoint_configuration_refuses_ambiguous_or_implicit_routes
test_local_endpoint_real_tcp_request_uses_canonical_record_and_stable_namespace
test_local_endpoint_refusal_is_bounded_without_network_or_fallback
test_local_endpoint_real_unix_client_uses_the_published_host
""",
    "test_hosting.py": """
test_two_participants_auth_read_progress_and_restart
test_real_singleton_refuses_second_host_before_store_open
test_held_wait_drain_and_health_keep_independent_requests_responsive
test_request_framing_and_duplicate_auth_refuse_before_dispatch
test_absolute_ingress_deadline_retires_slow_input_and_releases_admission
test_aggregate_headers_refuse_without_reflecting_input
test_worker_admission_is_bounded_and_overflow_does_not_spawn
test_configuration_refuses_without_echoing_values
test_small_or_non_ascii_secret_refuses_before_data_creation
test_maximum_key_with_file_line_ending_is_used_without_masking
test_failed_endpoint_publication_retires_host_and_removes_own_attempt
test_close_retains_custody_until_held_transaction_and_workers_retire
test_busy_health_is_structured_and_recovers_after_transaction
test_complete_valid_membership_is_retrievable_with_wire_bounded_pages
test_members_page_controls_are_validated_before_lookup
""",
    "test_transport.py": """
test_real_authorization_construction_preserves_the_supplied_value
test_invalid_credentials_are_rejected_without_echo
test_endpoint_selection_is_explicit_and_does_not_downgrade
test_real_owned_provider_keeps_stdin_stdout_stderr_separate
test_real_owned_provider_has_a_deadline
test_missing_caller_death_capability_refuses_before_legacy_helper_execution
test_owned_provider_passes_separate_capture_budgets_and_stdin
test_real_provider_overflow_refuses_before_its_deadline_without_disclosing_output
test_unconfirmed_cleanup_retains_exact_typed_custody
test_credential_provider_has_its_own_raw_byte_budget
test_owned_provider_exit_status_is_visible_without_its_stderr
test_incomplete_or_implicit_shell_commands_refuse
test_cross_os_provider_is_refused_before_execution
test_explicit_stdio_contract_is_bounded_and_has_no_hidden_credential_call
test_invalid_stdio_responses_are_not_retried
""",
    "test_store.py": """
test_independent_nonconsuming_pages_and_forward_cas
test_restart_dedup_snapshot_identity_and_monotonic_events
test_expected_owner_leave_succession_and_inactive_cursor
test_committed_post_retry_after_leave_is_reconciled_without_rejoining
test_post_failure_rolls_back_counter_event_and_request_identity
test_full_quota_keeps_dedup_leave_and_cursor_available
test_concurrent_retries_commit_one_effect
test_scan_paging_contains_no_bodies_or_semantic_progress
test_pending_hint_crash_keeps_exact_body_unread
test_unicode_and_escaped_pages_never_advance_omitted_events
test_shared_waiter_bound_is_released
test_held_scan_observes_membership_or_store_end
test_held_membership_generation_is_independent_of_clock
test_database_busy_is_explicit_without_partial_post
test_drain_freezes_all_rpc_without_mutating_conversation
test_drain_fences_held_waits_even_after_immediate_undrain
test_drain_waits_for_existing_write_commit_and_restart_preserves_it
test_waiter_failure_releases_lifecycle_count_and_capacity
test_strict_serialized_inputs_are_bounded_and_unambiguous
test_invalid_operation_fields_and_controls_are_explicit
test_legacy_epoch_upgrade_preserves_events_and_semantic_cursor
test_membership_capacity_is_finite_and_inactive_rows_are_retained
test_message_byte_and_unicode_bounds
test_actor_aggregate_and_request_id_bounds
test_quiet_wait_and_plural_post_batch_do_not_consume_reading
""",
    "test_qualification.py": """
test_membership_is_exact_and_full_retains_every_parameter
test_membership_refuses_missing_unassigned_or_duplicate_nodes
test_artifact_helper_preserves_cwd_stdin_and_environment
""",
}

FULL_ONLY = {"test_hosting.py", "test_host_distribution.py", "test_setup_distribution.py"}
EXPECTED = {
    f"{module}::{name}": "full-only" if module in FULL_ONLY else "acceptance"
    for module, names in FAMILIES.items() for name in names.split()
}

PARAMETER_COUNTS = dict.fromkeys(EXPECTED, 1)
PARAMETER_COUNTS.update({
    "test_collaboration.py::test_metadata_refuses_invalid_fields_and_bounds": 22,
    "test_collaboration.py::test_annotated_pending_requires_complete_receipt_before_recovery": 6,
    "test_collaboration.py::test_client_refuses_invalid_views_without_mutating_semantic_progress": 13,
    "test_collaboration.py::test_inbox_lookahead_work_is_bounded_before_merging_pending_branches": 2,
    "test_collaboration.py::test_inbox_examination_is_bounded_over_settled_history": 3,
    "test_collaboration.py::test_inbox_scan_continuations_preserve_captured_pending_without_skips": 3,
    "test_workspace.py::test_existing_owner_head_and_execution_tuple_are_not_reassigned": 4,
    "test_workspace.py::test_invalid_initialization_refuses_before_executing_owner": 6,
    "test_workspace.py::test_unresolved_or_inconsistent_owner_evidence_refuses_without_fallback": 22,
    "test_workspace.py::test_workspace_requires_explicit_complete_owner_argv_before_execution": 4,
    "test_workspace.py::test_malformed_owner_json_has_the_client_response_error_boundary": 4,
    "test_workspace.py::test_anchor_source_type_is_checked_before_configured_provenance": 6,
    "test_workspace.py::test_workspace_cli_requires_exact_explicit_initialization_before_owner": 4,
    "test_catalog.py::test_source_version_reads_only_its_own_project_metadata": 6,
    "test_catalog.py::test_source_version_refuses_invalid_metadata_without_installed_fallback": 21,
    "test_catalog.py::test_installed_version_uses_distribution_metadata_and_preserves_display": 2,
    "test_catalog.py::test_catalog_includes_the_host_module_in_its_verified_closure": 3,
    "test_catalog.py::test_catalog_includes_the_workspace_module_in_its_verified_closure": 3,
    "test_catalog.py::test_catalog_includes_collaboration_in_its_verified_closure": 3,
    "test_catalog.py::test_catalog_refuses_unusable_installed_evidence": 17,
    "test_catalog.py::test_catalog_refuses_changed_construction_resources": 9,
    "test_cli.py::test_cli_dispatch_preserves_exact_client_controls": 9,
    "test_cli.py::test_http_failures_are_not_retried_or_redirected": 4,
    "test_cli.py::test_http_total_deadline_releases_viewer_lock_despite_continuing_traffic": 2,
    "test_client.py::test_failed_output_never_commits_a_cursor": 3,
    "test_client.py::test_local_reader_lock_is_shared_and_crash_released": 2,
    "test_client.py::test_flushed_output_with_unconfirmed_progress_is_explicit": 2,
    "test_client.py::test_invalid_member_controls_refuse_before_transport": 5,
    "test_client.py::test_invalid_member_pages_refuse_progress": 9,
    "test_client.py::test_invalid_pages_do_not_print_or_advance": 7,
    "test_client.py::test_malformed_success_objects_do_not_confirm_an_operation": 6,
    "test_client.py::test_scan_validates_body_free_returned_only_progress_without_reading": 4,
    "test_distribution.py::test_installed_catalog_worker_preserves_isolation": 2,
    "test_setup_distribution.py::test_independent_sdist_refuses_missing_construction_input": 3,
    "test_distribution.py::test_source_independent_wheel": 2,
    "test_hosting.py::test_absolute_ingress_deadline_retires_slow_input_and_releases_admission": 2,
    "test_hosting.py::test_configuration_refuses_without_echoing_values": 7,
    "test_hosting.py::test_held_wait_drain_and_health_keep_independent_requests_responsive": 2,
    "test_hosting.py::test_members_page_controls_are_validated_before_lookup": 6,
    "test_hosting.py::test_request_framing_and_duplicate_auth_refuse_before_dispatch": 5,
    "test_local_endpoint.py::test_local_endpoint_configuration_refuses_ambiguous_or_implicit_routes": 4,
    "test_local_endpoint.py::test_local_endpoint_refusal_is_bounded_without_network_or_fallback": 18,
    "test_store.py::test_drain_fences_held_waits_even_after_immediate_undrain": 4,
    "test_store.py::test_drain_freezes_all_rpc_without_mutating_conversation": 9,
    "test_store.py::test_held_membership_generation_is_independent_of_clock": 8,
    "test_store.py::test_held_scan_observes_membership_or_store_end": 2,
    "test_store.py::test_invalid_operation_fields_and_controls_are_explicit": 13,
    "test_store.py::test_membership_capacity_is_finite_and_inactive_rows_are_retained": 4,
    "test_store.py::test_message_byte_and_unicode_bounds": 7,
    "test_store.py::test_strict_serialized_inputs_are_bounded_and_unambiguous": 12,
    "test_store.py::test_waiter_failure_releases_lifecycle_count_and_capacity": 2,
    "test_transport.py::test_endpoint_selection_is_explicit_and_does_not_downgrade": 12,
    "test_transport.py::test_incomplete_or_implicit_shell_commands_refuse": 4,
    "test_transport.py::test_invalid_credentials_are_rejected_without_echo": 5,
    "test_transport.py::test_invalid_stdio_responses_are_not_retried": 4,
    "test_transport.py::test_real_provider_overflow_refuses_before_its_deadline_without_disclosing_output": 2,
    "test_qualification.py::test_membership_refuses_missing_unassigned_or_duplicate_nodes": 4,
})


def membership(nodes):
    """Refuse missing/new families and duplicate nodes instead of silently omitting work."""
    if len(nodes) != len(set(nodes)):
        raise ValueError("duplicate Chat qualification nodes")
    families = {node.split("[", 1)[0] for node in nodes}
    if families != EXPECTED.keys():
        raise ValueError(
            f"Chat qualification membership changed: missing={sorted(EXPECTED.keys() - families)}; "
            f"unassigned={sorted(families - EXPECTED.keys())}"
        )
    if Counter(node.split("[", 1)[0] for node in nodes) != PARAMETER_COUNTS:
        raise ValueError("Chat qualification parameter membership changed")
    return {node: EXPECTED[node.split("[", 1)[0]] for node in sorted(nodes)}


def membership_digest(mapping):
    return hashlib.sha256(
        json.dumps(mapping, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def execution_summary(mapping, profile, reports):
    if profile not in {"acceptance", "full"}:
        raise ValueError("unknown Chat qualification lane")
    expected = {
        node for node, lane in mapping.items() if profile == "full" or lane == "acceptance"
    }
    allowed_skips = {
        "test_client.py::test_posix_uncertain_post_is_private_under_umask_022_without_changing_test_process",
        "test_local_endpoint.py::test_local_endpoint_real_unix_client_uses_the_published_host",
    } if os.name == "nt" else set()
    completed, called, skipped, rejected = [], [], [], []
    for report in reports:
        node = report.nodeid.removeprefix("tests/")
        if report.when == "call" and report.outcome == "passed" and not hasattr(report, "wasxfail"):
            called.append(node)
        elif (
            report.when == "setup" and report.outcome == "skipped"
            and node in allowed_skips and not hasattr(report, "wasxfail")
        ):
            skipped.append(node)
        else:
            rejected.append(node)
            continue
        completed.append(node)
    observed = set(completed)
    missing, unexpected = sorted(expected - observed), sorted(observed - expected)
    duplicate = len(completed) != len(observed)
    return {
        "status": "incomplete" if not expected or missing or unexpected or rejected or duplicate
        else "complete",
        "called": len(called), "skipped": sorted(skipped), "missing": missing,
        "unexpected": unexpected, "rejected": sorted(rejected), "duplicate": duplicate,
    }

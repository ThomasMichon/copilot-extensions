"""HTTP-52 owner membership; all earlier generations remain immutable evidence."""

from functools import partial
import json
from pathlib import Path

import bridge_http51_evidence as retained
import bridge_http_retained_evidence as common

previous = common.previous
installation = common.installation
GENERATION = 52
VECTOR_SCHEMA = "http52-owner-membership-cases-v1"
INPUT = common.CONTRACT + "inputs/http52/owner-membership.json"
PRIMARY = previous.CURRENT + "http52-owner-membership.json"
FAMILY = "http52-owner-membership-asgi-v1"
CAPTURE = "tools/capture_bridge_http52.py"
REGISTRY = common.REGISTRY
SCOPE = (
    retained.SCOPE + "; membership transcripts use a virtual reader clock; "
    "separate deadline-expiry and actual SQLite VM-cutoff probes; "
    "not wall-clock read qualification; portable launch joins actual archive/policy readers, "
    "ASGI/SQLite and driver HTTP with controlled SDK, native mux, provider process, SSH, "
    "Owner, claims, forwards and POSIX-to-private-scratch namespace fixtures; "
    "not native Copilot, mux, remote authentication or installed qualification"
)
POLICY = common.POLICY
AFFECTED = (
    *retained.AFFECTED,
    *(previous.PREFIX + name for name in (
        "acp_client.py", "acp_callbacks.py", "acp_current_requests.py",
        "session_current_requests.py", "resume_cli_contract.py",
        "event_tool_statistics.py", "session_tool_statistics.py",
        "owner_membership_schema.py",
        "cli_mode_models.py", "cli_mode_provider.py", "db_inbox_custody.py",
        "live_inbox_models.py", "plugin_readiness.py", "routes/live_startup_controls.py",
        "receiving_cli.py", "receiving_bundle.py", "receiving_mux.py",
        "session_lifecycle_cli.py", "worktree_writer_observation.py",
        "acquisition_worktree_writer.py", "acquisition_writer_inspection.py", "acquisition_authority.py",
        "acquisition_cli.py", "acquisition_contract.py", "acquisition_custody_schema.py",
        "acquisition_journal.py", "acquisition_runtime.py", "acquisition_store.py",
        "routes/acquisitions.py", "session_handoff.py", "session_resume.py",
        "session_start.py", "session_core.py", "session_prompts.py", "worktree_head.py", "worktree_lineage.py",
        "_peer_launch.py",
    )),
    *("plugins/agent-worktrees/src/agent_worktrees/" + name for name in (
        "retained_record_writer.py", "resource_relationship_cli.py", "tracking_write.py",
        "session_binding_cli.py", "session_tracking_cli.py",
        "tracking_session_registration_write.py", "tracking_session_lifecycle_write.py",
    )),
    "plugins/agent-bridge/extensions/agent-bridge/metadata.mjs",
    "plugins/agent-bridge/extensions/agent-bridge/retained-delivery.mjs",
    "plugins/agent-bridge/extensions/agent-bridge/remote-driver.mjs",
    "plugins/agent-remote-driver/extensions/agent-remote-driver/control-client.mjs",
    "plugins/agent-remote-driver/extensions/agent-remote-driver/startup-client.mjs",
    "libs/venue-copilot/src/venue_copilot/portable_mux.py",
)
EXTRA_PATHS = (
    *retained.EXTRA_PATHS, INPUT, CAPTURE, "tools/bridge_http52_evidence.py",
    "plugins/agent-bridge/tests/test_acp_current_requests.py",
    "plugins/agent-bridge/tests/test_inbox_consumer_enrollment.py",
    "plugins/agent-bridge/tests/test_live_inbox_producer.py",
    "plugins/agent-bridge/tests/test_plugin_readiness.py",
    "plugins/agent-bridge/tests/test_receiving_bundle.py",
    "plugins/agent-bridge/tests/test_portable_mux_joined.py",
    "plugins/agent-bridge/tests/test_worktree_writer_observation.py",
    "plugins/agent-bridge/tests/test_acquisition_runtime.py",
    "plugins/agent-bridge/tests/test_acquisition_writer_inspection.py",
    "plugins/agent-bridge/tests/test_worktree_lineage_authority.py",
    "plugins/agent-worktrees/tests/fixtures/worktree_record_owner.py",
    "plugins/agent-bridge/tests/fixtures/portable-mux-sdk.mjs",
    "plugins/agent-bridge/launch-wrapper-assets.json",
    "plugins/agent-bridge/packaged-assets.json",
    *(path.relative_to(Path(__file__).resolve().parents[1]).as_posix()
      for plugin in ("agent-codespaces", "agent-worktrees")
      for path in sorted(
          (Path(__file__).resolve().parents[1] / "plugins" / plugin / "src").rglob("*.py"),
      ) if "__pycache__" not in path.parts),
    *(declaration["canonicalDir"] + "/" + name
      for manifest in ("packaged-assets.json", "launch-wrapper-assets.json")
      for declaration in (json.loads(
          (Path(__file__).resolve().parents[1] / "plugins/agent-bridge" / manifest).read_bytes(),
      ),)
      for name in declaration["files"]),
)
PROBES = {
    **retained.PROBES,
    "test_private_deadline_exhaustion_is_a_typed_gap_without_wallclock_sleep": 1,
    "test_private_deadline_uses_precise_elapsed_time_not_coarse_ticks": 3,
    "test_unknown_owner_large_history_has_actual_private_vm_cutoff": 1,
    "test_selected_owner_capture_does_not_scan_unrelated_registries": 1,
    "test_only_writable_bootstrap_prepares_lookup_indexes": 1,
    "test_actual_local_callback_future_reaches_http_without_history_inference": 1,
    "test_optional_wire_is_explicit_and_never_added_to_old_or_history_reads": 1,
    "test_optional_cli_negotiates_actual_provider_before_sending_new_query": 4,
    "test_actual_cli_optional_parser_declaration_and_http_frame_join": 1,
    "test_live_waiters_change_without_changing_event_positions": 2,
    "test_waiter_capture_before_wait_race_does_not_wait_for_backstop": 1,
    "test_live_qualification_replacement_terminal_and_reader_cleanup": 1,
    "test_replaced_same_log_handle_keeps_exact_subscriber_and_producer_custody": 1,
    "test_closing_one_current_follower_preserves_other_follower_push": 1,
    "test_optional_block_reserves_space_before_event_tail_and_preserves_page_ends": 1,
    "test_contribution_projection_retains_only_bounded_reported_names": 1,
    "test_statistics_cover_owner_range_not_page_or_repeated_delivery": 1,
    "test_statistics_empty_zero_and_ancillary_failure_keep_source_records": 1,
    "test_statistics_atomic_cut_and_captured_gap_do_not_claim_full_history": 1,
    "test_statistics_large_range_uses_only_bounded_event_projection": 1,
    "test_statistics_only_qualification_change_pushes_on_existing_follower": 1,
    "test_statistics_parser_rejects_malformed_or_cross_cut_claims": 20,
    "test_statistics_wire_is_optional_and_independent_of_current_requests": 1,
    "test_statistics_cli_checks_live_support_without_losing_supported_waiters": 4,
    "test_statistics_actual_cli_parser_http_join_and_strict_legacy": 1,
    "test_portable_scope_never_leaks_a_seed_to_the_legacy_inbox_before_enrollment": 1,
    "test_portable_reload_keeps_its_native_binding_and_uses_the_existing_cas": 1,
    "test_remote_enrollment_requires_the_original_portable_reservation": 7,
    "test_retained_startup_admission_requires_its_original_ready_input": 12,
    "test_readonly_poll_never_claims_or_expires_mutation_controls": 1,
    "test_bridge_provider_receiver_and_extensions_share_one_conversation": 2,
    "test_provider_owner_uses_the_actual_client_interface": 1,
    "test_health_observes_canonical_writer_selection_without_launch": 5,
    "test_repeated_health_reuses_real_resolver_but_refreshes_selected_slot": 2,
    "test_repeated_health_retains_packaged_validator_but_rechecks_cell_receipts": 1,
    "test_unknown_or_changed_slot_never_certifies_a_build": 10,
    "test_same_cell_health_rechecks_peer_selection_without_launch": 11,
    "test_required_writer_round_trips_through_the_existing_http_owner": 1,
    "test_required_writer_survives_stopped_resume_and_later_acquisition": 2,
    "test_required_handoff_retry_reuses_original_successor_after_lineage_failure": 4,
    "test_required_handoff_retains_unknown_effect_without_replacement": 2,
    "test_required_writer_refuses_provider_type_change_before_persist_or_launch": 5,
    "test_required_writer_resume_exhaustion_cannot_recreate": 1,
    "test_required_handoff_retries_registration_on_original_native": 3,
    "test_required_registration_failure_detaches_before_normal_send": 2,
    "test_required_queued_send_refusal_keeps_startup_recovery_dormant": 1,
    "test_required_writer_refuses_before_native_effects": 6,
    "test_retained_acquisition_and_later_lineage_use_actual_owner_processes": 1,
    "test_retained_session_receiver_refuses_changed_scope_without_resident_fallback": 12,
    "test_retained_writer_inspection_cli_http": 3,
    "test_retained_writer_inspection_unavailable_http": 4,
}
TEST = retained.TEST
CAPABILITY_VERSIONS = {
    **retained.CAPABILITY_VERSIONS, "owner_membership": 52,
    "bridge-session-current-requests-v1": 1,
    "bridge-session-tool-statistics-v1": 1,
    "portable_mux": 1, "portable_inbox": 1, "readonly_plugin_controls": 1,
    "worktree_record_delegation": 1,
    "retained_acquisition_worktree_writer": 1,
    "retained_acquisition_writer_inspection": 1,
}
SPEC = common.EvidenceSpec(
    GENERATION, INPUT, PRIMARY, FAMILY, AFFECTED, EXTRA_PATHS, PROBES,
    (*retained.SPEC.capability_floors, ("OWNER_MEMBERSHIP_PROTOCOL_VERSION", 52)),
    scope=SCOPE,
)
encode = common.encode
digest = common.digest
safe = common.safe
contract = common.contract
baseline = partial(common.baseline, SPEC)
source_snapshot = partial(common.source_snapshot, SPEC)
source_fixture_path = partial(common.source_fixture_path, SPEC)
history = partial(common.history, SPEC)
build_outputs = partial(common.build_outputs, SPEC)
validate_outputs = partial(common.validate_outputs, SPEC)
validate = partial(common.validate, SPEC)
validate_http52 = validate

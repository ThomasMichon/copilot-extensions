"""HTTP-52 owner membership; all earlier generations remain immutable evidence."""

from functools import partial

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
    "not wall-clock read qualification"
)
POLICY = common.POLICY
AFFECTED = (
    *retained.AFFECTED,
    *(previous.PREFIX + name for name in (
        "acp_client.py", "acp_callbacks.py", "acp_current_requests.py",
        "session_current_requests.py", "resume_cli_contract.py",
    )),
)
EXTRA_PATHS = (
    *retained.EXTRA_PATHS, INPUT, CAPTURE, "tools/bridge_http52_evidence.py",
    "plugins/agent-bridge/tests/test_acp_current_requests.py",
)
PROBES = {
    **retained.PROBES,
    "test_private_deadline_exhaustion_is_a_typed_gap_without_wallclock_sleep": 1,
    "test_unknown_owner_large_history_has_actual_private_vm_cutoff": 1,
    "test_actual_local_callback_future_reaches_http_without_history_inference": 1,
    "test_optional_wire_is_explicit_and_never_added_to_old_or_history_reads": 1,
    "test_optional_cli_negotiates_actual_provider_before_sending_new_query": 4,
    "test_actual_cli_optional_parser_declaration_and_http_frame_join": 1,
    "test_live_waiters_change_without_changing_event_positions": 2,
    "test_waiter_capture_before_wait_race_does_not_wait_for_backstop": 1,
    "test_live_qualification_replacement_terminal_and_reader_cleanup": 1,
    "test_replaced_same_log_handle_keeps_exact_subscriber_and_producer_custody": 1,
    "test_optional_block_reserves_space_before_event_tail_and_preserves_page_ends": 1,
}
TEST = retained.TEST
CAPABILITY_VERSIONS = {
    **retained.CAPABILITY_VERSIONS, "owner_membership": 52,
    "bridge-session-current-requests-v1": 1,
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

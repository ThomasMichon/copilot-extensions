"""HTTP-51 standard native send; prior captures remain immutable."""

from functools import partial

import bridge_http_retained_evidence as common

previous = common.previous
historical = common.historical
installation = common.installation
CONTRACT = common.CONTRACT
REGISTRY = common.REGISTRY
GENERATION = 51
VECTOR_SCHEMA = "http51-standard-native-send-cases-v1"
CAPTURE = "tools/capture_bridge_http51.py"
SCOPE = (
    "model-free native-send and known-session observation ASGI/journal evidence; "
    "not SDK/native qualification"
)
INPUT = CONTRACT + "inputs/http51/standard-native-send.json"
PRIMARY = previous.CURRENT + "http51-standard-native-send.json"
FAMILY = "http51-standard-native-send-asgi-v1"
AFFECTED = tuple(previous.PREFIX + name for name in (
    "owner_membership_read.py", "owner_membership_signal.py",
    "db_runtime_custody.py", "db_sessions.py", "db_relationships.py",
    "db_events.py", "db_prompts.py", "db_live_controls.py",
    "remote_relationships.py", "remote_acp_sessions.py",
    "owner_membership.py", "owner_membership_records.py", "owner_membership_contract.py",
    "owner_membership_cli.py", "routes/owner_membership.py",
    "__main__.py", "app_routes.py", "events.py", "compaction_evidence.py", "db_adoption.py",
    "session_observation_contract.py", "session_observations.py",
    "session_observation_cli.py", "routes/session_observations.py",
    "protocol.py", "native_send_admission.py", "native_bound_prompt.py",
    "native_keyed_admission.py", "routes/prompts.py", "send_outcome.py",
    "session_prompts.py", "session_targeting_cli.py", "native_send_receipt.py",
    "native_keyed_client.py", "routes/health.py",
    "db_core.py", "db_schema.py", "inbox_custody.py", "db_live_sessions.py", "db_live_session_aliases.py",
    "db_live_message_receipts.py", "live_message_enqueue.py", "live_message_models.py",
    "live_keyed_receipt.py", "live_exact_notify.py", "routes/live_inbox.py",
    "routes/live_sessions.py", "routes/live_message_receipts.py",
)) + (
    "plugins/agent-bridge/extensions/agent-bridge/delivery.mjs",
    "plugins/agent-bridge/extensions/agent-bridge/extension.mjs",
)
TEST = "plugins/agent-bridge/tests/test_http51_capture_probes.py"
EXTRA_PATHS = (
    INPUT, TEST, "plugins/agent-bridge/tests/test_native_send_admission.py",
    "tools/bridge_http51_declarations.py",
    "tools/test_bridge_http51_declarations.py",
    "plugins/agent-bridge/contract/README.md",
    "plugins/agent-bridge/docs/session-observations.md",
    "plugins/agent-bridge/docs/owner-membership.md",
    "plugins/agent-bridge/tests/test_owner_membership.py",
    "plugins/agent-bridge/tests/test_owner_membership_budget.py",
    "plugins/agent-bridge/tests/test_http51_capture_guards.py",
    "plugins/agent-bridge/tests/test_session_observations.py",
    "plugins/agent-bridge/tests/test_session_observation_transport.py",
    "plugins/agent-bridge/tests/test_payload_invocation.py",
    "plugins/agent-bridge/bin/agent-bridge",
    "plugins/agent-bridge/bin/agent-bridge.ps1",
    "plugins/agent-bridge/scripts/runtime-gate.sh",
    "plugins/agent-bridge/scripts/runtime-gate.ps1",
    "plugins/agent-bridge/scripts/resolve-runtime.sh",
    "plugins/agent-bridge/scripts/resolve-runtime.ps1",
    "plugins/agent-bridge/scripts/installation-context/json-query.awk",
    "plugins/agent-bridge/tests/http51_receipt_probes.py",
    "plugins/agent-bridge/tests/http51_live_cli_probes.py",
    "plugins/agent-bridge/tests/test_native_bound_prompt.py",
    "plugins/agent-bridge/tests/test_live_inbox_runtime.py",
    "plugins/agent-bridge/tests/test_loop_governance_custody.py",
    "plugins/agent-bridge/tests/test_live_keyed_receipt.py",
    "plugins/agent-bridge/tests/test_live_exact_notify.py",
    "plugins/agent-bridge/tests/test_live_exact_rollback.py",
    "plugins/agent-bridge/tests/fixtures/schema38-exact-rollback-source.json",
    "plugins/agent-bridge/tests/fixtures/schema38_db_schema.py",
    "plugins/agent-bridge/tests/fixtures/schema38_inbox_custody.py",
    "plugins/agent-bridge/tests/fixtures/schema38_db_live_session_aliases.py",
    "plugins/agent-bridge/tests/delivery.test.mjs",
    "plugins/agent-bridge/tests/retained-extension.test.mjs",
    "plugins/agent-bridge/tests/plugin-readiness.test.mjs",
    "plugins/agent-bridge/extensions/agent-bridge/delivery.mjs",
    "plugins/agent-bridge/extensions/agent-bridge/extension.mjs",
)
CAPABILITY_VERSIONS = {
    "live_keyed_receipt": 1, "live_exact_notify": 1,
    "bridge-session-observations-v1": 1,
    "bridge-session-observation-history-v1": 1,
    "bridge-owner-membership-v1": 1,
}
PROBES = {
    "test_http51_capture_actual_observations": 2,
    "test_concurrent_stopped_retries_reconcile_after_actual_resume_state_race": 1,
    "test_failed_resume_without_a_matching_receipt_remains_a_failure": 1,
    "test_interrupted_pre30_upgrade_reopens_every_legacy_guard_checkpoint": 20,
    "test_http51_observation_boundaries": 2,
    "test_observation_http_resource_boundary": 3,
    "test_observation_cancellation_preserves_other_subscription": 1,
    "test_observer_cli_http_eof": 8,
    "test_http51_observer_payload_abbreviations": 2,
    "test_http51_owner_membership_observations": 2,
    "test_adopted_writable_connection_preserves_membership_commits": 1,
}
POLICY = common.POLICY
encode = common.encode
digest = common.digest
safe = common.safe
contract = common.contract
SPEC = common.EvidenceSpec(
    GENERATION, INPUT, PRIMARY, FAMILY, AFFECTED, EXTRA_PATHS, PROBES,
    (("NATIVE_SEND_IDEMPOTENCY_PROTOCOL_VERSION", 51),
     ("NATIVE_SEND_RECEIPT_CAPABILITY_VERSION", 1),
     ("RETAINED_INBOX_PROTOCOL_VERSION", 48), ("RETAINED_INBOX_PRODUCER_PROTOCOL_VERSION", 49),
     ("RETAINED_INBOX_RECOVERY_PROTOCOL_VERSION", 50)),
    scope=SCOPE,
)
baseline = partial(common.baseline, SPEC)
source_snapshot = partial(common.source_snapshot, SPEC)
source_fixture_path = partial(common.source_fixture_path, SPEC)
history = partial(common.history, SPEC)
build_outputs = partial(common.build_outputs, SPEC)
validate_outputs = partial(common.validate_outputs, SPEC)
validate = partial(common.validate, SPEC)
validate_http51 = validate

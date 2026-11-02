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
SCOPE = "model-free native-send ASGI/journal evidence; not SDK/native qualification"
INPUT = CONTRACT + "inputs/http51/standard-native-send.json"
PRIMARY = previous.CURRENT + "http51-standard-native-send.json"
FAMILY = "http51-standard-native-send-asgi-v1"
AFFECTED = tuple(previous.PREFIX + name for name in (
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
    "plugins/agent-bridge/tests/http51_receipt_probes.py",
    "plugins/agent-bridge/tests/http51_live_cli_probes.py",
    "plugins/agent-bridge/tests/test_native_bound_prompt.py",
    "plugins/agent-bridge/tests/test_live_inbox_runtime.py",
    "plugins/agent-bridge/tests/test_loop_governance_custody.py",
    "plugins/agent-bridge/tests/test_live_keyed_receipt.py",
    "plugins/agent-bridge/tests/test_live_exact_notify.py",
    "plugins/agent-bridge/tests/test_live_exact_rollback.py",
    "plugins/agent-bridge/tests/fixtures/schema38-exact-rollback-source.json",
    "plugins/agent-bridge/tests/delivery.test.mjs",
    "plugins/agent-bridge/tests/retained-extension.test.mjs",
    "plugins/agent-bridge/tests/plugin-readiness.test.mjs",
    "plugins/agent-bridge/extensions/agent-bridge/delivery.mjs",
    "plugins/agent-bridge/extensions/agent-bridge/extension.mjs",
)
CAPABILITY_VERSIONS = {"live_keyed_receipt": 1, "live_exact_notify": 1}
PROBES = {
    "test_http51_capture_actual_observations": 2,
    "test_concurrent_stopped_retries_reconcile_after_actual_resume_state_race": 1,
    "test_failed_resume_without_a_matching_receipt_remains_a_failure": 1,
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

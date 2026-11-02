"""HTTP-50 original-handoff recovery; older captures remain immutable."""

from functools import partial

import bridge_http_retained_evidence as common

previous = common.previous
historical = common.historical
installation = common.installation
CONTRACT = common.CONTRACT
REGISTRY = common.REGISTRY
GENERATION = 50
VECTOR_SCHEMA = "http50-retained-recovery-cases-v1"
CAPTURE = "tools/capture_bridge_http50.py"
SCOPE = "model-free original-handoff recovery source evidence; not SDK/native qualification"
INPUT = CONTRACT + "inputs/http50/retained-recovery.json"
PRIMARY = previous.CURRENT + "http50-retained-recovery.json"
FAMILY = "http50-retained-recovery-asgi-v1"
AFFECTED = tuple(previous.PREFIX + name for name in (
    "protocol.py", "live_inbox_models.py", "live_inbox_runtime.py", "routes/live_inbox.py",
    "db_inbox_custody.py", "db_live_message_receipts.py", "inbox_custody.py",
    "db_core.py", "db_schema.py", "inbox_consumer_schema.py", "session_lifecycle.py",
))
TEST = "plugins/agent-bridge/tests/test_http50_capture_probes.py"
EXTRA_PATHS = (
    INPUT, TEST, "plugins/agent-bridge/tests/test_live_inbox_runtime.py",
    "plugins/agent-bridge/tests/test_loop_governance_custody.py",
)
PROBES = {"test_http50_capture_actual_observations": 2}
POLICY = common.POLICY
encode = common.encode
digest = common.digest
safe = common.safe
contract = common.contract
SPEC = common.EvidenceSpec(
    GENERATION, INPUT, PRIMARY, FAMILY, AFFECTED, EXTRA_PATHS, PROBES,
    (("RETAINED_INBOX_PROTOCOL_VERSION", 48), ("RETAINED_INBOX_PRODUCER_PROTOCOL_VERSION", 49),
     ("RETAINED_INBOX_RECOVERY_PROTOCOL_VERSION", 50)),
)
baseline = partial(common.baseline, SPEC)
source_snapshot = partial(common.source_snapshot, SPEC)
source_fixture_path = partial(common.source_fixture_path, SPEC)
history = partial(common.history, SPEC)
build_outputs = partial(common.build_outputs, SPEC)
validate_outputs = partial(common.validate_outputs, SPEC)
validate = partial(common.validate, SPEC)
validate_http50 = validate

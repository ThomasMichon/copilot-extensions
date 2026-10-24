"""HTTP-48 policy facade; shared mechanics retain its historical scope."""

from functools import partial

import bridge_http_retained_evidence as common

previous = common.previous
historical = common.historical
installation = common.installation
CONTRACT = common.CONTRACT
REGISTRY = common.REGISTRY
GENERATION = 48
VECTOR_SCHEMA = "http48-retained-inbox-cases-v1"
CAPTURE = "tools/capture_bridge_http48.py"
SCOPE = "model-free local ASGI/ordinary-producer source evidence; not SDK/native qualification"
INPUT = CONTRACT + "inputs/http48/retained-inbox.json"
PRIMARY = previous.CURRENT + "http48-retained-inbox.json"
FAMILY = "http48-retained-inbox-asgi-v1"
AFFECTED = tuple(previous.PREFIX + name for name in (
    "protocol.py", "app_routes.py", "live_inbox_models.py", "live_inbox_runtime.py",
    "routes/live_inbox.py", "db_inbox_custody.py", "db_live_sessions.py",
    "inbox_custody.py", "loop_governance.py", "live_message_enqueue.py", "routes/live_sessions.py",
    "db_live_session_aliases.py", "db_live_message_receipts.py",
    "plugin_readiness.py", "routes/live_startup_controls.py",
    "db_core.py", "db_schema.py", "inbox_consumer_schema.py",
))
TEST = "plugins/agent-bridge/tests/test_http48_capture_probes.py"
EXTRA_PATHS = (INPUT, TEST, "plugins/agent-bridge/tests/test_live_inbox_runtime.py",
               "plugins/agent-bridge/tests/test_loop_governance_custody.py")
PROBES = {"test_http48_capture_actual_observations": 2}
POLICY = common.POLICY
encode = common.encode
digest = common.digest
safe = common.safe
contract = common.contract
SPEC = common.EvidenceSpec(
    GENERATION, INPUT, PRIMARY, FAMILY, AFFECTED, EXTRA_PATHS, PROBES,
    (("RETAINED_INBOX_PROTOCOL_VERSION", 48),),
)
baseline = partial(common.baseline, SPEC)
source_snapshot = partial(common.source_snapshot, SPEC)
source_fixture_path = partial(common.source_fixture_path, SPEC)
history = partial(common.history, SPEC)
build_outputs = partial(common.build_outputs, SPEC)
validate_outputs = partial(common.validate_outputs, SPEC)
validate = partial(common.validate, SPEC)
validate_http48 = validate

"""HTTP-52 authorized native replacement; prior captures remain immutable."""

from functools import partial

import bridge_http_retained_evidence as common

previous = common.previous
historical = common.historical
installation = common.installation
CONTRACT = common.CONTRACT
REGISTRY = common.REGISTRY
GENERATION = 52
VECTOR_SCHEMA = "http52-native-replacement-cases-v1"
CAPTURE = "tools/capture_bridge_http52.py"
SCOPE = "model-free replacement client/ASGI/journal evidence; native and ground owners mocked"
INPUT = CONTRACT + "inputs/http52/native-replacement.json"
PRIMARY = previous.CURRENT + "http52-native-replacement.json"
FAMILY = "http52-native-replacement-asgi-v1"
AFFECTED = tuple(previous.PREFIX + name for name in (
    "protocol.py", "client_session_start.py", "routes/sessions.py",
    "session_start_models.py", "workspace_creation.py", "workspace_creation_cli.py",
    "acquisition_cli.py", "acquisition_authority.py", "native_replacement.py",
))
TEST = "plugins/agent-bridge/tests/test_http52_capture_probes.py"
EXTRA_PATHS = (
    INPUT, TEST, CAPTURE, "tools/bridge_http52_evidence.py",
    "plugins/agent-bridge/tests/test_native_replacement.py",
    "plugins/agent-bridge/tests/test_workspace_creation.py",
    "plugins/agent-bridge/tests/test_acquisition_runtime.py",
    "plugins/agent-bridge/tests/test_live_inbox_runtime.py",
)
PROBES = {"test_http52_capture_actual_observations": 2}
POLICY = common.POLICY
encode = common.encode
digest = common.digest
safe = common.safe
contract = common.contract
SPEC = common.EvidenceSpec(
    GENERATION, INPUT, PRIMARY, FAMILY, AFFECTED, EXTRA_PATHS, PROBES,
    (("NATIVE_REPLACEMENT_PROTOCOL_VERSION", 52),
     ("NATIVE_SEND_IDEMPOTENCY_PROTOCOL_VERSION", 51)),
    scope=SCOPE,
)
baseline = partial(common.baseline, SPEC)
source_snapshot = partial(common.source_snapshot, SPEC)
source_fixture_path = partial(common.source_fixture_path, SPEC)
history = partial(common.history, SPEC)
build_outputs = partial(common.build_outputs, SPEC)
validate_outputs = partial(common.validate_outputs, SPEC)
validate = partial(common.validate, SPEC)
validate_http52 = validate

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
SCOPE = retained.SCOPE
POLICY = common.POLICY
AFFECTED = retained.AFFECTED
EXTRA_PATHS = (*retained.EXTRA_PATHS, INPUT, CAPTURE, "tools/bridge_http52_evidence.py")
PROBES = retained.PROBES
TEST = retained.TEST
CAPABILITY_VERSIONS = {**retained.CAPABILITY_VERSIONS, "owner_membership": 52}
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

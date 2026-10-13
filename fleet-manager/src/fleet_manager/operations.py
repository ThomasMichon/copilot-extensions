"""Immutable intent/receipt shapes; parsing grants no authority or persistence."""

from dataclasses import dataclass
import json

from .planning import PlanError, canonical, decode, digest, identifier, integer, record

COMMAND_FIELDS = {
    "task.submit": {"objective"},
    "workspace.allocate": {"repository", "objective"},
    "prompt.deliver": {"session", "message_ref"},
}
SCOPE = ("operation", "authority", "principal", "account", "target", "generation")


def _scope(value):
    for field in SCOPE[:-1]:
        identifier(value[field], field)
    integer(value["generation"], "generation", minimum=1)


@dataclass(frozen=True)
class OperationIntent:
    """Canonical immutable bytes retain caller-supplied identity, not authentication."""

    encoded: bytes

    def __post_init__(self):
        if type(self.encoded) is not bytes:
            raise PlanError("intent must retain immutable canonical bytes")
        value = decode(self.encoded)
        _validate_intent(value)
        if canonical(value) != self.encoded:
            raise PlanError("intent encoding must be canonical")

    @classmethod
    def from_dict(cls, value):
        _validate_intent(value)
        return cls(canonical(value))

    def to_dict(self):
        return json.loads(self.encoded)

    @property
    def request_digest(self):
        return digest(self.to_dict())

    def check_repeat(self, candidate):
        """Compare a prior identity; no lookup or durable deduplication is performed."""
        before, after = self.to_dict(), candidate.to_dict()
        identity = ("authority", "principal", "account", "operation")
        if any(before[field] != after[field] for field in identity):
            raise PlanError("operation identity mismatch")
        if self.encoded != candidate.encoded:
            raise PlanError("operation identity reused with conflicting intent")


def _validate_intent(value):
    canonical(value)
    record(value, "fleet-manager.operation-intent", {*SCOPE, "command", "parameters"})
    _scope(value)
    command = value["command"]
    if not isinstance(command, str) or command not in COMMAND_FIELDS:
        raise PlanError("unsupported operation command")
    parameters = value["parameters"]
    if not isinstance(parameters, dict) or set(parameters) != COMMAND_FIELDS[command]:
        raise PlanError("invalid command parameter fields")
    for field, parameter in parameters.items():
        identifier(parameter, field)


@dataclass(frozen=True)
class TargetReceipt:
    """Target claims acceptance/outcome; does not settle the parent objective."""

    encoded: bytes

    def __post_init__(self):
        if type(self.encoded) is not bytes:
            raise PlanError("receipt must retain immutable canonical bytes")
        value = decode(self.encoded)
        _validate_receipt(value)
        if canonical(value) != self.encoded:
            raise PlanError("receipt encoding must be canonical")

    @classmethod
    def from_dict(cls, value):
        _validate_receipt(value)
        return cls(canonical(value))

    def to_dict(self):
        return json.loads(self.encoded)

    def check_intent(self, intent):
        value, request = self.to_dict(), intent.to_dict()
        if any(value[field] != request[field] for field in (*SCOPE, "command")):
            raise PlanError("receipt scope or generation mismatch")
        if value["request_digest"] != intent.request_digest:
            raise PlanError("receipt content correlation mismatch")


@dataclass(frozen=True)
class AllocationRelease:
    """Immutable cooperative retirement assertion; parsing does not authenticate it."""

    encoded: bytes

    def __post_init__(self):
        if type(self.encoded) is not bytes:
            raise PlanError("release must retain immutable canonical bytes")
        value = decode(self.encoded)
        canonical(value)
        record(value, "fleet-manager.allocation-release",
               {*SCOPE, "command", "request_digest", "resource", "installation"})
        _scope(value)
        if value["command"] != "workspace.allocate":
            raise PlanError("release requires workspace.allocate")
        _fingerprint(value["request_digest"])
        identifier(value["resource"], "release resource")
        identifier(value["installation"], "release installation")
        if canonical(value) != self.encoded:
            raise PlanError("release encoding must be canonical")

    @classmethod
    def from_dict(cls, value):
        return cls(canonical(value))

    def to_dict(self):
        return json.loads(self.encoded)

    def check_intent(self, intent, *, resource, installation):
        value, request = self.to_dict(), intent.to_dict()
        if any(value[field] != request[field] for field in (*SCOPE, "command")):
            raise PlanError("release scope or generation mismatch")
        if value["request_digest"] != intent.request_digest:
            raise PlanError("release content correlation mismatch")
        if value["resource"] != resource or value["installation"] != installation:
            raise PlanError("release resource or installation mismatch")


def _fingerprint(value):
    if (not isinstance(value, str) or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)):
        raise PlanError("request digest must be lowercase SHA-256")


def _validate_receipt(value):
    canonical(value)
    record(value, "fleet-manager.target-receipt",
           {*SCOPE, "command", "request_digest", "sequence", "status", "result_ref"})
    _scope(value)
    if not isinstance(value["command"], str) or value["command"] not in COMMAND_FIELDS:
        raise PlanError("unsupported receipt command")
    _fingerprint(value["request_digest"])
    integer(value["sequence"], "receipt sequence", minimum=1)
    status = value["status"]
    if status not in ("accepted", "succeeded", "failed", "unknown"):
        raise PlanError("unsupported receipt status")
    if status in ("accepted", "unknown"):
        if value["result_ref"] is not None:
            raise PlanError("acceptance or uncertainty cannot assert an outcome reference")
    else:
        identifier(value["result_ref"], "outcome reference")

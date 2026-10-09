from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from typing import ClassVar

MAX_MESSAGE_BYTES = 65536
MAX_TARGETS = 256
MAX_SERVICES = 32
MAX_QUERY_BYTES = 4096
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", re.ASCII)
_REVISION = re.compile(r"sha256:[a-f0-9]{64}", re.ASCII)
_CAPABILITIES = frozenset({"ssh", "create", "delete", "lease", "bootstrap"})
_OPERATIONS = {
    "health-v1": frozenset({"health"}),
    "index-v1": frozenset({"health", "index.search"}),
}


class ContractError(ValueError):
    """Invalid contract; diagnostics deliberately omit supplied values."""


def _identifier(value: object, field: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ContractError(f"{field} must be a bounded identifier")


def _integer(value: object, field: str, *, minimum: int = 1, maximum: int = 2**63 - 1) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ContractError(f"{field} must be an integer in [{minimum}, {maximum}]")


def _text(value: object, field: str, maximum: int) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{field} must be nonempty text")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ContractError(f"{field} must be UTF-8 text") from exc
    if size > maximum or any(ord(c) < 32 and c not in "\t\n\r" for c in value):
        raise ContractError(f"{field} exceeds its text bounds")


def _object(value: object, required: set[str], optional: set[str] | None = None) -> dict:
    if not isinstance(value, dict):
        raise ContractError("record must be an object")
    if not required <= value.keys() or value.keys() - required - (optional or set()):
        raise ContractError("record has missing or unexpected fields")
    return value


def _wire(value: object, schema: str, fields: set[str]) -> dict:
    data = _object(value, fields | {"schema", "version"})
    if data["schema"] != schema or type(data["version"]) is not int or data["version"] != 1:
        raise ContractError("unsupported contract schema or version")
    return data


def _tuple(value: object, field: str, maximum: int, *, empty: bool = False) -> tuple:
    if not isinstance(value, (list, tuple)) or len(value) > maximum or (not value and not empty):
        raise ContractError(f"{field} must be a bounded collection")
    return tuple(value)


def _distinct(values: tuple, field: str) -> None:
    if len(set(values)) != len(values):
        raise ContractError(f"{field} contains duplicate identities")


def _check_tree(value: object, depth: int = 0) -> None:
    if depth > 16:
        raise ContractError("JSON nesting exceeds the contract limit")
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ContractError("JSON object keys must be strings")
            _check_tree(child, depth + 1)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _check_tree(child, depth + 1)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ContractError("JSON contains a nonfinite number")


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ContractError("JSON object contains duplicate fields")
        result[key] = value
    return result


def _nonfinite(_: str) -> None:
    raise ContractError("JSON contains a nonfinite number")


def decode_json(payload: bytes) -> dict:
    if not isinstance(payload, bytes) or len(payload) > MAX_MESSAGE_BYTES:
        raise ContractError("JSON message exceeds the contract byte limit")
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_nonfinite)
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, ContractError):
            raise
        raise ContractError("invalid JSON message") from exc
    _check_tree(value)
    if not isinstance(value, dict):
        raise ContractError("JSON message must be an object")
    return value


def encode_json(value: dict) -> bytes:
    _check_tree(value)
    if not isinstance(value, dict):
        raise ContractError("JSON message must be an object")
    try:
        payload = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        encoded = payload.encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
        raise ContractError("invalid JSON message") from exc
    if len(encoded) > MAX_MESSAGE_BYTES:
        raise ContractError("JSON message exceeds the contract byte limit")
    return encoded


class _WireRecord:
    SCHEMA: ClassVar[str]

    def to_dict(self) -> dict:
        return {"schema": self.SCHEMA, "version": 1, **asdict(self)}


@dataclass(frozen=True)
class TargetRef:
    driver: str
    provider_instance: str
    target_id: str

    def __post_init__(self) -> None:
        for name in ("driver", "provider_instance", "target_id"):
            _identifier(getattr(self, name), name)

    @classmethod
    def from_dict(cls, value: object) -> TargetRef:
        data = _object(value, {"driver", "provider_instance", "target_id"})
        return cls(**data)


@dataclass(frozen=True)
class DriverTarget:
    target_id: str
    state: str
    capabilities: tuple[str, ...]
    reason: str | None = None

    def __post_init__(self) -> None:
        _identifier(self.target_id, "target_id")
        if self.state not in ("configured", "unavailable"):
            raise ContractError("unsupported target state")
        if type(self.capabilities) is not tuple:
            raise ContractError("capabilities must be an immutable collection")
        values = _tuple(self.capabilities, "capabilities", len(_CAPABILITIES), empty=True)
        if any(not isinstance(item, str) or item not in _CAPABILITIES for item in values):
            raise ContractError("unsupported driver capability")
        _distinct(values, "capabilities")
        if self.state == "configured" and self.reason is not None:
            raise ContractError("configured target cannot carry an unavailable reason")
        if self.state == "unavailable" and self.reason not in (
            "not-configured", "provider-unavailable", "unsupported",
        ):
            raise ContractError("unavailable target requires a bounded reason code")

    @classmethod
    def from_dict(cls, value: object) -> DriverTarget:
        data = _object(value, {"target_id", "state", "capabilities", "reason"})
        return cls(
            data["target_id"], data["state"],
            _tuple(data["capabilities"], "capabilities", len(_CAPABILITIES), empty=True),
            data["reason"],
        )


@dataclass(frozen=True)
class DriverSnapshot(_WireRecord):
    SCHEMA: ClassVar[str] = "agent-fleet.driver-snapshot"
    driver: str
    provider_instance: str
    source_revision: str
    targets: tuple[DriverTarget, ...]

    def __post_init__(self) -> None:
        _identifier(self.driver, "driver")
        _identifier(self.provider_instance, "provider_instance")
        if not isinstance(self.source_revision, str) or not _REVISION.fullmatch(self.source_revision):
            raise ContractError("source_revision must be a SHA-256 source identity")
        if type(self.targets) is not tuple:
            raise ContractError("targets must be an immutable collection")
        values = _tuple(self.targets, "targets", MAX_TARGETS)
        if any(not isinstance(item, DriverTarget) for item in values):
            raise ContractError("targets must contain driver target records")
        _distinct(tuple(item.target_id for item in values), "targets")

    @classmethod
    def from_dict(cls, value: object) -> DriverSnapshot:
        data = _wire(value, cls.SCHEMA, {"driver", "provider_instance", "source_revision", "targets"})
        targets = _tuple(data["targets"], "targets", MAX_TARGETS)
        return cls(data["driver"], data["provider_instance"], data["source_revision"],
                   tuple(DriverTarget.from_dict(item) for item in targets))


@dataclass(frozen=True)
class ServiceOffer:
    service_id: str
    installation_id: str
    adapter: str
    operations: tuple[str, ...]

    def __post_init__(self) -> None:
        _identifier(self.service_id, "service_id")
        _identifier(self.installation_id, "installation_id")
        if not isinstance(self.adapter, str) or self.adapter not in _OPERATIONS:
            raise ContractError("unsupported service adapter")
        if type(self.operations) is not tuple:
            raise ContractError("operations must be an immutable collection")
        values = _tuple(self.operations, "operations", 2)
        if any(not isinstance(item, str) or item not in _OPERATIONS[self.adapter] for item in values):
            raise ContractError("operation is not supported by this adapter")
        _distinct(values, "operations")

    @classmethod
    def from_dict(cls, value: object) -> ServiceOffer:
        data = _object(value, {"service_id", "installation_id", "adapter", "operations"})
        return cls(data["service_id"], data["installation_id"], data["adapter"],
                   _tuple(data["operations"], "operations", 2))


@dataclass(frozen=True)
class ConnectorRegistration(_WireRecord):
    SCHEMA: ClassVar[str] = "agent-fleet.connector-registration"
    fleet_id: str
    target: TargetRef
    connector_id: str
    generation: int
    expires_at: int
    services: tuple[ServiceOffer, ...]

    def __post_init__(self) -> None:
        _identifier(self.fleet_id, "fleet_id")
        _identifier(self.connector_id, "connector_id")
        if not isinstance(self.target, TargetRef):
            raise ContractError("target must be a target reference")
        _integer(self.generation, "generation")
        _integer(self.expires_at, "expires_at")
        if type(self.services) is not tuple:
            raise ContractError("services must be an immutable collection")
        values = _tuple(self.services, "services", MAX_SERVICES, empty=True)
        if any(not isinstance(item, ServiceOffer) for item in values):
            raise ContractError("services must contain service offers")
        _distinct(tuple(item.service_id for item in values), "services")

    def check_live(self, now: int, *, max_ttl: int = 600) -> None:
        _integer(now, "now", minimum=0)
        _integer(max_ttl, "max_ttl", maximum=3600)
        if not now < self.expires_at <= now + max_ttl:
            raise ContractError("registration is expired or exceeds permitted lifetime")

    @classmethod
    def from_dict(cls, value: object) -> ConnectorRegistration:
        data = _wire(value, cls.SCHEMA, {
            "fleet_id", "target", "connector_id", "generation", "expires_at", "services",
        })
        services = _tuple(data["services"], "services", MAX_SERVICES, empty=True)
        return cls(data["fleet_id"], TargetRef.from_dict(data["target"]), data["connector_id"],
                   data["generation"], data["expires_at"],
                   tuple(ServiceOffer.from_dict(item) for item in services))


@dataclass(frozen=True)
class SearchParameters:
    q: str
    limit: int = 10
    source: str | None = None
    language: str | None = None
    repo: str | None = None

    def __post_init__(self) -> None:
        _text(self.q, "q", MAX_QUERY_BYTES)
        _integer(self.limit, "limit", maximum=100)
        for name in ("source", "language", "repo"):
            value = getattr(self, name)
            if value is not None:
                _text(value, name, 256)
                if any(ord(c) < 32 for c in value):
                    raise ContractError(f"{name} cannot contain control characters")

    @classmethod
    def from_dict(cls, value: object) -> SearchParameters:
        data = _object(value, {"q"}, {"limit", "source", "language", "repo"})
        return cls(**data)


@dataclass(frozen=True)
class RouteRequest(_WireRecord):
    SCHEMA: ClassVar[str] = "agent-fleet.route-request"
    fleet_id: str
    target: TargetRef
    connector_id: str
    generation: int
    service_id: str
    installation_id: str
    request_id: str
    operation: str
    parameters: SearchParameters | None

    def __post_init__(self) -> None:
        for name in ("fleet_id", "connector_id", "service_id", "installation_id", "request_id"):
            _identifier(getattr(self, name), name)
        if not isinstance(self.target, TargetRef):
            raise ContractError("target must be a target reference")
        _integer(self.generation, "generation")
        if self.operation == "health":
            if self.parameters is not None:
                raise ContractError("health operation cannot carry parameters")
        elif self.operation == "index.search":
            if not isinstance(self.parameters, SearchParameters):
                raise ContractError("index.search requires validated search parameters")
        else:
            raise ContractError("unsupported routing operation")

    def check_offer(
        self, registration: ConnectorRegistration, now: int, *, max_ttl: int = 600,
    ) -> ServiceOffer:
        registration.check_live(now, max_ttl=max_ttl)
        if (
            self.fleet_id != registration.fleet_id or self.target != registration.target
            or self.connector_id != registration.connector_id
            or self.generation != registration.generation
        ):
            raise ContractError("request does not match the current connector registration")
        for offer in registration.services:
            if offer.service_id == self.service_id:
                if self.installation_id != offer.installation_id or self.operation not in offer.operations:
                    raise ContractError("request does not match the offered service operation")
                return offer
        raise ContractError("service is not offered by this connector")

    @classmethod
    def from_dict(cls, value: object) -> RouteRequest:
        data = _wire(value, cls.SCHEMA, {
            "fleet_id", "target", "connector_id", "generation", "service_id",
            "installation_id", "request_id", "operation", "parameters",
        })
        parameters = data["parameters"]
        if parameters is not None:
            parameters = SearchParameters.from_dict(parameters)
        return cls(
            data["fleet_id"], TargetRef.from_dict(data["target"]), data["connector_id"],
            data["generation"], data["service_id"], data["installation_id"],
            data["request_id"], data["operation"], parameters,
        )

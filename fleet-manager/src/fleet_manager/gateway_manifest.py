"""Bounded desired Gateway state; parsing confers no authority or transport.

References are opaque, non-secret names: alphanumeric-led ASCII segments using
``_.-``, separated by ``/``, with an optional final ``@version`` of the same
form. They are never resolved or authenticated here. This grammar excludes
URLs, executable syntax and credential assignments, not every possible secret.
Roles and capabilities declare intent, not grants or implemented transports.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import planning

SCHEMA = "fleet-manager.gateway-manifest"
MAX_REVISION = 2**53 - 1
_REFERENCE = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9_.-]*(?:/[A-Za-z0-9][A-Za-z0-9_.-]*)*"
    r"(?:@[A-Za-z0-9][A-Za-z0-9_.-]*)?\Z"
)
_PREFIX = re.compile(r"/[A-Za-z0-9._~-]+(?:/[A-Za-z0-9._~-]+)*\Z")
_RESERVED = ("/v1", "/_fleet", "/health", "/ready", "/metrics", "/status")
_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
_CAPABILITIES = frozenset({"http", "response-stream"})


class ManifestError(ValueError):
    """A static, value-free reason code; supplied data never enters diagnostics."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ManifestError(code)


def _fields(
    value: object, required: set[str], optional: frozenset[str] = frozenset()
) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ManifestError("invalid-fields")
    _require(required <= value.keys() and not value.keys() - required - optional,
             "invalid-fields")
    return value


def _items(value: object, maximum: int, *, minimum: int = 1) -> list[object]:
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ManifestError("collection-limit")
    return value


def _identifier(value: object) -> str:
    try:
        planning.identifier(value, "identifier")
    except planning.PlanError:
        raise ManifestError("invalid-identifier") from None
    assert isinstance(value, str)
    return value


def _reference(value: object) -> str:
    if not isinstance(value, str) or not 0 < len(value) <= 256:
        raise ManifestError("invalid-reference")
    _require(_REFERENCE.fullmatch(value) is not None, "invalid-reference")
    return value


def _overlap(left: str, right: str) -> bool:
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def _prefix(value: object) -> str:
    if not isinstance(value, str) or not 0 < len(value) <= 256:
        raise ManifestError("invalid-prefix")
    _require(_PREFIX.fullmatch(value) is not None, "invalid-prefix")
    _require(not any(segment in (".", "..") for segment in value.split("/")),
             "invalid-prefix")
    _require(not any(_overlap(value, reserved) for reserved in _RESERVED),
             "reserved-prefix")
    return value


def _choices(value: object, allowed: frozenset[str], code: str) -> tuple[str, ...]:
    items = _items(value, len(allowed))
    if not all(isinstance(item, str) for item in items):
        raise ManifestError(code)
    selected = tuple(item for item in items if isinstance(item, str))
    _require(len(set(selected)) == len(selected) and set(selected) <= allowed, code)
    return tuple(sorted(selected))


@dataclass(frozen=True)
class KeySourceRef:
    """An unresolved non-secret key-source declaration."""

    name: str
    provider: str
    reference: str


@dataclass(frozen=True)
class RouteSpec:
    """A namespace and declared HTTP intent, not evidence of serving support."""

    prefix: str
    methods: tuple[str, ...]
    capabilities: tuple[str, ...]


@dataclass(frozen=True)
class ServiceSlot:
    """An explicit desired identity assignment without inferred privileges."""

    name: str
    role: str
    principal: str
    key_source: str
    routes: tuple[RouteSpec, ...]
    configuration_ref: str | None


def _key_source(value: object) -> KeySourceRef:
    fields = _fields(value, {"name", "provider", "reference"})
    return KeySourceRef(_identifier(fields["name"]), _identifier(fields["provider"]),
                        _reference(fields["reference"]))


def _route(value: object) -> RouteSpec:
    fields = _fields(value, {"prefix", "methods", "capabilities"})
    capabilities = _choices(fields["capabilities"], _CAPABILITIES, "invalid-capabilities")
    _require("http" in capabilities, "invalid-capabilities")
    return RouteSpec(_prefix(fields["prefix"]),
                     _choices(fields["methods"], _METHODS, "invalid-methods"), capabilities)


def _slot(value: object, roles: tuple[str, ...], sources: set[str]) -> ServiceSlot:
    fields = _fields(value, {"name", "role", "principal", "key_source", "routes"},
                     frozenset({"configuration_ref"}))
    role = _identifier(fields["role"])
    source = _identifier(fields["key_source"])
    _require(role in roles, "undeclared-role")
    _require(source in sources, "undeclared-key-source")
    routes = tuple(sorted((_route(item) for item in _items(fields["routes"], 16)),
                          key=lambda route: route.prefix))
    config = _reference(fields["configuration_ref"]) if "configuration_ref" in fields else None
    return ServiceSlot(_identifier(fields["name"]), role, _identifier(fields["principal"]),
                       source, routes, config)


@dataclass(frozen=True)
class GatewayManifest:
    """Immutable normalized desired state. Use load/parse for untrusted input."""

    fleet: str
    gateway: str
    revision: int
    roles: tuple[str, ...]
    key_sources: tuple[KeySourceRef, ...]
    slots: tuple[ServiceSlot, ...]

    @classmethod
    def load(cls, value: object) -> GatewayManifest:
        """Validate the complete bounded JSON tree and normalize unordered lists."""
        try:
            planning.canonical(value)
        except planning.PlanError:
            raise ManifestError("invalid-json") from None
        fields = _fields(value, {"schema", "version", "fleet", "gateway", "revision",
                                 "roles", "key_sources", "slots"})
        _require(fields["schema"] == SCHEMA and type(fields["version"]) is int
                 and fields["version"] == 1, "unsupported-schema")
        revision = fields["revision"]
        if type(revision) is not int or not 1 <= revision <= MAX_REVISION:
            raise ManifestError("invalid-revision")
        roles = tuple(sorted(_identifier(item) for item in _items(fields["roles"], 64)))
        _require(len(set(roles)) == len(roles), "duplicate-role")
        sources = tuple(sorted(
            (_key_source(item) for item in _items(fields["key_sources"], 128)),
            key=lambda source: source.name,
        ))
        source_names = {source.name for source in sources}
        _require(len(source_names) == len(sources), "duplicate-key-source")
        slots = tuple(sorted(
            (_slot(item, roles, source_names) for item in _items(fields["slots"], 64, minimum=0)),
            key=lambda slot: slot.name,
        ))
        _require(len({slot.name for slot in slots}) == len(slots), "duplicate-slot")
        prefixes = [route.prefix for slot in slots for route in slot.routes]
        _require(len(prefixes) <= 256, "route-limit")
        for index, prefix in enumerate(prefixes):
            _require(not any(_overlap(prefix, other) for other in prefixes[:index]),
                     "overlapping-prefix")
        return cls(_identifier(fields["fleet"]), _identifier(fields["gateway"]),
                   revision, roles, sources, slots)

    @classmethod
    def parse(cls, raw: bytes) -> GatewayManifest:
        """Decode strict bounded JSON, rejecting duplicate keys and nonfinite values."""
        try:
            value = planning.decode(raw)
        except planning.PlanError:
            raise ManifestError("invalid-json") from None
        return cls.load(value)

    def to_dict(self) -> dict[str, object]:
        """Return a fresh JSON tree with no mutable aliases to domain state."""
        slots: list[dict[str, object]] = []
        for slot in self.slots:
            item: dict[str, object] = {
                "name": slot.name, "role": slot.role, "principal": slot.principal,
                "key_source": slot.key_source,
                "routes": [{"prefix": route.prefix, "methods": list(route.methods),
                            "capabilities": list(route.capabilities)} for route in slot.routes],
            }
            if slot.configuration_ref is not None:
                item["configuration_ref"] = slot.configuration_ref
            slots.append(item)
        return {
            "schema": SCHEMA, "version": 1, "fleet": self.fleet, "gateway": self.gateway,
            "revision": self.revision, "roles": list(self.roles),
            "key_sources": [{"name": source.name, "provider": source.provider,
                             "reference": source.reference} for source in self.key_sources],
            "slots": slots,
        }

    def canonical_bytes(self) -> bytes:
        """Return the existing planning format's canonical JSON bytes."""
        try:
            return planning.canonical(self.to_dict())
        except planning.PlanError:
            raise ManifestError("invalid-json") from None

    @property
    def digest(self) -> str:
        """Return a canonical SHA-256 hex identity including the desired revision."""
        try:
            return planning.digest(self.to_dict())
        except planning.PlanError:
            raise ManifestError("invalid-json") from None

"""Pure pool definitions; no registry publication or allocation authority."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .registrar import Filters, RegistrarError, _as_int, _load_filters
from .registrations import RegistrationKind

POOL_DEFINITION_VERSION = 1


class PoolDefinitionError(RegistrarError):
    """A pool definition is incomplete or unsupported for placement."""


def filters_to_dict(filters: Filters) -> dict[str, dict[str, list[str]]]:
    """Render the complete predicate without losing shorthand or reject rules."""
    return {
        "permit": {dim: sorted(values) for dim, values in sorted(filters.permit.items())},
        "reject": {dim: sorted(values) for dim, values in sorted(filters.reject.items())},
    }


def _identity_value(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PoolDefinitionError(f"pool instance requires an explicit {field}")
    return value


@dataclass(frozen=True)
class PoolInstanceDefinition:
    """One machine/environment capacity owner, independent of native identity."""

    registration_id: str
    machine: str
    env: str
    priority: int
    max_concurrent: int
    filters: Filters

    def __post_init__(self) -> None:
        for name in ("registration_id", "machine", "env"):
            _identity_value(getattr(self, name), name)
        try:
            _as_int(self.priority, key="priority")
            _as_int(self.max_concurrent, key="max_concurrent", minimum=1)
            normalized = _load_filters(filters_to_dict(self.filters))
        except RegistrarError as exc:
            raise PoolDefinitionError(str(exc)) from exc
        object.__setattr__(
            self, "filters",
            Filters(
                permit=MappingProxyType(dict(normalized.permit)),
                reject=MappingProxyType(dict(normalized.reject)),
            ),
        )

    @property
    def identity(self) -> tuple[str, str, str]:
        """Stable key; changing priority or filters does not change capacity owner."""
        return self.registration_id, self.machine, self.env

    def to_dict(self) -> dict[str, object]:
        """Lossless JSON data, not an executing-runtime capability declaration."""
        return {
            "version": POOL_DEFINITION_VERSION,
            "registration_id": self.registration_id,
            "machine": self.machine,
            "env": self.env,
            "priority": self.priority,
            "max_concurrent": self.max_concurrent,
            "filters": filters_to_dict(self.filters),
        }

    @classmethod
    def from_dict(cls, data: object) -> PoolInstanceDefinition:
        """Read version 1; tolerate additive fields but refuse missing semantics."""
        if not isinstance(data, Mapping):
            raise PoolDefinitionError("pool definition must be a mapping")
        if type(data.get("version")) is not int or data["version"] != POOL_DEFINITION_VERSION:
            raise PoolDefinitionError("unsupported pool definition version")
        filters = data.get("filters")
        if not isinstance(filters, Mapping) or not {"permit", "reject"} <= filters.keys():
            raise PoolDefinitionError("pool definition requires complete permit/reject filters")
        if any(not isinstance(filters[side], Mapping) for side in ("permit", "reject")):
            raise PoolDefinitionError("pool definition filter sides must be mappings")
        try:
            return cls(
                registration_id=_identity_value(data.get("registration_id"), "registration_id"),
                machine=_identity_value(data.get("machine"), "machine"),
                env=_identity_value(data.get("env"), "env"),
                priority=_as_int(data.get("priority"), key="priority"),
                max_concurrent=_as_int(data.get("max_concurrent"), key="max_concurrent", minimum=1),
                filters=_load_filters(filters),
            )
        except RegistrarError as exc:
            raise PoolDefinitionError(str(exc)) from exc

    @classmethod
    def from_registration(cls, registration: object) -> PoolInstanceDefinition:
        """Read an in-process definition without guessing a legacy row's filters."""
        if not isinstance(registration, Mapping):
            raise PoolDefinitionError("pool registration must be a mapping")
        if registration.get("kind") != RegistrationKind.SUPERVISED_LANE:
            raise PoolDefinitionError("only supervised worker lanes define pool instances")
        data = registration.get("pool_definition")
        if not isinstance(data, Mapping):
            raise PoolDefinitionError("registration has no complete pool definition")
        definition = cls.from_dict(data)
        if definition.identity != (
            registration.get("id"), registration.get("machine"), registration.get("env"),
        ):
            raise PoolDefinitionError("pool definition conflicts with registration scope")
        spec = registration.get("spec")
        if not isinstance(spec, Mapping):
            raise PoolDefinitionError("pool definition conflicts with registration capacity")
        try:
            capacity = _as_int(spec.get("max_concurrent", 1), key="max_concurrent", minimum=1)
        except RegistrarError as exc:
            raise PoolDefinitionError(str(exc)) from exc
        if capacity != definition.max_concurrent:
            raise PoolDefinitionError("pool definition conflicts with registration capacity")
        return definition


def ordered_pool_instances(
    definitions: Iterable[PoolInstanceDefinition],
) -> tuple[PoolInstanceDefinition, ...]:
    """Order an already-effective set; duplicate identities indicate a bad snapshot."""
    instances = tuple(definitions)
    identities = [instance.identity for instance in instances]
    if len(set(identities)) != len(identities):
        raise PoolDefinitionError("effective pool set contains duplicate instance identities")
    return tuple(sorted(instances, key=lambda instance: (-instance.priority, *instance.identity)))

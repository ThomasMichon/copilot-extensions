"""Explicit non-secret role/provider references, without lifecycle authority."""

from __future__ import annotations

from dataclasses import dataclass

from .planning import MAX_ITEMS, PlanError, canonical, identifier, record


def reference(value: object, label: str) -> str:
    identifier(value, label)
    if not isinstance(value, str):
        raise PlanError("role binding reference must be text")
    return value


def references(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > MAX_ITEMS:
        raise PlanError("role binding references must be a bounded collection")
    result = tuple(reference(item, label) for item in value)
    if len(set(result)) != len(result):
        raise PlanError("role binding references must be unique")
    return tuple(sorted(result))


def fields(value: object, required: set[str], optional: set[str] | None = None) -> dict[str, object]:
    if (
        not isinstance(value, dict)
        or not required <= value.keys()
        or value.keys() - required - (optional or set())
    ):
        raise PlanError("role binding has missing or unsupported fields")
    return value


@dataclass(frozen=True)
class ProviderInstance:
    name: str
    implementation: str
    instance_ref: str
    target: str
    configuration_ref: str
    bootstrap_ref: str

    def __post_init__(self) -> None:
        for value in (
            self.name, self.implementation, self.instance_ref, self.target,
            self.configuration_ref, self.bootstrap_ref,
        ):
            reference(value, "provider reference")

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name, "implementation": self.implementation,
            "instance_ref": self.instance_ref, "target": self.target,
            "configuration_ref": self.configuration_ref, "bootstrap_ref": self.bootstrap_ref,
        }


@dataclass(frozen=True)
class RoleBinding:
    role: str
    implementation: str
    provider: str
    target: str
    resource: str
    artifact_ref: str
    configuration_ref: str
    key_refs: tuple[str, ...] = ()
    requires: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for value in (
            self.role, self.implementation, self.provider, self.target, self.resource,
            self.artifact_ref, self.configuration_ref,
        ):
            reference(value, "role reference")
        for name in ("key_refs", "requires", "depends_on"):
            value = getattr(self, name)
            if not isinstance(value, tuple) or value != references(value, name):
                raise PlanError("role binding references must be canonical tuples")

    def as_dict(self) -> dict:
        return {
            "role": self.role, "implementation": self.implementation,
            "provider": self.provider, "target": self.target, "resource": self.resource,
            "artifact_ref": self.artifact_ref, "configuration_ref": self.configuration_ref,
            "key_refs": list(self.key_refs), "requires": list(self.requires),
            "depends_on": list(self.depends_on),
        }


@dataclass(frozen=True)
class RoleResource:
    role: str
    resource_type: str
    instance_ref: str
    target: str
    resource_ref: str
    artifact_ref: str
    configuration_ref: str
    key_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        for value in (
            self.role, self.resource_type, self.instance_ref, self.target,
            self.resource_ref, self.artifact_ref, self.configuration_ref,
        ):
            reference(value, "resource reference")
        if not isinstance(self.key_refs, tuple) or self.key_refs != references(self.key_refs, "key reference"):
            raise PlanError("resource key references must be canonical tuples")

    def as_dict(self) -> dict:
        return {
            "role": self.role, "resource_type": self.resource_type,
            "instance_ref": self.instance_ref, "target": self.target,
            "resource_ref": self.resource_ref, "artifact_ref": self.artifact_ref,
            "configuration_ref": self.configuration_ref, "key_refs": list(self.key_refs),
        }


def parse_bindings(value: object) -> tuple[tuple[ProviderInstance, ...], tuple[RoleBinding, ...]]:
    canonical(value)
    namespace = fields(value, {"schema", "version", "providers", "bindings"})
    record(namespace, "fleet-manager.role-bindings", {"providers", "bindings"})
    providers = namespace["providers"]
    bindings = namespace["bindings"]
    if not isinstance(providers, dict) or not isinstance(bindings, dict) or not providers or not bindings:
        raise PlanError("role bindings require explicit providers and bindings")
    instances = []
    identities = set()
    for name, raw in sorted(providers.items()):
        entry = fields(raw, {
            "implementation", "instance_ref", "target", "configuration_ref", "bootstrap_ref",
        })
        instance = ProviderInstance(
            reference(name, "provider name"),
            *(reference(entry[key], "provider reference") for key in (
                "implementation", "instance_ref", "target", "configuration_ref", "bootstrap_ref",
            )),
        )
        if instance.instance_ref in identities:
            raise PlanError("role bindings contain an aliased provider instance")
        identities.add(instance.instance_ref)
        instances.append(instance)
    result = []
    owners = set()
    for role, raw in sorted(bindings.items()):
        entry = fields(
            raw,
            {"implementation", "provider", "target", "resource", "artifact_ref", "configuration_ref"},
            {"key_refs", "requires", "depends_on"},
        )
        binding = RoleBinding(
            reference(role, "role id"),
            *(reference(entry[key], "role reference") for key in (
                "implementation", "provider", "target", "resource", "artifact_ref", "configuration_ref",
            )),
            *(references(entry.get(key, []), key) for key in ("key_refs", "requires", "depends_on")),
        )
        if binding.provider not in providers:
            raise PlanError("role binding names an undeclared provider")
        owner = (binding.provider, binding.resource)
        if owner in owners:
            raise PlanError("multiple roles claim the same provider resource")
        owners.add(owner)
        result.append(binding)
    names = {binding.role for binding in result}
    if any(set(binding.depends_on) - names for binding in result):
        raise PlanError("role binding dependency is not explicitly bound")
    return tuple(instances), tuple(result)

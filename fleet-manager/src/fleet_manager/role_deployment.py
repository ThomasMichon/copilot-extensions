"""Join explicit role bindings with the existing non-mutating fleet planner."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from .planning import PlanError, canonical, digest, integer, plan
from .role_bindings import (
    ProviderInstance, RoleBinding, RoleResource, parse_bindings, reference, references,
)


@dataclass(frozen=True)
class ProviderContract:
    implementation: str
    role_implementations: tuple[str, ...]
    capabilities: tuple[str, ...]
    version: int = 1

    def __post_init__(self) -> None:
        reference(self.implementation, "provider implementation")
        integer(self.version, "provider contract version", minimum=1, maximum=1)
        for value in (self.role_implementations, self.capabilities):
            if not isinstance(value, tuple) or value != references(value, "provider contract reference"):
                raise PlanError("provider contract requires canonical reference tuples")
        if not self.role_implementations:
            raise PlanError("provider contract must name supported role implementations")

    def as_dict(self) -> dict:
        return {
            "implementation": self.implementation, "version": self.version,
            "role_implementations": list(self.role_implementations),
            "capabilities": list(self.capabilities),
        }


class RoleProvider(Protocol):
    @property
    def contract(self) -> ProviderContract: ...

    def project(self, instance: ProviderInstance, binding: RoleBinding) -> RoleResource: ...


def _ordered(bindings: tuple[RoleBinding, ...]) -> tuple[RoleBinding, ...]:
    remaining = {binding.role: set(binding.depends_on) for binding in bindings}
    by_name = {binding.role: binding for binding in bindings}
    result = []
    while remaining:
        ready = sorted(name for name, dependencies in remaining.items() if not dependencies)
        if not ready:
            raise PlanError("role binding dependencies contain a cycle")
        for name in ready:
            result.append(by_name[name])
            del remaining[name]
        for dependencies in remaining.values():
            dependencies.difference_update(ready)
    return tuple(result)


def _projection(instance: ProviderInstance, binding: RoleBinding, provider: RoleProvider) -> dict:
    projected = provider.project(instance, binding)
    if not isinstance(projected, RoleResource):
        raise PlanError("provider returned an unsupported role projection")
    expected = RoleResource(
        binding.role, projected.resource_type, instance.instance_ref, binding.target,
        binding.resource, binding.artifact_ref, binding.configuration_ref, binding.key_refs,
    )
    if projected != expected:
        raise PlanError("provider changed the selected role or resource references")
    return projected.as_dict()


def plan_role_deployment(
    template: dict, config: dict, inventory: dict, *,
    providers: Mapping[str, RoleProvider], fleet_id: str, machine_limit: int, now: int | float,
) -> dict:
    """Compile selected references only; provider callbacks are trusted pure code."""
    if not isinstance(providers, Mapping):
        raise PlanError("role providers require an explicit code-registered mapping")
    base = plan(template, config, inventory, fleet_id=fleet_id, machine_limit=machine_limit, now=now)
    preserved = base["suggested_config"]
    if config is None or not isinstance(preserved.get("extensions"), dict):
        raise PlanError("role binding configuration must be explicitly selected")
    namespace = preserved["extensions"].get("role_bindings")
    if namespace is None:
        raise PlanError("role binding configuration must be explicitly selected")
    instances, bindings = parse_bindings(namespace)
    if any(binding.role not in base["suggested_config"]["roles"] for binding in bindings):
        raise PlanError("role binding has no declared role")
    ordered = _ordered(bindings)
    bound_providers = {binding.provider for binding in bindings}
    admitted_targets = frozenset(
        host["id"] for host in inventory["hosts"] if host["owned"] and host["ready"]
    )
    diagnostics: dict[tuple[str, str | None, str | None], set[str]] = {}
    registered: dict[str, tuple[RoleProvider, ProviderContract] | None] = {}
    contracts: dict[str, dict] = {}
    bootstraps = []
    selected: dict[str, tuple[RoleProvider, ProviderContract]] = {}

    def block(code: str, *, role: str | None = None, provider: str | None = None) -> None:
        scope = "role" if role is not None else "provider" if provider is not None else "fleet"
        diagnostics.setdefault((scope, role, provider), set()).add(code)

    def admitted(target: str) -> bool:
        return target in admitted_targets

    for code in base["blockers"]:
        block(code)
    for instance in instances:
        if instance.name not in bound_providers:
            continue
        if instance.implementation not in registered:
            implementation = providers.get(instance.implementation)
            candidate = None
            if implementation is not None:
                contract = implementation.contract
                if not isinstance(contract, ProviderContract) or contract.implementation != instance.implementation:
                    raise PlanError("registered provider contract does not match its selection")
                contracts[instance.implementation] = contract.as_dict()
                candidate = (implementation, contract)
            registered[instance.implementation] = candidate
        candidate = registered[instance.implementation]
        if candidate is None:
            block("provider-implementation-unavailable", provider=instance.name)
        else:
            selected[instance.name] = candidate
        if not admitted(instance.target):
            block("provider-target-not-admitted", provider=instance.name)
        bootstraps.append({
            **instance.as_dict(), "state": "owner-evidence-required",
            "required_authority": "independent-substrate-owner",
            "independence_verified": False, "installed": False,
            "live_readiness_verified": False,
        })
    by_provider = {instance.name: instance for instance in instances}
    projections = []
    blocked_roles: set[str] = set()
    for binding in ordered:
        candidate = selected.get(binding.provider)
        instance = by_provider[binding.provider]
        projected = None
        eligible = not base["blockers"] and admitted(instance.target) and admitted(binding.target)
        if not admitted(binding.target):
            block("role-target-not-admitted", role=binding.role, provider=binding.provider)
        if set(binding.depends_on) & blocked_roles:
            eligible = False
            block("role-dependency-blocked", role=binding.role, provider=binding.provider)
        if candidate is not None:
            implementation, contract = candidate
            if binding.implementation not in contract.role_implementations:
                block("role-implementation-unsupported", role=binding.role, provider=binding.provider)
            elif set(binding.requires) - set(contract.capabilities):
                block("provider-capability-unsupported", role=binding.role, provider=binding.provider)
            elif eligible:
                projected = _projection(instance, binding, implementation)
        if projected is None:
            blocked_roles.add(binding.role)
        projections.append({
            **binding.as_dict(), "projection": projected,
            "state": "blocked" if projected is None else "configured-not-installed",
            "live_readiness_verified": False,
        })
    blockers = [
        {"scope": scope, "role": role, "provider": provider, "codes": sorted(codes)}
        for (scope, role, provider), codes in sorted(
            diagnostics.items(),
            key=lambda item: tuple(value or "" for value in item[0]),
        )
    ]
    result = {
        "schema": "fleet-manager.role-plan", "version": 1, "fleet_id": fleet_id,
        "evidence": "supplied-snapshots-and-provider-contracts-only",
        "scope": "explicit-role-bindings-only",
        "input_digests": {**base["input_digests"], "provider_contracts": digest(contracts)},
        "parent_plan_digest": base["plan_digest"], "valid_until": base["valid_until"],
        "suggested_config": base["suggested_config"], "capacity": base["capacity"],
        "providers": bootstraps, "roles": projections,
        "unbound_roles": sorted(set(base["suggested_config"]["roles"]) - {binding.role for binding in bindings}),
        "unbound_providers": sorted({instance.name for instance in instances} - bound_providers),
        "blockers": blockers, "status": "blocked" if blockers else "review-required",
        "activation_authorized": False, "installed": False,
        "live_readiness_verified": False, "mutations_performed": False, "cleanup_actions": [],
    }
    result["plan_digest"] = digest(result, output=True)
    canonical(result, output=True)
    return result


def verify_role_plan(
    saved: dict, template: dict, config: dict, inventory: dict, *,
    providers: Mapping[str, RoleProvider], fleet_id: str, machine_limit: int, now: int | float,
) -> dict:
    """Recompile exact inputs/contracts; integrity supplies no execution grant."""
    if not isinstance(saved, dict) or "plan_digest" not in saved:
        raise PlanError("invalid saved role plan")
    canonical(saved, output=True)
    body = {key: value for key, value in saved.items() if key != "plan_digest"}
    if digest(body, output=True) != saved["plan_digest"]:
        raise PlanError("saved role plan integrity mismatch")
    current = plan_role_deployment(
        template, config, inventory, providers=providers,
        fleet_id=fleet_id, machine_limit=machine_limit, now=now,
    )
    if current != saved or inventory["expires_at"] <= now:
        raise PlanError("role plan assumptions changed or expired")
    if current["blockers"]:
        raise PlanError("role plan has unresolved blockers")
    return current

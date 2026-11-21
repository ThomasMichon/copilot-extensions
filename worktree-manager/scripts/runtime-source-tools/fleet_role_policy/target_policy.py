"""Determine standalone installation demand from a selected adopter configuration."""
from __future__ import annotations

from .planning import MAX_BYTES as MAX_BYTES, PlanError, configuration_capacity, decode
from .role_bindings import parse_bindings, reference


def service_roles(raw: bytes, target: str) -> tuple[str, ...]:
    """Return the service roles bound to this target, not the provider's host."""
    reference(target, "target machine")
    config = decode(raw)
    if not isinstance(config, dict):
        raise PlanError("Fleet configuration must be an object")
    configuration_capacity(config, fleet_id=reference(config.get("fleet_id"), "fleet id"))
    extensions = config.get("extensions", {})
    if not isinstance(extensions, dict):
        raise PlanError("Fleet extensions must be an object")
    if "role_bindings" not in extensions:
        return ()
    _, bindings = parse_bindings(extensions["role_bindings"])
    roles = config["roles"]
    selected = []
    for binding in bindings:
        role = roles.get(binding.role)
        if not isinstance(role, dict):
            raise PlanError("Fleet binding requires a declared role")
        if binding.target != target:
            continue
        if role.get("kind") not in ("service", "execution"):
            raise PlanError("Fleet binding on selected target has unresolved role kind")
        if role["kind"] == "service":
            selected.append(binding.role)
    return tuple(sorted(selected))

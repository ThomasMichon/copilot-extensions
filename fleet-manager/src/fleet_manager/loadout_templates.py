"""Code-owned, portable role templates; values are references, never credentials."""

from __future__ import annotations

def _slot(name: str) -> dict:
    return {"slot": name}


def actions_arc_template() -> dict:
    """ARC owns pods; supporting allocations must be explicitly selected."""
    references = (
        "scale_set", "forge_scope_ref", "runner_group_ref", "architecture",
        "artifact_ref", "configuration_ref", "credential_ref", "trust_policy_ref",
        "toolchain_ref", "cluster", "controller", "credential_service", "network",
    )
    numbers = (
        "min_runners", "max_runners", "cpu_millis", "memory_bytes", "disk_bytes",
        "limit_cpu_millis", "limit_memory_bytes", "limit_disk_bytes",
    )
    slot = _slot
    return {
        "schema": "fleet-manager.loadout-template", "version": 1,
        "id": "actions.arc", "revision": "1", "source": "fleet-manager",
        "slots": {
            **{name: {"type": "reference"} for name in references},
            **{name: {"type": "integer", "minimum": 0} for name in numbers},
        },
        "provides": ["actions.runner"],
        "requires": {capability: slot(name) for name, capability in (
            ("cluster", "kubernetes.cluster"), ("controller", "arc.controller"),
            ("credential_service", "forge.credential"), ("network", "runner.network"),
        )},
        "resources": {
            "units": slot("max_runners"),
            "reservation": {name: slot(name) for name in (
                "cpu_millis", "memory_bytes", "disk_bytes",
            )},
            "limit": {name: slot(f"limit_{name}") for name in (
                "cpu_millis", "memory_bytes", "disk_bytes",
            )},
        },
        "configuration": {
            "adapter": "helm.arc", "scale_set": slot("scale_set"),
            **{name: slot(name) for name in references if name != "scale_set"},
            "min_runners": slot("min_runners"), "max_runners": slot("max_runners"),
        },
    }


def builtin_templates() -> list[dict]:
    return [actions_arc_template()]

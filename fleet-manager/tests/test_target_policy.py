import json

import pytest

from fleet_manager.planning import PlanError
from fleet_manager.target_policy import service_roles


@pytest.mark.parametrize(("kind", "target", "expected"), [
    ("service", "worker", ("gateway",)),
    ("service", "provider", ()),
    ("execution", "worker", ()),
])
def test_target_binding(kind: str, target: str, expected: tuple[str, ...]) -> None:
    config = {
        "schema": "fleet-manager.config", "version": 1, "fleet_id": "test", "revision": "one",
        "capacity": {"machine_limit": 2, "execution_hosts": 0}, "roles": {"gateway": {"kind": kind}},
        "extensions": {"role_bindings": {
            "schema": "fleet-manager.role-bindings", "version": 1,
            "providers": {"p": {"implementation": "komodo", "instance_ref": "primary", "target": "provider",
                                "configuration_ref": "cfg", "bootstrap_ref": "boot"}},
            "bindings": {"gateway": {"implementation": "fleet-manager.gateway", "provider": "p",
                                     "target": "worker", "resource": "gateway", "artifact_ref": "app",
                                     "configuration_ref": "cfg"}},
        }},
    }
    assert service_roles(json.dumps(config).encode(), target) == expected
    assert service_roles(json.dumps({**config, "extensions": {}}).encode(), target) == ()
    with pytest.raises(PlanError, match="unresolved role kind"):
        service_roles(json.dumps({**config, "roles": {"gateway": {}}}).encode(), "worker")


def test_invalid_document_is_not_a_negative_decision() -> None:
    with pytest.raises(PlanError):
        service_roles(b"{", "worker")


def test_provider_inventory_without_assignments() -> None:
    provider = {"implementation": "komodo", "instance_ref": "primary", "target": "controller",
                "configuration_ref": "cfg", "bootstrap_ref": "boot"}
    config = {
        "schema": "fleet-manager.config", "version": 1, "fleet_id": "test", "revision": "one",
        "capacity": {"machine_limit": 2, "execution_hosts": 0}, "roles": {},
        "extensions": {"role_bindings": {
            "schema": "fleet-manager.role-bindings", "version": 1,
            "providers": {"p": provider}, "bindings": {},
        }},
    }
    assert service_roles(json.dumps(config).encode(), "new-machine") == ()
    del provider["bootstrap_ref"]
    with pytest.raises(PlanError):
        service_roles(json.dumps(config).encode(), "new-machine")

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import FrozenInstanceError

import pytest

from fleet_manager.planning import MAX_ITEMS, PlanError
from fleet_manager.role_bindings import (
    ProviderInstance, RoleBinding, RoleResource, parse_bindings,
)
from fleet_manager.role_deployment import ProviderContract


def declaration() -> dict:
    return {
        "schema": "fleet-manager.role-bindings", "version": 1,
        "providers": {
            "manager": {
                "implementation": "komodo", "instance_ref": "core-a", "target": "host-a",
                "configuration_ref": "core-config", "bootstrap_ref": "host-bootstrap",
            },
        },
        "bindings": {
            "gateway": {
                "implementation": "fleet-manager.gateway", "provider": "manager",
                "target": "host-a", "resource": "gateway-stack",
                "artifact_ref": "gateway-image", "configuration_ref": "gateway-config",
                "key_refs": ["tls-key", "identity"], "requires": ["named-resource", "container-role"],
            },
            "status": {
                "implementation": "container.service", "provider": "manager",
                "target": "host-a", "resource": "status-stack",
                "artifact_ref": "status-image", "configuration_ref": "status-config",
                "depends_on": ["gateway"],
            },
        },
    }


def test_role_bindings_parse_canonical_immutable_references_without_mutating_input() -> None:
    source = declaration()
    before = copy.deepcopy(source)
    providers, bindings = parse_bindings(source)
    assert source == before
    assert providers[0].instance_ref == "core-a"
    assert providers[0].bootstrap_ref == "host-bootstrap"
    assert bindings[0].key_refs == ("identity", "tls-key")
    assert bindings[0].requires == ("container-role", "named-resource")
    assert bindings[1].depends_on == ("gateway",)
    with pytest.raises(FrozenInstanceError):
        providers[0].target = "other"
    with pytest.raises(FrozenInstanceError):
        bindings[0].target = "other"
    exported = bindings[0].as_dict()
    exported["key_refs"].append("another")
    assert bindings[0].key_refs == ("identity", "tls-key")


@pytest.mark.parametrize("change", [
    {"version": True}, {"version": 2}, {"schema": "unknown"}, {"unknown": True},
    {"providers": []}, {"providers": {}}, {"bindings": []}, {"bindings": {}},
])
def test_role_bindings_reject_unknown_or_malformed_namespace(change: dict) -> None:
    source = declaration()
    source.update(change)
    with pytest.raises(PlanError):
        parse_bindings(source)


@pytest.mark.parametrize("field,value", [
    ("implementation", ""), ("instance_ref", True), ("target", "https://example.com"),
    ("configuration_ref", "../private"), ("bootstrap_ref", None),
])
def test_role_bindings_provider_requires_reference_only_fields(field: str, value: object) -> None:
    source = declaration()
    source["providers"]["manager"][field] = value
    with pytest.raises(PlanError):
        parse_bindings(source)


@pytest.mark.parametrize("field", ["url", "command", "password", "environment", "bootstrap_provider"])
def test_role_bindings_refuse_provider_payload_or_implicit_bootstrap_authority(field: str) -> None:
    source = declaration()
    source["providers"]["manager"][field] = "private-value"
    with pytest.raises(PlanError) as caught:
        parse_bindings(source)
    assert "private-value" not in str(caught.value)


@pytest.mark.parametrize("field", ["command", "url", "environment", "mounts", "token"])
def test_role_bindings_refuse_raw_role_payload_fields(field: str) -> None:
    source = declaration()
    source["bindings"]["gateway"][field] = "private-value"
    with pytest.raises(PlanError) as caught:
        parse_bindings(source)
    assert "private-value" not in str(caught.value)


@pytest.mark.parametrize("field,value", [
    ("implementation", "../module"), ("provider", "missing"), ("target", None),
    ("resource", "stack/name"), ("artifact_ref", ""), ("configuration_ref", False),
    ("key_refs", "key"), ("key_refs", ["same", "same"]), ("requires", [True]),
    ("depends_on", ["missing"]),
])
def test_role_bindings_refuse_invalid_role_references(field: str, value: object) -> None:
    source = declaration()
    source["bindings"]["gateway"][field] = value
    with pytest.raises(PlanError):
        parse_bindings(source)


@pytest.mark.parametrize("implementation", ["komodo", "another-adapter"])
def test_role_bindings_reject_aliases_of_same_explicit_controller_instance(implementation: str) -> None:
    source = declaration()
    source["providers"]["alias"] = {
        **source["providers"]["manager"], "implementation": implementation,
    }
    with pytest.raises(PlanError, match="aliased provider instance"):
        parse_bindings(source)


def test_role_bindings_one_named_resource_cannot_have_two_role_writers() -> None:
    source = declaration()
    source["bindings"]["status"]["resource"] = "gateway-stack"
    source["bindings"]["status"]["target"] = "host-b"
    with pytest.raises(PlanError, match="same provider resource"):
        parse_bindings(source)


def test_role_bindings_independent_controller_instances_can_reuse_resource_name() -> None:
    source = declaration()
    source["providers"]["other"] = {
        **source["providers"]["manager"], "instance_ref": "core-b", "configuration_ref": "core-b-config",
    }
    source["bindings"]["status"].update(provider="other", resource="gateway-stack")
    providers, bindings = parse_bindings(source)
    assert len(providers) == len(bindings) == 2


def test_role_bindings_collection_bounds_are_not_silently_truncated() -> None:
    source = declaration()
    source["bindings"]["gateway"]["key_refs"] = [f"key-{number}" for number in range(MAX_ITEMS + 1)]
    with pytest.raises(PlanError):
        parse_bindings(source)


@pytest.mark.parametrize("factory", [
    lambda: ProviderInstance("manager", "komodo", "core-a", "../host", "config", "bootstrap"),
    lambda: RoleBinding("role", "implementation", "provider", "target", "resource", "artifact", "config",
                        key_refs=["key"]),
    lambda: RoleBinding("role", "implementation", "provider", "target", "resource", "artifact", "config",
                        depends_on=("z", "a")),
    lambda: RoleResource("role", "Stack", "core", "target", "resource", "artifact", "config", ("key", "key")),
    lambda: ProviderContract("komodo", ("role",), (), version=True),
    lambda: ProviderContract("komodo", ("role",), (), version=2),
    lambda: ProviderContract("komodo", (), ()),
    lambda: ProviderContract("komodo", ("z", "a"), ()),
])
def test_role_bindings_direct_domain_construction_also_validates(factory: Callable[[], object]) -> None:
    with pytest.raises(PlanError):
        factory()

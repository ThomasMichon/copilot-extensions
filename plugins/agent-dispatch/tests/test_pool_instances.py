"""Pure definition contracts, not provider capacity or installed-placement proof."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest

from agent_dispatch.pool_instances import (
    PoolDefinitionError,
    PoolInstanceDefinition,
    ordered_pool_instances,
)
from agent_dispatch.registrar import RegistrarError, load_declaration
from agent_dispatch.registrar_reconcile import declaration_to_registration
from agent_dispatch.registrar_runtime_cli import _declaration_summary
from agent_dispatch.supervisor_registration import _spec_fingerprint, build_command


def _registration(
    *, priority: int = 0, machine: str | None = "host-a", env: str = "default",
) -> dict[str, Any]:
    declaration = load_declaration({
        "name": "review", "owner": "repo:example", "labels": ["review"],
        "repos": "example", "concurrency": 2, "priority": priority,
        "filters": {
            "permit": {"role": ["review"], "capabilities": ["python", "git"]},
            "reject": {"worktree": ["excluded"]},
        },
    })
    return declaration_to_registration(declaration, machine=machine, env=env)


@pytest.mark.parametrize("priority", [-100, 0, 100])
def test_priority_is_parsed_summarized_and_retained(priority: int) -> None:
    declaration = load_declaration({"name": "review", "priority": priority})
    registration = declaration_to_registration(declaration, machine="host-a")
    definition = PoolInstanceDefinition.from_registration(registration)
    assert declaration.priority == priority
    assert _declaration_summary(declaration)["priority"] == priority
    assert definition.priority == priority


def test_priority_default_is_zero() -> None:
    declaration = load_declaration({"name": "review"})
    assert declaration.priority == 0
    assert _declaration_summary(declaration)["priority"] == 0


@pytest.mark.parametrize("priority", [True, False, 1.5, "1", None])
def test_invalid_declaration_priority_refuses(priority: object) -> None:
    with pytest.raises(RegistrarError, match="priority: expected an integer"):
        load_declaration({"name": "review", "priority": priority})


def test_non_pool_priority_refuses_instead_of_being_ignored() -> None:
    with pytest.raises(RegistrarError, match="does not accept lane fields"):
        load_declaration({
            "name": "emitter", "kind": "emitter", "priority": 1,
            "spec": {"command": ["emit"], "interval_seconds": 60},
        })
    with pytest.raises(RegistrarError, match="only worker-pool"):
        load_declaration({"name": "review", "evaluator": "evaluator.json", "priority": 1})


def test_complete_filter_roundtrip_preserves_shorthand_and_rejects() -> None:
    definition = PoolInstanceDefinition.from_registration(_registration())
    restored = PoolInstanceDefinition.from_dict(json.loads(json.dumps(definition.to_dict())))
    assert restored == definition
    assert restored.filters.permit["repo"] == frozenset({"example"})
    assert restored.filters.permit["task-type"] == frozenset({"review"})
    assert restored.filters.permits({
        "repo": "example", "task_type": "review", "role": "review",
        "capabilities": ["python"],
    })
    for attributes in (
        {"repo": "other"}, {"task-type": "other"}, {"role": "implementation"},
        {"capabilities": ["unavailable"]}, {"worktree": "excluded"},
    ):
        assert not restored.filters.permits(attributes)
    assert restored.filters.permits({})


def test_explicit_filter_shorthand_override_survives_conversion() -> None:
    declaration = load_declaration({
        "name": "review", "repos": "shorthand", "labels": ["review"],
        "filters": {"permit": {"repo": ["explicit"], "task-type": ["special"]}},
    })
    definition = PoolInstanceDefinition.from_registration(
        declaration_to_registration(declaration, machine="host-a"),
    )
    assert definition.filters.permit["repo"] == frozenset({"explicit"})
    assert definition.filters.permit["task-type"] == frozenset({"special"})


def test_same_logical_registration_has_distinct_host_and_environment_keys() -> None:
    first = PoolInstanceDefinition.from_registration(_registration())
    second = PoolInstanceDefinition.from_registration(_registration(machine="host-b"))
    third = PoolInstanceDefinition.from_registration(_registration(env="isolated"))
    assert first.registration_id == second.registration_id == third.registration_id
    assert len({first.identity, second.identity, third.identity}) == 3
    assert [definition.max_concurrent for definition in (first, second, third)] == [2, 2, 2]
    assert replace(first, priority=10).identity == first.identity


def test_priority_precedes_stable_identity_ties() -> None:
    first = PoolInstanceDefinition.from_registration(_registration())
    later = replace(first, machine="host-b")
    other_env = replace(first, env="isolated")
    high = replace(first, registration_id="zzz", priority=5)
    low = replace(first, registration_id="aaa", priority=-5)
    assert ordered_pool_instances([low, later, other_env, high, first]) == (
        high, first, other_env, later, low,
    )


def test_duplicate_instance_is_not_a_second_capacity_owner() -> None:
    definition = PoolInstanceDefinition.from_registration(_registration())
    with pytest.raises(PoolDefinitionError, match="duplicate instance"):
        ordered_pool_instances([definition, replace(definition, priority=1)])


@pytest.mark.parametrize("machine", [None, "", "   "])
def test_unknown_machine_does_not_fabricate_an_instance(machine: str | None) -> None:
    registration = _registration(machine=machine)
    assert "pool_definition" not in registration
    with pytest.raises(PoolDefinitionError, match="no complete pool definition"):
        PoolInstanceDefinition.from_registration(registration)


def test_legacy_registration_is_not_promoted_from_labels() -> None:
    registration = _registration()
    registration.pop("pool_definition")
    with pytest.raises(PoolDefinitionError, match="no complete pool definition"):
        PoolInstanceDefinition.from_registration(registration)


@pytest.mark.parametrize("field,value", [
    ("version", True), ("version", 2), ("registration_id", ""), ("machine", None),
    ("env", ""), ("priority", False), ("priority", "5"), ("max_concurrent", 0),
    ("max_concurrent", True), ("filters", {"permit": {}}),
    ("filters", {"permit": None, "reject": {}}),
    ("filters", {"permit": {"unknown": ["value"]}, "reject": {}}),
])
def test_invalid_or_partial_definition_refuses(field: str, value: object) -> None:
    data = PoolInstanceDefinition.from_registration(_registration()).to_dict()
    data[field] = value
    with pytest.raises(PoolDefinitionError):
        PoolInstanceDefinition.from_dict(data)


@pytest.mark.parametrize("field", ["registration_id", "machine", "env", "priority", "max_concurrent"])
def test_missing_required_definition_field_refuses(field: str) -> None:
    data = PoolInstanceDefinition.from_registration(_registration()).to_dict()
    data.pop(field)
    with pytest.raises(PoolDefinitionError):
        PoolInstanceDefinition.from_dict(data)


def test_additive_definition_field_is_compatible() -> None:
    definition = PoolInstanceDefinition.from_registration(_registration())
    assert PoolInstanceDefinition.from_dict({**definition.to_dict(), "future": "ignored"}) == definition


@pytest.mark.parametrize("value", [None, [], "invalid"])
def test_non_mapping_inputs_refuse(value: object) -> None:
    with pytest.raises(PoolDefinitionError, match="must be a mapping"):
        PoolInstanceDefinition.from_dict(value)
    with pytest.raises(PoolDefinitionError, match="must be a mapping"):
        PoolInstanceDefinition.from_registration(value)


@pytest.mark.parametrize("field,value", [
    ("id", "other"), ("machine", "other"), ("env", "other"),
    ("spec", {"max_concurrent": 3}), ("spec", {"max_concurrent": True}),
])
def test_registration_scope_and_capacity_tampering_refuses(field: str, value: object) -> None:
    registration = _registration()
    registration[field] = value
    with pytest.raises(PoolDefinitionError):
        PoolInstanceDefinition.from_registration(registration)


def test_priority_metadata_does_not_change_legacy_launch_or_fingerprint() -> None:
    first, second = _registration(), _registration(priority=100)
    assert build_command(first, python="python") == build_command(second, python="python")
    assert _spec_fingerprint(first) == _spec_fingerprint(second)
    assert "pool_definition" not in first["spec"]

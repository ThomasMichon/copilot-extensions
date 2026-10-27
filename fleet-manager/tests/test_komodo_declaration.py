"""Selected substrate parsing and existing admission/custody, not installation proof."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from fleet_manager.komodo_declaration import KomodoDeclaration, select_substrate
from fleet_manager.node_journal import NodeJournal
from fleet_manager.operations import OperationIntent, TargetReceipt
from fleet_manager.planning import PlanError, canonical
from fleet_manager.runtime_admission import AdmissionError, OperationGrant
from test_role_deployment import compute, inputs
from test_runtime_admission import Harness, actor, policy
from test_runtime_identity import keys as keys


def declaration() -> dict[str, Any]:
    return {
        "schema": "fleet-manager.komodo-substrate", "version": 1,
        "provider": "manager", "instance": "core-a", "target": "host-a",
        "configuration_ref": "core-config", "revision": "config-1",
        "project": "example-manager", "architecture": "arm64", "core_port": 19120,
        "server_name": "example-host", "outfitting_seconds": 180,
        "references": {name: name + "-owner" for name in (
            "artifact", "credentials", "host", "runtime", "storage",
        )},
        "images": {
            "core": {"image": "ghcr.io/moghtech/komodo-core@sha256:" + "a" * 64, "version": "2.0.0"},
            "periphery": {"image": "ghcr.io/moghtech/komodo-periphery@sha256:" + "b" * 64, "version": "2.0.0"},
            "mongo": {"image": "docker.io/library/mongo@sha256:" + "c" * 64, "version": "8.0.0"},
        },
        "resources": {name: {"cpu_millis": 500, "memory_bytes": 512 * 1024**2}
                      for name in ("core", "mongo", "periphery")},
    }


def intent(operation: str = "install-1", *, generation: int = 1,
           deployment: str = "a" * 64) -> OperationIntent:
    return OperationIntent.from_dict({
        "schema": "fleet-manager.operation-intent", "version": 1,
        "authority": "gateway-a", "principal": "actor-a", "account": "account-a",
        "operation": operation, "target": "host-a", "generation": generation,
        "command": "manager.converge", "parameters": {"deployment": deployment},
    })


def test_selected_substrate_is_immutable_and_does_not_change_role_projection() -> None:
    template, config, inventory = inputs()
    before = compute(template, config, inventory)
    desired = declaration()
    config["extensions"]["role_bindings"]["substrates"] = {"primary": desired}
    original = copy.deepcopy(config)
    selected = select_substrate(config, "primary")
    assert selected.to_dict() == desired
    desired["revision"] = "changed"
    assert selected.to_dict()["revision"] == "config-1"
    config = original
    after = compute(template, config, inventory)
    assert after["roles"] == before["roles"]
    assert after["providers"] == before["providers"]
    assert after["suggested_config"] == config
    assert after["mutations_performed"] is False
    assert all(provider["installed"] is False for provider in after["providers"])


@pytest.mark.parametrize(("path", "replacement"), [
    (("architecture",), "x86"),
    (("desired_state",), "unknown"),
    (("core_port",), True),
    (("core_port",), 80),
    (("core_port",), 65536),
    (("project",), "--project"),
    (("project",), "project/other"),
    (("outfitting_seconds",), 1801),
    (("images", "core", "image"), "ghcr.io/moghtech/komodo-core:2"),
    (("images", "core", "image"), "ghcr.io/other/core@sha256:" + "a" * 64),
    (("images", "core", "image"), "ghcr.io/moghtech/komodo-core@sha256:" + "A" * 64),
    (("images", "core", "image"), "ghcr.io/moghtech/komodo-core@sha256:" + "a" * 65),
    (("images", "core", "version"), "3.0.0"),
    (("images", "core", "version"), "2.1.0"),
    (("images", "mongo", "version"), "8.1.0"),
    (("images", "mongo", "version"), "08.0.0"),
    (("resources", "core", "cpu_millis"), 0),
    (("resources", "mongo", "memory_bytes"), 1),
    (("references",), {"credentials": "inline-password"}),
    (("compose",), "services: arbitrary"),
    (("command",), ["sh", "-c", "anything"]),
    (("environment",), {"PASSWORD": "not-allowed"}),
    (("provider_module",), "arbitrary.module"),
])
def test_invalid_or_unbounded_declaration_refuses(path: tuple[str, ...], replacement: Any) -> None:
    value = declaration()
    cursor = value
    for component in path[:-1]:
        cursor = cursor[component]
    cursor[path[-1]] = replacement
    with pytest.raises(PlanError):
        KomodoDeclaration.from_dict(value)


def test_selection_requires_exact_provider_and_does_not_validate_unselected_substrates() -> None:
    template, config, inventory = inputs()
    config["extensions"]["role_bindings"]["substrates"] = {
        "primary": declaration(), "unselected": {"future": "declaration"},
    }
    assert select_substrate(config, "primary").to_dict() == declaration()
    assert compute(template, config, inventory)["mutations_performed"] is False
    for key in ("provider", "instance", "target", "configuration_ref"):
        changed = copy.deepcopy(config)
        changed["extensions"]["role_bindings"]["substrates"]["primary"][key] = "other"
        with pytest.raises(PlanError):
            select_substrate(changed, "primary")
    with pytest.raises(PlanError):
        select_substrate(config, "missing")


def test_declaration_rejects_noncanonical_mutable_and_duplicate_key_bytes() -> None:
    value = declaration()
    for raw in (bytearray(canonical(value)), b" " + canonical(value),
                b'{"schema":"one","schema":"two"}'):
        with pytest.raises(PlanError):
            KomodoDeclaration(raw)


def test_manager_admission_requires_exact_content_not_only_command_target_generation(keys: Any) -> None:
    harness = Harness(keys)
    first = intent()
    harness.current = policy(enrollments=(actor(operations=(
        OperationGrant("manager.converge", "host-a", 1, first.request_digest),
    )),))
    permit = harness.admit(first)
    permit = harness.owner.revalidate(permit, first)
    assert permit.request_digest == first.request_digest
    harness.owner.complete(permit, first)
    with pytest.raises(AdmissionError):
        harness.admit(intent("changed-identity"))
    with pytest.raises(AdmissionError):
        harness.admit(intent(deployment="b" * 64))
    for fingerprint in (None, "not-a-digest", "a" * 65):
        with pytest.raises(AdmissionError):
            OperationGrant("manager.converge", "host-a", 1, fingerprint)


def test_manager_unknown_survives_restart_and_forbids_alias_or_generation_takeover(tmp_path: Path) -> None:
    def reopen() -> NodeJournal:
        return NodeJournal(tmp_path / "node.db", authority="gateway-a",
                           installation="install-a", target="host-a")

    original = intent()
    node = reopen()
    accepted = node.accept_named_external(original, resource="physical-daemon-project")
    assert accepted.to_dict()["status"] == "accepted"
    assert node.begin_external(original)
    node = reopen()
    assert not node.begin_external(original)
    assert node.accept_named_external(original, resource="physical-daemon-project").to_dict()["status"] == "unknown"
    for competing in (intent("alias"), intent("new-generation", generation=2)):
        with pytest.raises(PlanError, match="unsettled"):
            node.accept_named_external(competing, resource="physical-daemon-project")
    with pytest.raises(PlanError, match="no longer owns"):
        node.accept_named_external(original, resource="alias-resource")
    settled = accepted.to_dict()
    settled.update(status="succeeded", sequence=3, result_ref="current-native-proof")
    node.reconcile(original, TargetReceipt.from_dict(settled))
    assert node.accept_named_external(
        intent("new-generation", generation=2), resource="physical-daemon-project",
    ).to_dict()["status"] == "accepted"


def test_manager_operation_accepts_only_one_reference_not_commands_or_inline_content() -> None:
    value = intent().to_dict()
    for parameters in ({}, {"deployment": "a", "command": "anything"},
                       {"deployment": {"compose": "arbitrary"}}):
        with pytest.raises(PlanError):
            OperationIntent.from_dict({**value, "parameters": parameters})

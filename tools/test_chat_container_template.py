"""Consume Chat's data-only contribution through the existing Fleet schemas."""

from __future__ import annotations

import copy
import importlib
import json
from pathlib import Path
import time
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
CONTRIBUTION = ROOT / "services" / "chat" / "container"
DIMENSIONS = ("cpu_millis", "memory_bytes", "disk_bytes")
SUPPORT = {
    "container": "container.runtime",
    "gateway": "gateway.chat-route",
    "storage": "storage.single-writer",
    "credentials": "credential.chat-domain",
    "manager": "deployment.komodo",
    "network": "network.gateway-outbound",
}


def document(name):
    return json.loads((CONTRIBUTION / name).read_text(encoding="utf-8"))


@pytest.fixture
def owner(monkeypatch):
    source = ROOT / "fleet-manager" / "src"
    monkeypatch.syspath_prepend(str(source))
    loadouts = importlib.import_module("fleet_manager.loadouts")
    bindings = importlib.import_module("fleet_manager.role_bindings")
    cli = importlib.import_module("fleet_manager.__main__")
    assert Path(loadouts.__file__).is_relative_to(source)
    assert Path(bindings.__file__).is_relative_to(source)
    return SimpleNamespace(
        design=loadouts.design_loadout, error=loadouts.PlanError,
        parse_bindings=bindings.parse_bindings, main=cli.main,
    )


def inputs():
    catalog = document("loadout-catalog.json")
    allocation = document("allocation.example.json")
    allocations = {"chat": allocation}
    for name, capability in SUPPORT.items():
        template = {
            "schema": "fleet-manager.loadout-template", "version": 1,
            "id": "fixture." + name, "revision": "1", "source": "fixture",
            "slots": {}, "provides": [capability], "requires": {},
            "resources": {
                "units": 1,
                "reservation": dict.fromkeys(DIMENSIONS, 10),
                "limit": dict.fromkeys(DIMENSIONS, 20),
            },
            "configuration": {"adapter": "fixture.support"},
        }
        catalog["templates"].append(template)
        allocations[allocation["slots"][name]] = {
            "template": {key: template[key] for key in ("id", "revision", "source")},
            "target": "support-host", "slots": {},
        }
    config = {
        "schema": "fleet-manager.config", "version": 1, "fleet_id": "fixture",
        "revision": "1", "capacity": {"machine_limit": 2, "execution_hosts": 0},
        "roles": {"custom": {"preserve": True}},
        "extensions": {
            "custom": {"preserve": ["private-choice"]},
            "loadouts": {
                "schema": "fleet-manager.loadout-registrar", "version": 1,
                "revision": "1", "allocations": allocations,
            },
        },
    }
    inventory = {
        "schema": "fleet-manager.loadout-inventory", "version": 1,
        "fleet_id": "fixture", "revision": "1", "observed_at": 100, "expires_at": 200,
        "complete": True, "baseline_excludes": sorted(allocations),
        "hosts": [{
            "id": name, "owned": True, "ready": True,
            "capacity": dict.fromkeys(DIMENSIONS, 2**40),
            "headroom": dict.fromkeys(DIMENSIONS, 0),
            "reserved": dict.fromkeys(DIMENSIONS, 0),
            "observed": dict.fromkeys(DIMENSIONS, 0),
        } for name in ("host-a", "support-host")],
    }
    return config, inventory, catalog


def slots(config):
    return config["extensions"]["loadouts"]["allocations"]["chat"]["slots"]


def test_real_owner_actualizes_the_complete_support_graph_without_private_state_mutation(owner):
    args = inputs()
    before = copy.deepcopy(args)
    result = owner.design(*args, now=150)
    assert args == before
    assert result["status"] == "review-required" and not result["blockers"]
    assert all(result[key] is False for key in (
        "mutations_performed", "activation_authorized", "live_readiness_verified",
    ))
    composition = result["composition"]
    chat = composition["allocations"]["chat"]
    assert chat["requires"] == sorted(slots(args[0])[name] for name in SUPPORT)
    assert composition["dependency_order"][-1] == "chat"
    assert chat["reservation"] == {key: slots(args[0])[key] for key in DIMENSIONS}
    assert chat["limit"] == {key: slots(args[0])["limit_" + key] for key in DIMENSIONS}
    assert chat["configuration"] == {
        "adapter": "container.service", "artifact_ref": "approved-chat-image",
        "configuration_ref": "chat-service-config", "storage_ref": "chat-conversation-volume",
        "key_refs": ["chat-domain-identity"], "gateway_slot_ref": "chat-route",
        "manager_ref": "core-a", "resource_ref": "chat-stack",
    }
    assert result["grid"]["support-host"]["proposed_reservation"] == dict.fromkeys(DIMENSIONS, 60)
    suggested = result["suggested_config"]
    assert suggested["roles"] == args[0]["roles"]
    assert suggested["extensions"]["custom"] == args[0]["extensions"]["custom"]
    assert suggested["extensions"]["loadouts"] == args[0]["extensions"]["loadouts"]
    assert owner.design(suggested, *args[1:], now=150)["suggested_config"] == suggested


def test_explicit_overrides_are_retained_instead_of_template_defaults(owner):
    args = inputs()
    chosen = slots(args[0])
    chosen.update(artifact_ref="private-approved-image", storage_ref="retained-private-volume")
    chosen["memory_bytes"] *= 2
    chosen["limit_memory_bytes"] = chosen["memory_bytes"] * 2
    before = copy.deepcopy(args)
    result = owner.design(*args, now=150)
    chat = result["composition"]["allocations"]["chat"]
    assert chat["configuration"]["artifact_ref"] == chosen["artifact_ref"]
    assert chat["configuration"]["storage_ref"] == chosen["storage_ref"]
    assert chat["reservation"]["memory_bytes"] == chosen["memory_bytes"]
    assert chat["limit"]["memory_bytes"] == chosen["limit_memory_bytes"]
    assert args == before


@pytest.mark.parametrize("name", list(SUPPORT))
def test_every_missing_or_unprovided_support_capability_refuses(owner, name):
    args = inputs()
    slots(args[0])[name] = "missing-support"
    with pytest.raises(owner.error):
        owner.design(*args, now=150)
    args = inputs()
    template = next(item for item in args[2]["templates"] if item["id"] == "fixture." + name)
    template["provides"] = []
    with pytest.raises(owner.error):
        owner.design(*args, now=150)


@pytest.mark.parametrize("change", [
    "missing-slot", "extra-slot", "revision", "source", "zero-resource",
    "boolean-resource", "limit-below-reservation", "raw-command", "support-cycle",
])
def test_invalid_private_actualization_cannot_become_a_reviewable_design(owner, change):
    args = inputs()
    selected = args[0]["extensions"]["loadouts"]["allocations"]["chat"]
    if change == "missing-slot":
        del selected["slots"]["storage_ref"]
    elif change == "extra-slot":
        selected["slots"]["command"] = "raw"
    elif change in ("revision", "source"):
        selected["template"][change] = "foreign"
    elif change == "zero-resource":
        selected["slots"]["disk_bytes"] = 0
    elif change == "boolean-resource":
        selected["slots"]["cpu_millis"] = True
    elif change == "limit-below-reservation":
        selected["slots"]["limit_memory_bytes"] -= 1
    elif change == "raw-command":
        selected["slots"]["artifact_ref"] = "docker run example"
    else:
        args[2]["templates"][1]["requires"] = {"conversation.chat": "chat"}
    with pytest.raises(owner.error):
        owner.design(*args, now=150)


def test_stale_capacity_is_blocked_not_live_deployment_permission(owner):
    args = inputs()
    args[1]["expires_at"] = 150
    result = owner.design(*args, now=150)
    assert result["status"] == "blocked" and result["blockers"]
    assert result["activation_authorized"] is False
    assert result["mutations_performed"] is False


def test_source_cli_reads_the_contribution_and_preserves_actualization_inputs(owner, tmp_path, capsys):
    config, inventory, catalog = inputs()
    now = int(time.time())
    inventory.update(observed_at=now - 1, expires_at=now + 300)
    arguments = ["design"]
    for name, value in (("config", config), ("inventory", inventory), ("catalog", catalog)):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        arguments.extend(("--" + name, str(path)))
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    assert owner.main(arguments) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "review-required"
    assert result["activation_authorized"] is False
    assert result["composition"]["allocations"]["chat"]["configuration"]["adapter"] == (
        "container.service"
    )
    assert result["suggested_config"]["extensions"]["custom"] == config["extensions"]["custom"]
    assert before == {path.name: path.read_bytes() for path in tmp_path.iterdir()}


def test_komodo_example_uses_the_canonical_reference_only_binding(owner):
    example = document("role-bindings.example.json")
    providers, bindings = owner.parse_bindings(example)
    assert len(providers) == len(bindings) == 1
    provider, binding = providers[0], bindings[0]
    selected = document("allocation.example.json")
    assert provider.implementation == "komodo"
    assert provider.instance_ref == selected["slots"]["manager_ref"]
    assert provider.bootstrap_ref == "independent-bootstrap"
    assert binding.implementation == "container.service"
    assert binding.target == selected["target"]
    for field, slot in (
        ("resource", "resource_ref"), ("artifact_ref", "artifact_ref"),
        ("configuration_ref", "configuration_ref"),
    ):
        assert getattr(binding, field) == selected["slots"][slot]
    assert binding.key_refs == (selected["slots"]["credential_ref"],)
    assert binding.depends_on == ()
    example["bindings"]["chat"]["volumes"] = ["/private/data:/data"]
    with pytest.raises(owner.error):
        owner.parse_bindings(example)

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from fleet_manager.komodo_roles import KomodoRoleProvider
from fleet_manager.planning import PlanError, canonical, plan
from fleet_manager.role_bindings import ProviderInstance, RoleBinding, RoleResource
from fleet_manager.role_deployment import ProviderContract, RoleProvider, plan_role_deployment, verify_role_plan
from fleet_manager.role_plan import main


def maximum_inputs(count: int) -> tuple[dict, dict, dict]:
    template, config, inventory = inputs()
    template["roles"] = {
        f"role-{number}": {"kind": "service", "required": True, "available": False}
        for number in range(count)
    }
    config["roles"] = {name: {} for name in template["roles"]}
    config["extensions"]["role_bindings"] = {
        "schema": "fleet-manager.role-bindings", "version": 1,
        "providers": {
            f"manager-{number}": {
                "implementation": "unavailable", "instance_ref": f"core-{number}",
                "target": "missing-host", "configuration_ref": "core-config",
                "bootstrap_ref": "local-bootstrap",
            }
            for number in range(count)
        },
        "bindings": {
            f"role-{number}": {
                "implementation": "container.service", "provider": f"manager-{number}",
                "target": "missing-host", "resource": f"resource-{number}",
                "artifact_ref": "image", "configuration_ref": "config",
                "depends_on": [f"role-{number - 1}"] if number else [],
            }
            for number in range(count)
        },
    }
    inventory.update(complete=False, hosts=[])
    return template, config, inventory


def inputs() -> tuple[dict, dict, dict]:
    template = {
        "schema": "fleet-manager.template", "version": 1,
        "id": "recommended", "revision": "r1", "execution_hosts": 0,
        "roles": {"gateway": {"kind": "service", "required": True, "available": True}},
    }
    config = {
        "schema": "fleet-manager.config", "version": 1, "fleet_id": "example", "revision": "c1",
        "capacity": {"machine_limit": 2, "execution_hosts": 0},
        "template": {"id": "recommended", "revision": "r1"},
        "roles": {"gateway": {"custom": "retained"}, "status": {}, "custom": {"data": "retained"}},
        "extensions": {
            "custom": {"keep": [1, 2]},
            "role_bindings": {
                "schema": "fleet-manager.role-bindings", "version": 1,
                "providers": {"manager": {
                    "implementation": "komodo", "instance_ref": "core-a", "target": "host-a",
                    "configuration_ref": "core-config", "bootstrap_ref": "local-bootstrap",
                }},
                "bindings": {
                    "gateway": {
                        "implementation": "fleet-manager.gateway", "provider": "manager",
                        "target": "host-a", "resource": "gateway-stack", "artifact_ref": "gateway-image",
                        "configuration_ref": "gateway-config", "key_refs": ["tls"],
                        "requires": ["container-role"],
                    },
                    "status": {
                        "implementation": "container.service", "provider": "manager",
                        "target": "host-a", "resource": "status-stack", "artifact_ref": "status-image",
                        "configuration_ref": "status-config", "depends_on": ["gateway"],
                    },
                },
            },
        },
    }
    inventory = {
        "schema": "fleet-manager.inventory", "version": 1, "fleet_id": "example",
        "provider": "static", "revision": "i1", "observed_at": 100, "expires_at": 200,
        "complete": True, "creation_allowed": False, "quota_remaining": None,
        "hosts": [{"id": "host-a", "owned": True, "ready": True, "roles": {"gateway": "ready"}}],
    }
    return template, config, inventory


def compute(
    template: dict, config: dict, inventory: dict, *,
    providers: Mapping[str, RoleProvider] | None = None,
) -> dict:
    return plan_role_deployment(
        template, config, inventory, fleet_id="example", machine_limit=2, now=150,
        providers=providers if providers is not None else {"komodo": KomodoRoleProvider()},
    )


def test_role_deployment_compiles_explicit_provider_and_dependency_order_without_effects() -> None:
    source = inputs()
    before = copy.deepcopy(source)
    result = compute(*source)
    assert source == before
    assert result["status"] == "review-required" and not result["blockers"]
    assert result["suggested_config"] == source[1]
    assert result["unbound_roles"] == ["custom"]
    assert [role["role"] for role in result["roles"]] == ["gateway", "status"]
    projection = result["roles"][0]["projection"]
    assert projection["resource_type"] == "Stack"
    assert projection["instance_ref"] == "core-a" and projection["artifact_ref"] == "gateway-image"
    assert result["providers"][0]["bootstrap_ref"] == "local-bootstrap"
    assert result["providers"][0]["required_authority"] == "independent-substrate-owner"
    assert not result["providers"][0]["independence_verified"]
    assert result["providers"][0]["state"] == "owner-evidence-required"
    assert not result["activation_authorized"] and not result["mutations_performed"]
    assert not result["installed"] and not result["live_readiness_verified"]
    assert result["cleanup_actions"] == []
    assert all(role["state"] == "configured-not-installed" for role in result["roles"])


def test_role_deployment_default_planner_retains_namespace_without_selecting_provider() -> None:
    template, config, inventory = inputs()
    result = plan(template, config, inventory, fleet_id="example", machine_limit=2, now=150)
    assert result["schema"] == "fleet-manager.plan"
    assert result["suggested_config"] == config
    assert "providers" not in result


def test_role_deployment_unbound_controller_is_retained_without_bootstrap_or_provider_lookup() -> None:
    template, config, inventory = inputs()
    config["extensions"]["role_bindings"]["providers"]["other-manager"] = {
        "implementation": "unavailable", "instance_ref": "other-core", "target": "unknown-host",
        "configuration_ref": "existing-core-config", "bootstrap_ref": "existing-core-owner",
    }
    result = compute(template, config, inventory)
    assert result["status"] == "review-required"
    assert result["unbound_providers"] == ["other-manager"]
    assert [item["name"] for item in result["providers"]] == ["manager"]
    assert result["suggested_config"] == config


@pytest.mark.parametrize("change,code", [
    ("provider", "provider-implementation-unavailable"),
    ("role", "role-implementation-unsupported"),
    ("capability", "provider-capability-unsupported"),
    ("self-bootstrap", "role-implementation-unsupported"),
])
def test_role_deployment_unsupported_selection_blocks_without_fallback(change: str, code: str) -> None:
    template, config, inventory = inputs()
    namespace = config["extensions"]["role_bindings"]
    if change == "provider":
        namespace["providers"]["manager"]["implementation"] = "not.imported.module"
    elif change == "role":
        namespace["bindings"]["gateway"]["implementation"] = "unknown"
    elif change == "capability":
        namespace["bindings"]["gateway"]["requires"] = ["bootstrap-execution"]
    else:
        namespace["bindings"]["gateway"]["implementation"] = "komodo.core"
    result = compute(template, config, inventory)
    assert result["status"] == "blocked"
    assert code in {item for blocker in result["blockers"] for item in blocker["codes"]}
    assert result["roles"][0]["projection"] is None
    with pytest.raises(PlanError, match="unresolved blockers"):
        verify_role_plan(
            result, template, config, inventory, providers={"komodo": KomodoRoleProvider()},
            fleet_id="example", machine_limit=2, now=160,
        )


@pytest.mark.parametrize("change", ["missing", "foreign", "offline", "manager-missing", "stale", "partial"])
def test_role_deployment_unadmitted_or_stale_targets_never_call_provider(change: str) -> None:
    template, config, inventory = inputs()
    if change == "missing":
        config["extensions"]["role_bindings"]["bindings"]["gateway"]["target"] = "absent"
    elif change == "manager-missing":
        config["extensions"]["role_bindings"]["providers"]["manager"]["target"] = "absent"
    elif change == "foreign":
        inventory["hosts"][0]["owned"] = False
    elif change == "offline":
        inventory["hosts"][0]["ready"] = False
    elif change == "stale":
        inventory["expires_at"] = 140
    else:
        inventory["complete"] = False

    class Uncalled(KomodoRoleProvider):
        def project(self, instance: ProviderInstance, binding: RoleBinding) -> RoleResource:
            raise AssertionError("unadmitted placement reached provider projection")

    result = compute(template, config, inventory, providers={"komodo": Uncalled()})
    assert result["status"] == "blocked"
    assert result["roles"][0]["projection"] is None


@pytest.mark.parametrize("change", ["cycle", "self-cycle", "unknown-role", "missing-selection"])
def test_role_deployment_invalid_graph_refuses_before_projection(change: str) -> None:
    template, config, inventory = inputs()
    namespace = config["extensions"]["role_bindings"]
    if change == "cycle":
        namespace["bindings"]["gateway"]["depends_on"] = ["status"]
    elif change == "self-cycle":
        namespace["bindings"]["gateway"]["depends_on"] = ["gateway"]
    elif change == "unknown-role":
        del config["roles"]["status"]
    else:
        del config["extensions"]["role_bindings"]
    with pytest.raises(PlanError):
        compute(template, config, inventory)


def test_role_deployment_code_registered_alternative_obeys_same_reference_contract() -> None:
    template, config, inventory = inputs()
    config["extensions"]["role_bindings"]["providers"]["manager"]["implementation"] = "fixture"

    class Fixture(KomodoRoleProvider):
        contract = ProviderContract(
            "fixture", ("container.service", "fleet-manager.gateway"),
            ("bootstrap-reference", "container-role", "named-resource"),
        )

        def project(self, instance: ProviderInstance, binding: RoleBinding) -> RoleResource:
            return replace(super().project(instance, binding), resource_type="Fixture")

    result = compute(template, config, inventory, providers={"fixture": Fixture()})
    assert result["status"] == "review-required"
    assert result["roles"][0]["projection"]["resource_type"] == "Fixture"
    assert result["providers"][0]["implementation"] == "fixture"


@pytest.mark.parametrize("change", ["wrong-contract", "wrong-reference", "raw-payload"])
def test_role_deployment_invalid_trusted_provider_output_is_not_a_success_fallback(change: str) -> None:
    class Invalid(KomodoRoleProvider):
        if change == "wrong-contract":
            contract = ProviderContract("different", ("container.service",), ())

        def project(self, instance: ProviderInstance, binding: RoleBinding) -> RoleResource:
            if change == "raw-payload":
                return {"command": "not-accepted"}  # type: ignore[return-value]
            return replace(super().project(instance, binding), target="wrong")

    with pytest.raises(PlanError):
        compute(*inputs(), providers={"komodo": Invalid()})


def test_role_deployment_pins_one_immutable_contract_snapshot_per_selected_implementation() -> None:
    class Changing(KomodoRoleProvider):
        reads = 0

        @property
        def contract(self) -> ProviderContract:
            self.reads += 1
            if self.reads > 1:
                return ProviderContract("komodo", ("other",), ())
            return KomodoRoleProvider.contract

    provider = Changing()
    result = compute(*inputs(), providers={"komodo": provider})
    assert provider.reads == 1
    assert result["status"] == "review-required"


def test_role_deployment_blocks_dependents_of_unsupported_role_without_projecting_them() -> None:
    template, config, inventory = inputs()
    config["extensions"]["role_bindings"]["bindings"]["gateway"]["requires"] = ["unavailable"]
    result = compute(template, config, inventory)
    assert all(role["projection"] is None and role["state"] == "blocked" for role in result["roles"])
    assert {
        "scope": "role", "codes": ["role-dependency-blocked"],
        "role": "status", "provider": "manager",
    } in result["blockers"]


@pytest.mark.parametrize("change", ["tamper", "config", "inventory", "contract", "expiry", "policy"])
def test_role_deployment_saved_plan_revalidation_binds_all_inputs_and_selected_contracts(change: str) -> None:
    template, config, inventory = inputs()
    saved = compute(template, config, inventory)
    providers = {"komodo": KomodoRoleProvider()}
    now, limit = 160, 2
    if change == "tamper":
        saved["roles"][0]["artifact_ref"] = "different"
    elif change == "config":
        config["revision"] = "new"
    elif change == "inventory":
        inventory["revision"] = "new"
    elif change == "contract":
        class Different(KomodoRoleProvider):
            contract = ProviderContract("komodo", ("container.service", "fleet-manager.gateway"), ())
        providers["komodo"] = Different()
    elif change == "expiry":
        now = 200
    else:
        limit = 1
    with pytest.raises(PlanError):
        verify_role_plan(saved, template, config, inventory, providers=providers,
                         fleet_id="example", machine_limit=limit, now=now)


def test_role_deployment_canonical_order_and_unrelated_extensions_are_retained() -> None:
    template, config, inventory = inputs()
    saved = compute(template, config, inventory)
    reordered = json.loads(json.dumps(config, sort_keys=True))
    assert compute(template, reordered, inventory) == saved
    assert verify_role_plan(
        saved, template, config, inventory, providers={"komodo": KomodoRoleProvider()},
        fleet_id="example", machine_limit=2, now=160,
    ) == saved
    assert saved["suggested_config"]["extensions"]["custom"] == {"keep": [1, 2]}


def test_role_deployment_preserved_near_limit_configuration_keeps_existing_output_depth() -> None:
    template, config, inventory = inputs()
    nested: object = "retained"
    for _ in range(7):
        nested = {"next": nested}
    config["extensions"]["custom"] = {"nested": nested, "large": ["x" * 60000] * 12}
    result = compute(template, config, inventory)
    assert len(canonical(result, output=True)) > 700000
    assert result["suggested_config"] == config


@pytest.mark.parametrize("count", [172, 256])
def test_role_deployment_maximum_cardinality_preserves_every_diagnostic_within_shared_bounds(count: int) -> None:
    template, config, inventory = maximum_inputs(count)
    result = compute(template, config, inventory)
    assert result["status"] == "blocked"
    assert len(result["roles"]) == len(result["providers"]) == count
    assert len(result["blockers"]) == 2 * count + 1
    assert len(canonical(result, output=True)) < 4 * 1024 * 1024
    diagnostics = {
        (item["scope"], item["role"], item["provider"], code)
        for item in result["blockers"] for code in item["codes"]
    }
    for number in range(count):
        name, manager = f"role-{number}", f"manager-{number}"
        assert ("provider", None, manager, "provider-implementation-unavailable") in diagnostics
        assert ("provider", None, manager, "provider-target-not-admitted") in diagnostics
        assert ("role", name, manager, "role-target-not-admitted") in diagnostics
        assert ("fleet", None, None, "required-role-unsupported:" + name) in diagnostics
        if number:
            assert ("role", name, manager, "role-dependency-blocked") in diagnostics
    with pytest.raises(PlanError, match="unresolved blockers"):
        verify_role_plan(
            result, template, config, inventory, providers={"komodo": KomodoRoleProvider()},
            fleet_id="example", machine_limit=2, now=160,
        )


def test_role_deployment_maximum_cardinality_cli_returns_blocked_json_not_invalid_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    template, config, inventory = maximum_inputs(256)
    now = int(time.time())
    inventory.update(observed_at=now - 1, expires_at=now + 60)
    for name, value in (("template", template), ("config", config), ("inventory", inventory)):
        (tmp_path / f"{name}.json").write_text(json.dumps(value))
    assert main([
        "--template", str(tmp_path / "template.json"), "--config", str(tmp_path / "config.json"),
        "--inventory", str(tmp_path / "inventory.json"), "--fleet-id", "example", "--machine-limit", "2",
    ]) == 2
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["status"] == "blocked" and len(result["blockers"]) == 513
    assert captured.err == ""


def write_inputs(root: Path) -> tuple[list[str], dict[str, bytes]]:
    template, config, inventory = inputs()
    now = int(time.time())
    inventory.update(observed_at=now - 1, expires_at=now + 60)
    for name, value in (("template", template), ("config", config), ("inventory", inventory)):
        (root / f"{name}.json").write_text(json.dumps(value), encoding="utf-8")
    args = [
        "--template", str(root / "template.json"), "--config", str(root / "config.json"),
        "--inventory", str(root / "inventory.json"), "--fleet-id", "example", "--machine-limit", "2",
    ]
    return args, {path.name: path.read_bytes() for path in root.iterdir()}


def test_role_deployment_installed_isolated_module_works_source_independently_without_effects(tmp_path: Path) -> None:
    args, before = write_inputs(tmp_path)
    script = (
        "import runpy,socket,subprocess,sys;"
        "def blocked(*args,**kwargs): raise AssertionError('unexpected process or network');"
    )
    script = script.replace(";def ", "\ndef ")
    script += "\nsocket.socket=blocked\nsubprocess.Popen=blocked\n"
    script += "sys.argv=['fleet_manager.role_plan',*sys.argv[1:]]\n"
    script += "runpy.run_module('fleet_manager.role_plan',run_name='__main__')\n"
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, *args], cwd=tmp_path,
        capture_output=True, text=True, timeout=10, **flags,
    )
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["schema"] == "fleet-manager.role-plan"
    assert not output["mutations_performed"] and not output["activation_authorized"]
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


@pytest.mark.parametrize("mode,expected", [("unsupported", 2), ("missing", 1), ("malformed", 1)])
def test_role_deployment_cli_preserves_blocked_invalid_exit_distinction(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mode: str, expected: int,
) -> None:
    args, _ = write_inputs(tmp_path)
    if mode == "unsupported":
        path = tmp_path / "config.json"
        value = json.loads(path.read_text())
        value["extensions"]["role_bindings"]["providers"]["manager"]["implementation"] = "unavailable"
        path.write_text(json.dumps(value))
    elif mode == "missing":
        args[3] = str(tmp_path / "absent")
    else:
        (tmp_path / "config.json").write_text('{"private-value":')
    assert main(args) == expected
    output = capsys.readouterr()
    if expected == 2:
        assert json.loads(output.out)["status"] == "blocked"
    else:
        assert output.out == "" and "private-value" not in output.err

"""Real template/registrar consumption and exact multidimensional admission."""

from __future__ import annotations

import copy
import json
import time

import pytest

from fleet_manager.__main__ import main
from fleet_manager.loadouts import DIMENSIONS, design_loadout
from fleet_manager.planning import MAX_PLAN_ITEMS, PlanError, canonical, decode


def resource(cpu=1000, memory=2048, disk=4096):
    return dict(zip(DIMENSIONS, (cpu, memory, disk)))


def inputs(now=100):
    catalog = {
        "schema": "fleet-manager.loadout-catalog", "version": 1,
        "templates": [{
            "schema": "fleet-manager.loadout-template", "version": 1,
            "id": "support", "revision": "r1", "source": "example-repo",
            "slots": {"config": {"type": "reference"}},
            "provides": ["kubernetes.cluster", "arc.controller", "forge.credential", "runner.network"],
            "requires": {},
            "resources": {"units": 1, "reservation": resource(), "limit": resource()},
            "configuration": {"adapter": "managed.support", "configuration_ref": {"slot": "config"}},
        }],
    }
    slots = {
        "scale_set": "example-runners", "forge_scope_ref": "selected-repo",
        "runner_group_ref": "restricted-group", "architecture": "x64",
        "artifact_ref": "pinned-runner-artifacts", "configuration_ref": "arc-values",
        "credential_ref": "github-app-binding", "trust_policy_ref": "isolated-jobs",
        "toolchain_ref": "ci-tools", "cluster": "support", "controller": "support",
        "credential_service": "support", "network": "support",
        "min_runners": 0, "max_runners": 2,
        **resource(), **{f"limit_{name}": value for name, value in resource().items()},
    }
    config = {
        "schema": "fleet-manager.config", "version": 1, "fleet_id": "example", "revision": "c1",
        "capacity": {"machine_limit": 2, "execution_hosts": 0},
        "roles": {"custom": {"keep": [1, 2]}},
        "extensions": {
            "custom": {"keep": True},
            "loadouts": {
                "schema": "fleet-manager.loadout-registrar", "version": 1, "revision": "r1",
                "allocations": {
                    "runners": {
                        "template": {"id": "actions.arc", "revision": "1", "source": "fleet-manager"},
                        "target": "worker-a", "slots": slots,
                    },
                    "support": {
                        "template": {"id": "support", "revision": "r1", "source": "example-repo"},
                        "target": "control-a", "slots": {"config": "support-config"},
                    },
                },
            },
        },
    }
    inventory = {
        "schema": "fleet-manager.loadout-inventory", "version": 1,
        "fleet_id": "example", "revision": "i1", "observed_at": now, "expires_at": now + 100,
        "complete": True, "baseline_excludes": ["runners", "support"], "hosts": [
            {"id": name, "owned": True, "ready": True, "capacity": resource(4000, 8192, 16384),
             "headroom": resource(), "reserved": resource(), "observed": resource(500, 1024, 2048)}
            for name in ("worker-a", "control-a")
        ],
    }
    return config, inventory, catalog


def compute(config, inventory, catalog, now=150):
    return design_loadout(config, inventory, catalog, now=now)


def allocation(config, name="runners"):
    return config["extensions"]["loadouts"]["allocations"][name]


def test_named_host_grid_exact_fit_support_dedup_and_publication_preserve_custom_state():
    args = inputs()
    before = copy.deepcopy(args)
    result = compute(*args)
    assert args == before
    assert result["status"] == "review-required" and not result["blockers"]
    assert not any(result[key] for key in (
        "mutations_performed", "activation_authorized", "live_readiness_verified",
    ))
    composition = result["composition"]
    assert composition["dependency_order"] == ["support", "runners"]
    assert composition["allocations"]["runners"]["requires"] == ["support"]
    assert composition["allocations"]["runners"]["reservation"] == resource(2000, 4096, 8192)
    assert composition["allocations"]["runners"]["configuration"]["adapter"] == "helm.arc"
    assert composition["allocations"]["runners"]["configuration"]["forge_scope_ref"] == "selected-repo"
    assert result["grid"]["worker-a"]["remaining"] == resource(0, 0, 0)
    assert result["grid"]["control-a"]["proposed_reservation"] == resource()
    suggested = result["suggested_config"]
    assert suggested["roles"] == args[0]["roles"]
    assert suggested["extensions"]["custom"] == args[0]["extensions"]["custom"]
    assert suggested["extensions"]["loadouts"] == args[0]["extensions"]["loadouts"]
    assert suggested["extensions"]["loadout_composition"] == composition
    assert compute(*args)["digest"] == result["digest"]
    reloaded = decode(canonical(suggested))
    assert compute(reloaded, *args[1:])["suggested_config"] == suggested


@pytest.mark.parametrize("count", [171, 172, 256])
def test_full_size_blocked_grid_survives_diagnostic_collection_boundary(count):
    config, inventory, catalog = inputs()
    support = allocation(config, "support")
    allocations = {
        f"allocation-{index:03d}": {**copy.deepcopy(support), "target": f"host-{index:03d}"}
        for index in range(count)
    }
    config["capacity"]["machine_limit"] = count
    config["extensions"]["loadouts"]["allocations"] = allocations
    inventory["baseline_excludes"] = list(allocations)
    inventory["hosts"] = [
        {"id": f"host-{index:03d}", "owned": True, "ready": True,
         **{name: resource(0, 0, 0) for name in ("capacity", "headroom", "reserved", "observed")}}
        for index in range(count)
    ]
    result = compute(config, inventory, catalog)
    assert result["status"] == "blocked"
    assert len(result["grid"]) == count
    assert result["blocker_count"] == count * 3
    assert len(result["blockers"]) == min(count * 3, MAX_PLAN_ITEMS)
    assert result["blockers_omitted"] == max(0, count * 3 - MAX_PLAN_ITEMS)
    assert all(row["remaining"] == resource(-1000, -2048, -4096)
               for row in result["grid"].values())
    assert json.loads(canonical(result, output=True)) == result


@pytest.mark.parametrize("excess", ["nesting", "bytes"])
def test_publication_config_must_fit_normal_reload_bounds_not_only_output_bounds(excess):
    args = inputs()
    if excess == "nesting":
        nested = "leaf"
        for _ in range(8):
            nested = {"level": nested}
        args[2]["templates"][0]["configuration"] = nested
    else:
        args[0]["extensions"]["custom"]["large"] = ["x" * 60000] * 12
        args[2]["templates"][0]["configuration"] = {"large": ["y" * 60000] * 6}
    for value in args:
        canonical(value)
    with pytest.raises(PlanError, match=f"input exceeds {excess[:-1] if excess == 'bytes' else excess} limit"):
        compute(*args)


def test_slot_expansion_refuses_before_copying_an_oversized_intermediate(monkeypatch):
    args = inputs()
    template = args[2]["templates"][0]
    template["slots"] = {"refs": {"type": "references"}}
    template["configuration"] = {
        "chunks": [[{"slot": "refs"} for _ in range(256)] for _ in range(16)],
    }
    values = [f"r{index:03d}" + "x" * 124 for index in range(256)]
    allocation(args[0], "support")["slots"] = {"refs": values}
    original = copy.deepcopy
    copies = 0

    def tracked(value, *positional, **keywords):
        nonlocal copies
        if value is values:
            copies += 1
            assert copies < 32, "oversized substitution must be refused before materialization"
        return original(value, *positional, **keywords)

    monkeypatch.setattr("fleet_manager.loadouts.copy.deepcopy", tracked)
    for value in args:
        canonical(value)
    with pytest.raises(PlanError, match="expansion exceeds byte budget"):
        compute(*args)
    assert 0 < copies < 32


def test_expansion_budget_is_shared_across_allocations_not_reset_per_template():
    args = inputs()
    args[2]["templates"][0]["configuration"] = {"large": ["x" * 60000] * 8}
    for name in ("support-two", "support-three"):
        args[0]["extensions"]["loadouts"]["allocations"][name] = copy.deepcopy(
            allocation(args[0], "support"),
        )
        args[1]["baseline_excludes"].append(name)
    for value in args:
        canonical(value)
    with pytest.raises(PlanError, match="expansion exceeds byte budget"):
        compute(*args)


@pytest.mark.parametrize("change", [
    "role-value", "role-id", "template-shape", "template-fields", "template-id", "template-revision",
])
def test_existing_adopter_contract_matches_the_normal_planner(change):
    args = inputs()
    if change == "role-value":
        args[0]["roles"]["custom"] = 7
    elif change == "role-id":
        args[0]["roles"]["invalid name"] = {}
    elif change == "template-shape":
        args[0]["template"] = 7
    elif change == "template-fields":
        args[0]["template"] = {"id": "default", "revision": "r1", "unexpected": 1}
    elif change == "template-id":
        args[0]["template"] = {"id": "invalid name", "revision": "r1"}
    else:
        args[0]["template"] = {"id": "default", "revision": False}
    with pytest.raises(PlanError):
        compute(*args)


def test_two_scale_sets_share_support_once_and_charge_both_runner_envelopes():
    args = inputs()
    second = copy.deepcopy(allocation(args[0]))
    second["slots"]["scale_set"] = "another-set"
    args[0]["extensions"]["loadouts"]["allocations"]["second"] = second
    args[1]["baseline_excludes"].append("second")
    result = compute(*args)
    assert result["composition"]["dependency_order"].count("support") == 1
    assert result["grid"]["control-a"]["proposed_reservation"] == resource()
    assert result["grid"]["worker-a"]["proposed_reservation"] == resource(4000, 8192, 16384)
    assert "capacity-exceeded:worker-a:memory_bytes" in result["blockers"]
    second["slots"]["scale_set"] = allocation(args[0])["slots"]["scale_set"]
    with pytest.raises(PlanError, match="same ARC scale set"):
        compute(*args)


def test_slot_named_slot_is_metadata_not_an_expression_and_reference_lists_are_typed():
    args = inputs()
    support = args[2]["templates"][0]
    support["slots"] = {
        "slot": {"type": "reference"}, "refs": {"type": "references"},
        "count": {"type": "integer", "minimum": 1, "maximum": 4},
    }
    support["configuration"] = {
        "value": {"slot": "slot"}, "references": {"slot": "refs"}, "count": {"slot": "count"},
    }
    allocation(args[0], "support")["slots"] = {"slot": "literal", "refs": ["one", "two"], "count": 2}
    result = compute(*args)
    assert result["composition"]["allocations"]["support"]["configuration"] == {
        "value": "literal", "references": ["one", "two"], "count": 2,
    }
    allocation(args[0], "support")["slots"]["refs"].append("one")
    with pytest.raises(PlanError):
        compute(*args)


@pytest.mark.parametrize("excluded", [[], ["support"], ["runners", "support", "foreign"]])
def test_inventory_cannot_double_count_or_silently_omit_baseline_ownership(excluded):
    args = inputs()
    args[1]["baseline_excludes"] = excluded
    with pytest.raises(PlanError, match="baseline"):
        compute(*args)


@pytest.mark.parametrize("dimension", DIMENSIONS)
@pytest.mark.parametrize("boundary,blocked", [(0, False), (-1, True)])
def test_every_dimension_admits_exact_threshold_not_one_unit_less(dimension, boundary, blocked):
    args = inputs()
    args[1]["hosts"][0]["capacity"][dimension] += boundary
    result = compute(*args)
    assert (f"capacity-exceeded:worker-a:{dimension}" in result["blockers"]) == blocked


@pytest.mark.parametrize("dimension", DIMENSIONS)
def test_cpu_cannot_pay_for_memory_or_disk_and_limits_are_not_reservations(dimension):
    args = inputs()
    slots = allocation(args[0])["slots"]
    slots[f"limit_{dimension}"] += 1
    result = compute(*args)
    row = result["grid"]["worker-a"]
    assert row["remaining"][dimension] == 0
    assert row["burst_remaining"][dimension] == -2
    assert f"burst-capacity-exceeded:worker-a:{dimension}" in result["blockers"]


def test_observations_are_not_free_capacity_or_double_counted_reservations():
    args = inputs()
    args[1]["hosts"][0]["observed"] = resource(1100, 2048, 4096)
    result = compute(*args)
    assert result["grid"]["worker-a"]["baseline"]["cpu_millis"] == 1100
    assert "capacity-exceeded:worker-a:cpu_millis" in result["blockers"]


@pytest.mark.parametrize("change,blocker", [
    ("missing", "target-missing:runners"),
    ("unowned", "target-not-admitted:runners"),
    ("unready", "target-not-admitted:runners"),
    ("unknown", "observed-usage-unknown:worker-a"),
    ("stale", "inventory-stale-or-incomplete"),
    ("incomplete", "inventory-stale-or-incomplete"),
    ("budget", "existing-capacity-exceeds-budget-retained"),
])
def test_unknown_stale_or_unadmitted_target_never_fits(change, blocker):
    args = inputs()
    host = args[1]["hosts"][0]
    if change == "missing":
        allocation(args[0])["target"] = "elsewhere"
    elif change in ("unowned", "unready"):
        host["owned" if change == "unowned" else "ready"] = False
    elif change == "unknown":
        host["observed"] = None
    elif change == "stale":
        args[1]["expires_at"] = 150
    elif change == "incomplete":
        args[1]["complete"] = False
    else:
        args[0]["capacity"]["machine_limit"] = 1
    assert blocker in compute(*args)["blockers"]


@pytest.mark.parametrize("change", [
    "missing-slot", "extra-slot", "bool-count", "negative-count", "zero-max", "zero-resource",
    "min-above-max", "limit-below-request", "unknown-template", "revision", "source",
    "catalog-shadow", "missing-support", "wrong-capability", "cycle", "unknown-slot",
    "duplicate-host", "duplicate-capability", "overflow", "unsupported-expression", "config-shape",
])
def test_invalid_composition_is_explicit_error_not_success(change):
    args = inputs()
    runner = allocation(args[0])
    slots = runner["slots"]
    support = args[2]["templates"][0]
    if change == "missing-slot":
        del slots["configuration_ref"]
    elif change == "extra-slot":
        slots["command"] = "untrusted"
    elif change == "bool-count":
        slots["max_runners"] = True
    elif change == "negative-count":
        slots["max_runners"] = -1
    elif change == "zero-max":
        slots["max_runners"] = 0
    elif change == "zero-resource":
        slots["memory_bytes"] = 0
    elif change == "min-above-max":
        slots["min_runners"] = 3
    elif change == "limit-below-request":
        slots["limit_memory_bytes"] = 1
    elif change == "unknown-template":
        runner["template"]["id"] = "unknown"
    elif change in ("revision", "source"):
        runner["template"][change] = "changed"
    elif change == "catalog-shadow":
        args[2]["templates"].append(copy.deepcopy(support))
    elif change == "missing-support":
        slots["cluster"] = "unknown"
    elif change == "wrong-capability":
        support["provides"].remove("arc.controller")
    elif change == "cycle":
        support["requires"] = {"actions.runner": "runners"}
    elif change == "unknown-slot":
        support["configuration"]["configuration_ref"] = {"slot": "missing"}
    elif change == "duplicate-host":
        args[1]["hosts"].append(copy.deepcopy(args[1]["hosts"][0]))
    elif change == "duplicate-capability":
        support["provides"].append(support["provides"][0])
    elif change == "overflow":
        slots["cpu_millis"] = slots["limit_cpu_millis"] = 2**63 - 1
    elif change == "config-shape":
        support["configuration"] = {"slot": "config"}
    else:
        support["configuration"]["configuration_ref"] = {"slot": "config", "extra": "not-an-expression"}
    with pytest.raises(PlanError):
        compute(*args)


def write_inputs(path):
    args = inputs(int(time.time()) - 1)
    result = []
    for name, value in zip(("config", "inventory", "catalog"), args):
        target = path / f"{name}.json"
        target.write_text(json.dumps(value), encoding="utf-8")
        result.extend((f"--{name}", str(target)))
    return result


def test_actual_cli_reads_contributed_slots_and_reports_errors(tmp_path, capsys):
    args = write_inputs(tmp_path)
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    assert main(["design", *args]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["composition"]["allocations"]["runners"]["configuration"]["max_runners"] == 2
    assert before == {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    (tmp_path / "config.json").write_bytes(canonical(result["suggested_config"]))
    assert main(["design", *args]) == 0
    assert json.loads(capsys.readouterr().out)["suggested_config"] == result["suggested_config"]
    inventory = tmp_path / "inventory.json"
    changed = json.loads(inventory.read_text())
    changed["hosts"][0]["capacity"]["disk_bytes"] -= 1
    inventory.write_text(json.dumps(changed))
    assert main(["design", *args]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "blocked"
    inventory.write_text('{"duplicate":1,"duplicate":2}')
    assert main(["design", *args]) == 1
    assert "invalid bounded JSON input" in capsys.readouterr().err

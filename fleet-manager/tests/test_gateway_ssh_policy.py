from copy import deepcopy
from dataclasses import FrozenInstanceError
import hashlib
import json
import os
import subprocess
import sys

import pytest

from fleet_manager.gateway_ssh_policy import GatewaySSHPolicy, SSHPolicyDecision
from fleet_manager.planning import MAX_BYTES, MAX_ITEMS, PlanError, canonical, digest


def machine(name="worker-a", environment="windows", driver="gateway", **changes):
    row = {
        "machine": name, "environment": environment, "driver": driver,
        "role": "interactive", "inbound": True, "outbound": True,
    }
    if driver == "gateway":
        row.update(controller="gateway-a", gateway_egress=True)
    row.update(changes)
    return row


def projection(*rows, **changes):
    value = {
        "schema": "fleet-manager.gateway-ssh-policy", "version": 1, "revision": "r1",
        "controllers": ["gateway-a", "gateway-b"],
        "machines": list(rows) if rows else [machine()],
    }
    value.update(changes)
    return value


def guest_host(**host_changes):
    guest = machine(environment="wsl", gateway_egress=False, outbound=False,
                    host_hop={"machine": "worker-a", "environment": "windows"})
    host = machine(role="service", outbound=False)
    host.update(host_changes)
    return guest, host


@pytest.mark.parametrize("driver", ["gateway", "dtssh", "cloudflare"])
@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("inbound,outbound", [(False, False), (False, True), (True, False), (True, True)])
def test_driver_platform_and_independent_directions(driver, environment, inbound, outbound):
    selected = GatewaySSHPolicy.from_dict(projection(machine(
        environment=environment, driver=driver, inbound=inbound, outbound=outbound,
    )))
    decision = selected.resolve("worker-a", environment)
    value = decision.to_dict()
    assert value == {
        "schema": "fleet-manager.gateway-ssh-decision", "version": 1,
        "machine": "worker-a", "environment": environment, "backend": driver,
        "role": "interactive", "controller": "gateway-a" if driver == "gateway" else None,
        "state": "configured" if inbound or outbound else "disabled",
        "reason": "declared-policy" if inbound or outbound else "ssh-disabled",
        "requested_inbound": inbound, "requested_outbound": outbound,
        "effective_inbound": inbound, "effective_outbound": outbound,
        "gateway_egress": driver == "gateway", "host_path": [],
        "policy_revision": "r1", "policy_digest": selected.policy_digest,
        "execution_authorized": False, "live_readiness_verified": False,
    }
    assert decision.decision_digest == digest(value)
    assert "decision_digest" not in value


@pytest.mark.parametrize("environment,role", [
    ("windows", "interactive"), ("windows", "service"), ("linux", "service"),
    ("wsl", "interactive"), ("linux", "client-only"),
])
def test_roles_are_explicit_intent_not_platform_or_live_discovery(environment, role):
    selected = GatewaySSHPolicy.from_dict(projection(machine(
        environment=environment, role=role, inbound=role != "client-only",
    )))
    assert selected.resolve("worker-a", environment).to_dict()["role"] == role


@pytest.mark.parametrize("inbound,outbound", [(False, False), (False, True), (True, False), (True, True)])
@pytest.mark.parametrize("driver", ["gateway", "dtssh", "cloudflare"])
def test_offline_preserves_selection_but_requests_no_effects(driver, inbound, outbound):
    row = machine(driver=driver, role="offline", inbound=inbound, outbound=outbound)
    value = GatewaySSHPolicy.from_dict(projection(row)).resolve("worker-a", "windows").to_dict()
    assert (value["state"], value["reason"], value["backend"]) == ("offline", "offline-role", driver)
    assert (value["requested_inbound"], value["requested_outbound"]) == (inbound, outbound)
    assert not value["effective_inbound"] and not value["effective_outbound"]
    assert value["execution_authorized"] is value["live_readiness_verified"] is False


def test_exact_machine_environment_and_multiple_controller_bindings():
    rows = [
        machine(), machine(environment="wsl", driver="dtssh"),
        machine("worker-b", "linux", controller="gateway-b", role="service"),
    ]
    selected = GatewaySSHPolicy.from_dict(projection(*rows))
    assert selected.resolve("worker-a", "windows").to_dict()["controller"] == "gateway-a"
    assert selected.resolve("worker-a", "wsl").to_dict()["backend"] == "dtssh"
    assert selected.resolve("worker-b", "linux").to_dict()["controller"] == "gateway-b"
    for target in [
        ("WORKER-A", "windows"), ("worker-a", "linux"),
        ("worker-b", "wsl"), ("missing", "windows"),
    ]:
        with pytest.raises(PlanError, match="not declared"):
            selected.resolve(*target)
    for target in [(None, "windows"), ("worker-a", []), ("worker-a", "macos")]:
        with pytest.raises(PlanError):
            selected.resolve(*target)


def test_guest_inbound_uses_declared_host_without_granting_host_logical_outbound():
    guest, host = guest_host()
    selected = GatewaySSHPolicy.from_dict(projection(guest, host))
    value = selected.resolve("worker-a", "wsl").to_dict()
    assert value["state"] == "configured"
    assert value["effective_inbound"] and not value["effective_outbound"]
    assert not value["gateway_egress"]
    assert value["host_path"] == [{"machine": "worker-a", "environment": "windows"}]
    host_value = selected.resolve("worker-a", "windows").to_dict()
    assert host_value["gateway_egress"] and not host_value["effective_outbound"]


@pytest.mark.parametrize("changes", [
    {"role": "offline"}, {"inbound": False}, {"gateway_egress": False},
    {"inbound": False, "outbound": True},
    {"role": "client-only", "inbound": False, "outbound": True},
])
def test_unavailable_host_blocks_guest_without_fallback(changes):
    guest, host = guest_host(**changes)
    value = GatewaySSHPolicy.from_dict(projection(guest, host)).resolve("worker-a", "wsl").to_dict()
    assert (value["state"], value["reason"]) == ("blocked", "host-hop-unavailable")
    assert not value["effective_inbound"] and not value["effective_outbound"]
    assert value["backend"] == "gateway" and value["controller"] == "gateway-a"


def test_guest_outbound_is_not_invented_by_host_mediation():
    guest, host = guest_host()
    guest["outbound"] = True
    value = GatewaySSHPolicy.from_dict(projection(guest, host)).resolve("worker-a", "wsl").to_dict()
    assert (value["state"], value["reason"]) == ("blocked", "gateway-egress-required")
    assert not value["effective_inbound"] and not value["effective_outbound"]


@pytest.mark.parametrize("inbound,outbound", [(True, False), (False, True), (True, True)])
def test_missing_connector_egress_is_explicitly_blocked(inbound, outbound):
    row = machine(gateway_egress=False, inbound=inbound, outbound=outbound)
    value = GatewaySSHPolicy.from_dict(projection(row)).resolve("worker-a", "windows").to_dict()
    assert (value["state"], value["reason"]) == ("blocked", "gateway-egress-required")
    assert not value["effective_inbound"] and not value["effective_outbound"]


def test_ordered_multihop_path_and_offline_precedence():
    guest, host = guest_host(gateway_egress=False,
                            host_hop={"machine": "worker-b", "environment": "linux"})
    terminal = machine("worker-b", "linux", role="service", outbound=False)
    selected = GatewaySSHPolicy.from_dict(projection(guest, host, terminal))
    value = selected.resolve("worker-a", "wsl").to_dict()
    assert value["state"] == "configured"
    assert value["host_path"] == [
        {"machine": "worker-a", "environment": "windows"},
        {"machine": "worker-b", "environment": "linux"},
    ]
    guest["role"] = "offline"
    host["role"] = "offline"
    selected = GatewaySSHPolicy.from_dict(projection(guest, host, terminal))
    assert selected.resolve("worker-a", "wsl").to_dict()["state"] == "offline"


def test_blocked_intermediate_policy_cannot_be_used_as_a_host_hop():
    guest, host = guest_host(
        gateway_egress=False, outbound=True,
        host_hop={"machine": "worker-b", "environment": "linux"},
    )
    terminal = machine("worker-b", "linux", role="service", outbound=False)
    selected = GatewaySSHPolicy.from_dict(projection(guest, host, terminal))
    assert selected.resolve("worker-a", "windows").to_dict()["state"] == "blocked"
    guest_value = selected.resolve("worker-a", "wsl").to_dict()
    assert (guest_value["state"], guest_value["reason"]) == ("blocked", "host-hop-unavailable")
    assert not guest_value["effective_inbound"]


@pytest.mark.parametrize("driver", ["dtssh", "cloudflare"])
def test_non_gateway_selection_needs_no_gateway_controller(driver):
    selected = GatewaySSHPolicy.from_dict(projection(machine(driver=driver), controllers=[]))
    assert selected.resolve("worker-a", "windows").to_dict()["controller"] is None


def test_maximum_declared_controller_and_target_counts_are_accepted():
    controllers = [f"gateway-{i:03}" for i in range(MAX_ITEMS)]
    rows = [machine(f"worker-{i:03}", controller=controller)
            for i, controller in enumerate(controllers)]
    selected = GatewaySSHPolicy.from_dict(projection(*rows, controllers=controllers))
    assert selected.resolve(f"worker-{MAX_ITEMS - 1:03}", "windows").to_dict()["state"] == "configured"


@pytest.mark.parametrize("kind", [
    "missing", "different-controller", "other-driver", "cycle", "self-cycle", "local-and-host",
])
def test_invalid_host_relationships_refuse_before_any_decision(kind):
    guest, host = guest_host()
    if kind == "missing":
        host["machine"] = "worker-b"
    elif kind == "different-controller":
        host["controller"] = "gateway-b"
    elif kind == "other-driver":
        host = machine(driver="dtssh", outbound=False)
    elif kind == "cycle":
        host.update(gateway_egress=False,
                    host_hop={"machine": "worker-a", "environment": "wsl"})
    elif kind == "self-cycle":
        guest["host_hop"]["environment"] = "wsl"
    else:
        guest["gateway_egress"] = True
    with pytest.raises(PlanError):
        GatewaySSHPolicy.from_dict(projection(guest, host))


@pytest.mark.parametrize("field", ["schema", "version", "revision", "controllers", "machines"])
def test_missing_root_fields_refuse(field):
    value = projection()
    del value[field]
    with pytest.raises(PlanError):
        GatewaySSHPolicy.from_dict(value)


@pytest.mark.parametrize("field", [
    "machine", "environment", "driver", "role", "inbound", "outbound", "controller", "gateway_egress",
])
def test_missing_row_fields_refuse(field):
    row = machine()
    del row[field]
    with pytest.raises(PlanError):
        GatewaySSHPolicy.from_dict(projection(row))


@pytest.mark.parametrize("changes", [
    {"schema": "other"}, {"version": True}, {"version": 2}, {"revision": []},
    {"revision": "../r1"}, {"revision": "x" * 129}, {"extra": "secret-value"},
    {"controllers": None}, {"controllers": {}}, {"controllers": [None]},
    {"controllers": ["gateway-a", "Gateway-A"]},
    {"controllers": ["gateway-a", "gateway-a"]},
    {"controllers": ["x" * 129]}, {"controllers": [f"g{i}" for i in range(MAX_ITEMS + 1)]},
    {"machines": []}, {"machines": {}}, {"machines": [False]},
    {"machines": [machine() for _ in range(MAX_ITEMS + 1)]},
])
def test_invalid_root_shape_refuses(changes):
    value = projection(**changes)
    before = deepcopy(value)
    with pytest.raises(PlanError):
        GatewaySSHPolicy.from_dict(value)
    assert value == before


@pytest.mark.parametrize("changes", [
    {"machine": []}, {"machine": "x" * 129}, {"machine": "../private-value"},
    {"environment": []}, {"environment": "macos"}, {"driver": []}, {"driver": "direct"},
    {"role": []}, {"role": "automatic"}, {"role": "client-only"},
    {"inbound": 1}, {"outbound": None}, {"gateway_egress": "true"},
    {"controller": []}, {"controller": "Gateway-A"}, {"controller": "unknown"},
    {"host_hop": None}, {"host_hop": []},
    {"host_hop": {"machine": "worker-a", "environment": "windows", "extra": True}},
    {"host_hop": {"machine": "worker-a", "environment": []}},
    {"extra": "secret-value"},
])
def test_invalid_row_shape_refuses_without_echoing_values(changes):
    row = machine(**changes)
    value = projection(row)
    before = deepcopy(value)
    with pytest.raises(PlanError) as error:
        GatewaySSHPolicy.from_dict(value)
    assert "private-value" not in str(error.value) and "secret-value" not in str(error.value)
    assert value == before


@pytest.mark.parametrize("rows", [
    [machine(), machine()],
    [machine(), machine("WORKER-A")],
    [machine(), machine("WORKER-A", "wsl")],
])
def test_duplicate_targets_and_cross_environment_case_collisions_refuse(rows):
    with pytest.raises(PlanError):
        GatewaySSHPolicy.from_dict(projection(*rows))


@pytest.mark.parametrize("driver", ["dtssh", "cloudflare"])
@pytest.mark.parametrize("field,value", [
    ("controller", None), ("gateway_egress", False), ("host_hop", None),
])
def test_other_drivers_cannot_carry_gateway_only_fields(driver, field, value):
    with pytest.raises(PlanError):
        GatewaySSHPolicy.from_dict(projection(machine(driver=driver, **{field: value})))


def test_normalized_semantic_digest_and_immutable_snapshots():
    guest, host = guest_host()
    source = projection(guest, host, controllers=["gateway-b", "gateway-a"])
    before = deepcopy(source)
    selected = GatewaySSHPolicy.from_dict(source)
    reordered = projection(host, guest)
    assert source == before
    assert selected.encoded == GatewaySSHPolicy.from_dict(reordered).encoded
    assert selected.policy_digest == hashlib.sha256(selected.encoded).hexdigest()
    source["machines"][0]["host_hop"]["machine"] = "foreign"
    source["controllers"].clear()
    output = selected.to_dict()
    output["machines"].clear()
    decision = selected.resolve("worker-a", "wsl")
    decision.to_dict()["host_path"].clear()
    assert selected.resolve("worker-a", "wsl").encoded == decision.encoded
    assert selected.to_dict() == GatewaySSHPolicy.from_dict(before).to_dict()
    assert SSHPolicyDecision(decision.encoded) == decision
    with pytest.raises(FrozenInstanceError):
        selected.encoded = b"changed"
    with pytest.raises(FrozenInstanceError):
        decision.encoded = b"changed"


@pytest.mark.parametrize("changes", [
    {"revision": "r2"}, {"controllers": ["gateway-a"]},
    {"machines": [machine(outbound=False)]},
    {"machines": [machine(controller="gateway-b")]},
])
def test_declared_revision_or_policy_changes_invalidate_digest(changes):
    original = GatewaySSHPolicy.from_dict(projection())
    changed = GatewaySSHPolicy.from_dict(projection(**changes))
    assert changed.policy_digest != original.policy_digest
    assert changed.resolve("worker-a", "windows").decision_digest != (
        original.resolve("worker-a", "windows").decision_digest
    )


@pytest.mark.parametrize("raw", [
    b"\xff", b'{"schema":"first","schema":"second"}',
    b" " * (MAX_BYTES + 1), bytearray(b"{}"), "{}",
], ids=["invalid-utf8", "duplicate-keys", "oversized", "mutable-bytes", "text"])
def test_encoded_snapshots_require_immutable_bounded_json(raw):
    for constructor in (GatewaySSHPolicy, SSHPolicyDecision):
        with pytest.raises(PlanError):
            constructor(raw)


def test_encoded_snapshots_require_normalized_canonical_encoding():
    value = projection(machine("worker-b"), machine(), controllers=["gateway-b", "gateway-a"])
    with pytest.raises(PlanError, match="normalized"):
        GatewaySSHPolicy(canonical(value))
    selected = GatewaySSHPolicy.from_dict(value)
    with pytest.raises(PlanError):
        GatewaySSHPolicy(json.dumps(selected.to_dict(), indent=1).encode())
    decision = selected.resolve("worker-a", "windows")
    with pytest.raises(PlanError):
        SSHPolicyDecision(json.dumps(decision.to_dict(), indent=1).encode())


@pytest.mark.parametrize("changes", [
    {"execution_authorized": True}, {"live_readiness_verified": True},
    {"execution_authorized": 0}, {"effective_outbound": 1},
    {"effective_inbound": False}, {"state": []}, {"state": "ready"},
    {"state": "offline"}, {"state": "disabled"}, {"state": "blocked"},
    {"reason": []}, {"reason": "ready"}, {"role": "offline"},
    {"policy_digest": "F" * 64}, {"policy_digest": None},
    {"controller": None}, {"backend": "dtssh"}, {"extra": "field"},
    {"host_path": {}}, {"host_path": [{"machine": "worker-a", "environment": "windows"}]},
    {"host_path": [{"machine": "worker-b", "environment": []}]},
])
def test_decision_snapshots_refuse_forged_readiness_or_inconsistent_permissions(changes):
    value = GatewaySSHPolicy.from_dict(projection()).resolve("worker-a", "windows").to_dict()
    value.update(changes)
    with pytest.raises(PlanError):
        SSHPolicyDecision(canonical(value))


@pytest.mark.parametrize("row", [
    machine(), machine(environment="wsl", role="offline"),
    machine(environment="linux", driver="cloudflare", role="service"),
])
def test_fresh_installed_isolated_consumer_is_inert(tmp_path, row):
    script = """
import json, socket, subprocess, sys
from pathlib import Path
def forbidden(*args, **kwargs):
    raise AssertionError('policy consumer must not access network or launch processes')
socket.socket = socket.create_connection = subprocess.Popen = forbidden
import fleet_manager.gateway_ssh_policy as module
from fleet_manager.gateway_ssh_policy import GatewaySSHPolicy
assert Path(module.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
policy = GatewaySSHPolicy(sys.argv[1].encode())
decision = policy.resolve(sys.argv[2], sys.argv[3])
assert not decision.to_dict()['execution_authorized']
assert not decision.to_dict()['live_readiness_verified']
assert list(Path.cwd().iterdir()) == []
print(json.dumps({'policy': policy.policy_digest, 'decision': decision.decision_digest}))
"""
    selected = GatewaySSHPolicy.from_dict(projection(row))
    decision = selected.resolve(row["machine"], row["environment"])
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, selected.encoded.decode(),
         row["machine"], row["environment"]],
        cwd=tmp_path, capture_output=True, text=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "policy": selected.policy_digest, "decision": decision.decision_digest,
    }
    assert list(tmp_path.iterdir()) == []

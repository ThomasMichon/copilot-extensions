import base64
from copy import deepcopy
import struct

import pytest

from fleet_manager.gateway_ssh_policy import GatewaySSHPolicy
from fleet_manager.gateway_ssh_provider import (
    directory_snapshot, project_gateway_authorization, project_gateway_profiles,
)
from fleet_manager.planning import PlanError


def key(number):
    wire = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", 32) + bytes([number]) * 32
    return "ssh-ed25519 " + base64.b64encode(wire).decode()


def fixtures(**local_changes):
    rows = [{
        "machine": name, "environment": "linux", "driver": "gateway",
        "role": "interactive", "inbound": True, "outbound": True,
        "controller": "gateway-a", "gateway_egress": True,
    } for name in ("worker-a", "worker-b")]
    rows[0].update(local_changes)
    policy = GatewaySSHPolicy.from_dict({
        "schema": "fleet-manager.gateway-ssh-policy", "version": 1, "revision": "r1",
        "controllers": ["gateway-a", "gateway-b"], "machines": rows,
    })
    directory = {
        "schema": "fleet-manager.gateway-ssh-directory", "version": 1,
        "controller": "gateway-a", "revision": 1, "policy_digest": policy.policy_digest,
        "machines": [{
            "machine": row["machine"], "environment": row["environment"],
            "alias": row["machine"], "user": "owner", "public_key": key(index + 1),
            "host_key": key(index + 11), "reverse_port": 24001 + index,
        } for index, row in enumerate(rows)],
    }
    return policy, directory


def project(policy, directory, **changes):
    arguments = {
        "machine": "worker-a", "environment": "linux", "gateway": "selected-gateway",
        "identity_file": "/selected/local/key", "local_public_key": key(1),
    }
    arguments.update(changes)
    return project_gateway_profiles(policy, directory, **arguments)


def test_real_policy_projects_selected_public_bindings_without_authorizing_execution():
    policy, directory = fixtures()
    before = deepcopy(directory)
    value = project(policy, directory)
    assert value["controller"] == "gateway-a" and value["directory_revision"] == 1
    assert value["config"]["schema"] == "agent-ssh.gateway-profile"
    assert value["config"]["machines"] == [{
        "name": row["alias"], "user": row["user"],
        "reverse_port": row["reverse_port"], "host_key": row["host_key"],
    } for row in directory["machines"]]
    assert not value["execution_authorized"] and not value["live_readiness_verified"]
    assert directory == before
    directory["machines"].reverse()
    assert project(policy, directory) == value


@pytest.mark.parametrize("inbound,outbound", [(False, False), (True, False), (False, True), (True, True)])
def test_source_directions_are_independent(inbound, outbound):
    policy, directory = fixtures(inbound=inbound, outbound=outbound)
    value = project(policy, directory)
    aliases = [row["name"] for row in value["config"]["machines"]]
    assert aliases == (["worker-a", "worker-b"] if inbound and outbound else ["worker-b"] if outbound else [])


def test_peer_outbound_is_not_a_prerequisite_for_its_inbound_profile():
    policy, directory = fixtures(outbound=False)
    value = project(policy, directory, machine="worker-b", local_public_key=key(2))
    assert [row["name"] for row in value["config"]["machines"]] == ["worker-a", "worker-b"]


@pytest.mark.parametrize("change", [
    "wrong-policy", "wrong-controller", "unknown-machine", "duplicate-target",
    "duplicate-alias", "duplicate-key", "duplicate-port", "missing-pin",
    "missing-port", "bad-key", "bad-user", "unknown-field", "bool-revision",
])
def test_invalid_or_mismatched_directory_refused(change):
    policy, directory = fixtures()
    row = directory["machines"][1]
    if change == "wrong-policy":
        directory["policy_digest"] = "0" * 64
    elif change == "wrong-controller":
        directory["controller"] = "gateway-b"
    elif change == "unknown-machine":
        row["machine"] = "unknown"
    elif change == "duplicate-target":
        row["machine"] = "worker-a"
    elif change == "duplicate-alias":
        row["alias"] = "WORKER-A"
    elif change == "duplicate-key":
        row["public_key"] = key(1)
    elif change == "duplicate-port":
        row["reverse_port"] = 24001
    elif change == "missing-pin":
        row["host_key"] = None
    elif change == "missing-port":
        row["reverse_port"] = None
    elif change == "bad-key":
        row["public_key"] = "ssh-ed25519 PRIVATE"
    elif change == "bad-user":
        row["user"] = "owner\nHost injected"
    elif change == "unknown-field":
        row["execution_authorized"] = True
    else:
        directory["revision"] = True
    with pytest.raises(PlanError):
        project(policy, directory)


def test_unadmitted_or_wrong_local_identity_never_gets_a_profile():
    policy, directory = fixtures()
    with pytest.raises(PlanError, match="identity"):
        project(policy, directory, local_public_key=key(9))
    directory["machines"].pop(0)
    with pytest.raises(PlanError, match="identity"):
        project(policy, directory)


def test_client_only_source_requires_no_server_pin_or_reverse_listener():
    policy, directory = fixtures(role="client-only", inbound=False)
    directory["machines"][0].update(host_key=None, reverse_port=None)
    value = project(policy, directory)
    assert [row["name"] for row in value["config"]["machines"]] == ["worker-b"]


def test_projection_uses_a_snapshot_not_a_mutable_directory_alias():
    policy, directory = fixtures()
    snapshot = directory_snapshot(directory, policy)
    directory["machines"][0]["alias"] = "changed"
    assert snapshot["machines"][0]["alias"] == "worker-a"


def test_host_mediation_is_not_silently_treated_as_native_egress():
    policy, directory = fixtures(
        environment="wsl", gateway_egress=False,
        host_hop={"machine": "worker-b", "environment": "linux"},
    )
    with pytest.raises(PlanError, match="mediation"):
        project(policy, directory, environment="wsl")


@pytest.mark.parametrize("inbound,outbound", [(False, False), (True, False), (False, True), (True, True)])
def test_login_authorization_uses_target_inbound_and_source_outbound_independently(inbound, outbound):
    policy, directory = fixtures(inbound=inbound, outbound=outbound)
    value = project_gateway_authorization(
        policy, directory, machine="worker-a", environment="linux", local_public_key=key(1),
    )
    assert [row["machine"] for row in value["peers"]] == (
        ["worker-a", "worker-b"] if inbound and outbound else ["worker-b"] if inbound else []
    )
    assert not value["execution_authorized"] and not value["live_readiness_verified"]


def test_client_only_source_may_log_in_without_its_own_inbound_service():
    policy, directory = fixtures(role="client-only", inbound=False)
    directory["machines"][0].update(host_key=None, reverse_port=None)
    value = project_gateway_authorization(
        policy, directory, machine="worker-b", environment="linux", local_public_key=key(2),
    )
    assert [row["machine"] for row in value["peers"]] == ["worker-a", "worker-b"]
    with pytest.raises(PlanError, match="identity"):
        project_gateway_authorization(
            policy, directory, machine="worker-b", environment="linux", local_public_key=key(1),
        )


def test_authorization_refuses_unimplemented_host_mediation():
    policy, directory = fixtures(
        environment="wsl", gateway_egress=False,
        host_hop={"machine": "worker-b", "environment": "linux"},
    )
    with pytest.raises(PlanError, match="mediation"):
        project_gateway_authorization(
            policy, directory, machine="worker-a", environment="wsl", local_public_key=key(1),
        )
    value = project_gateway_authorization(
        policy, directory, machine="worker-b", environment="linux", local_public_key=key(2),
    )
    assert [row["machine"] for row in value["peers"]] == ["worker-b"]

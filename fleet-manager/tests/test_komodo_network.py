"""Complete running membership without weakening pending or stopped custody."""

from __future__ import annotations

import sys
from typing import Any

import pytest

from fleet_manager.komodo_network import network_fingerprint, verify_network
from fleet_manager.komodo_selection import KomodoSelection
from fleet_manager.planning import PlanError
from test_komodo_native import native_rig as native_rig

pytestmark = [
    pytest.mark.skipif(sys.platform != "linux", reason="Linux native network custody"),
    pytest.mark.client_distribution_full,
]


@pytest.mark.parametrize("case", [
    "running-present", "running-missing", "pending-running-missing",
    "created-pending", "created-not-pending", "stopped-missing",
])
def test_network_membership_requires_running_nodes_but_preserves_pending(
    native_rig: Any, case: str,
) -> None:
    rig = native_rig
    root = rig["root"] / "contract-runtime"
    root.mkdir(mode=0o700)
    selected = KomodoSelection(
        rig["selected"], "fleet-a", "install-a", 1, "policy-a", 1900001000,
        "a" * 64, root, rig["tooling"], rig["material"])
    project = selected.declaration.to_dict()["project"]
    name, identity, node = project + "_default", "b" * 64, "c" * 64
    running, created = "running" in case, case.startswith("created")
    containers = {"core": {
        "Id": node, "State": {"Status": "created" if created else "running" if running else "exited",
                             "Running": running},
        "NetworkSettings": {"Networks": {name: {"NetworkID": "" if created else identity}}},
    }}
    network = {
        "Id": identity, "Name": name, "Driver": "bridge", "Scope": "local",
        "Internal": False, "Attachable": False, "Ingress": False, "ConfigOnly": False,
        "ConfigFrom": {"Network": ""}, "Options": {}, "IPAM": {"Driver": "default"},
        "Labels": {"io.fleet-manager.owner": selected.owner,
                   "com.docker.compose.project": project, "com.docker.compose.network": "default"},
        "Containers": {node: {"Name": "core"}} if case == "running-present" else {},
    }
    pending = case in ("pending-running-missing", "created-pending")
    if case in ("running-missing", "pending-running-missing", "created-not-pending"):
        with pytest.raises(PlanError, match="membership|attachment"):
            verify_network(network, selected, containers, None, created=True, pending=pending)
    else:
        assert verify_network(network, selected, containers, None, created=True,
                              pending=pending) == network_fingerprint(network)
    assert not (rig["directory"] / "calls.jsonl").exists()
    assert not (root / selected.resource).exists()

"""Synthetic attachment observations only; no Docker or network effects."""

from __future__ import annotations

import copy
import json
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from test_reproducer_lab_monitoring import experiment, reader
from network import inspect_inventory, selected_containers

SUBJECT, PEER, NETWORK = "c" * 64, "d" * 64, "e" * 64


def endpoint(item: str) -> str:
    return "a" * 64 if item == SUBJECT else "b" * 64


def inventory() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    name = experiment().resource_prefix + "-internal"
    containers = [
        {"Id": item, "State": {"Running": True}, "HostConfig": {"NetworkMode": name},
         "NetworkSettings": {"Networks": {
             name: {"NetworkID": NETWORK, "EndpointID": endpoint(item)},
         }}}
        for item in (SUBJECT, PEER)
    ]
    networks = [{
        "Id": NETWORK, "Created": "2026-01-01T00:00:00Z", "Name": name,
        "Driver": "bridge", "Scope": "local", "Internal": True, "Attachable": False,
        "Ingress": False, "Options": {},
        "Containers": {item: {"EndpointID": endpoint(item)} for item in (SUBJECT, PEER)},
    }]
    return containers, networks


def test_exact_physical_inventory_is_not_egress_or_custody_proof() -> None:
    containers, networks = inventory()
    original = copy.deepcopy((containers, networks))
    observed = inspect_inventory(experiment(), (SUBJECT, PEER), containers, networks)
    assert observed.network_id == NETWORK
    assert observed.container_ids == (SUBJECT, PEER)
    assert not hasattr(observed, "ready")
    assert (containers, networks) == original


def test_single_subject_inventory_without_peers() -> None:
    containers, networks = inventory()
    del networks[0]["Containers"][PEER]
    selected = selected_containers(SUBJECT, ())
    assert inspect_inventory(experiment(), selected, containers[:1], networks).container_ids == selected


@pytest.mark.parametrize("field,value", [
    ("Id", "short"), ("Name", "foreign"), ("Created", ""), ("Driver", "overlay"),
    ("Scope", "swarm"), ("Internal", False), ("Internal", 1), ("Attachable", True),
    ("Ingress", True), ("Options", {"com.docker.network.bridge.name": "foreign"}),
    ("Containers", {}), ("Containers", {SUBJECT: {}, PEER: {}, "f" * 64: {}}),
])
def test_unsafe_network_inventory(field: str, value: object) -> None:
    containers, networks = inventory()
    networks[0][field] = value
    with pytest.raises(ValueError):
        inspect_inventory(experiment(), (SUBJECT, PEER), containers, networks)


@pytest.mark.parametrize("field", [
    "Id", "Name", "Created", "Driver", "Scope", "Internal", "Attachable", "Ingress",
    "Options", "Containers",
])
def test_missing_network_fields_refuse(field: str) -> None:
    containers, networks = inventory()
    del networks[0][field]
    with pytest.raises(ValueError):
        inspect_inventory(experiment(), (SUBJECT, PEER), containers, networks)


@pytest.mark.parametrize("change", [
    "external", "missing", "wrong-network", "wrong-endpoint", "stopped", "shared-namespace",
    "duplicate", "foreign", "malformed",
])
def test_every_peer_attachment_is_checked(change: str) -> None:
    containers, networks = inventory()
    peer = containers[1]
    attached = peer["NetworkSettings"]["Networks"]
    name = next(iter(attached))
    if change == "external":
        attached["bridge"] = {}
    elif change == "missing":
        attached.clear()
    elif change == "wrong-network":
        attached[name]["NetworkID"] = "f" * 64
    elif change == "wrong-endpoint":
        attached[name]["EndpointID"] = "foreign"
    elif change == "stopped":
        peer["State"]["Running"] = False
    elif change == "shared-namespace":
        peer["HostConfig"]["NetworkMode"] = "container:" + SUBJECT
    elif change == "duplicate":
        containers[1] = containers[0]
    elif change == "foreign":
        peer["Id"] = "f" * 64
    else:
        networks[0]["Containers"][PEER] = None
    with pytest.raises(ValueError):
        inspect_inventory(experiment(), (SUBJECT, PEER), containers, networks)


@pytest.mark.parametrize("subject,peers", [
    ("short", ()), (SUBJECT, (SUBJECT,)), (SUBJECT, (PEER, PEER)),
    (SUBJECT, (PEER, "e" * 64, "f" * 64, "a" * 64)), (SUBJECT, ("short",)),
])
def test_selected_ids_are_full_distinct_and_bounded(subject: str, peers: tuple[str, ...]) -> None:
    with pytest.raises(ValueError):
        selected_containers(subject, peers)


def test_finite_reader_rechecks_inventory_and_retained_identity() -> None:
    containers, networks = inventory()
    calls: list[list[str]] = []

    def run(argv: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        assert 0 < timeout <= 10
        value = containers if argv[1] == "container" else networks
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")

    client = reader.DockerReader(Path("/selected/docker"), run=run, clock=lambda: 0)
    observed = client.network_inventory(experiment(), SUBJECT, peers=(PEER,))
    assert len(calls) == 4
    assert calls[0][1:] == ["container", "inspect", SUBJECT, PEER]
    assert all(call[1] in {"container", "network"} for call in calls)
    assert client.network_inventory(experiment(), SUBJECT, peers=(PEER,), expected=observed) == observed
    with pytest.raises(reader.ReadError, match="retained selection"):
        client.network_inventory(
            experiment(), SUBJECT, peers=(PEER,), expected=replace(observed, network_id="f" * 64),
        )


def test_replaced_network_between_samples_refuses() -> None:
    containers, networks = inventory()
    count = 0

    def run(argv: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        nonlocal count
        count += 1
        if count == 3:
            networks[0]["Created"] = "2026-01-02T00:00:00Z"
        value = containers if argv[1] == "container" else networks
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")

    client = reader.DockerReader(Path("/selected/docker"), run=run, clock=lambda: 0)
    with pytest.raises(reader.ReadError, match="changed during"):
        client.network_inventory(experiment(), SUBJECT, peers=(PEER,))


def test_reconnected_endpoint_between_samples_refuses() -> None:
    containers, networks = inventory()
    count = 0

    def run(argv: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        nonlocal count
        count += 1
        if count == 3:
            name = experiment().resource_prefix + "-internal"
            containers[1]["NetworkSettings"]["Networks"][name]["EndpointID"] = "f" * 64
            networks[0]["Containers"][PEER]["EndpointID"] = "f" * 64
        value = containers if argv[1] == "container" else networks
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")

    client = reader.DockerReader(Path("/selected/docker"), run=run, clock=lambda: 0)
    with pytest.raises(reader.ReadError, match="changed during"):
        client.network_inventory(experiment(), SUBJECT, peers=(PEER,))


def test_network_reader_deadline_includes_final_settlement() -> None:
    containers, networks = inventory()
    now = 0.0
    count = 0

    def run(argv: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
        nonlocal count, now
        count += 1
        if count == 4:
            now = 30.0
        value = containers if argv[1] == "container" else networks
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")

    client = reader.DockerReader(Path("/selected/docker"), run=run, clock=lambda: now)
    with pytest.raises(reader.ReadError, match="deadline exhausted"):
        client.network_inventory(experiment(), SUBJECT, peers=(PEER,))


@pytest.mark.parametrize("containers,networks", [
    ([], []), (None, [{}]), ([{}], {}), ([{}], [None]), ([{}], [{}, {}]),
])
def test_malformed_inventory_is_a_bounded_failure(containers: object, networks: object) -> None:
    with pytest.raises(ValueError):
        inspect_inventory(experiment(), (SUBJECT, PEER), containers, networks)

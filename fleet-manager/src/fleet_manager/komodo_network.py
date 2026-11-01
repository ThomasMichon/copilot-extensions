"""Physical bridge custody, independent of Compose's project-name convention."""

from __future__ import annotations

from typing import Any

from .komodo_incumbent import incumbent_topology
from .komodo_selection import KomodoSelection
from .planning import PlanError, digest


def network_fingerprint(network: dict[str, Any]) -> str:
    if set(network) == {"networks"}:
        return digest({name: network_fingerprint(item) for name, item in network["networks"].items()})
    return digest({key: value for key, value in network.items() if key != "Containers"})


def same_network_observation(before: dict[str, Any] | None, after: dict[str, Any] | None) -> bool:
    """Compare paired verified bridge identity and complete endpoint observations."""
    if before is None or after is None:
        return before is after
    if set(before) == {"networks"} or set(after) == {"networks"}:
        return before == after
    return (network_fingerprint(before) == network_fingerprint(after)
            and before["Containers"] == after["Containers"])


def verify_network(network: dict[str, Any] | None, selected: KomodoSelection,
                   containers: dict[str, dict[str, Any]], expected: str | None, *,
                   created: bool = False, pending: bool = False) -> str | None:
    if selected.declaration.preserving and selected.material.topology is not None:
        return _verify_incumbent(network, selected, containers, expected, created=created)
    if network is None:
        if expected is not None or containers:
            raise PlanError("Komodo retained native network is missing")
        return None
    name = selected.declaration.to_dict()["project"] + "_default"
    if (network.get("Name") != name or network.get("Driver") != "bridge"
            or network.get("Scope") != "local" or network.get("Internal") is not False
            or network.get("Attachable") is not False or network.get("Ingress") is not False
            or network.get("ConfigOnly") is not False
            or network.get("Options") not in (None, {})
            or network.get("ConfigFrom") not in (None, {"Network": ""})
            or not isinstance(network.get("Containers"), dict)):
        raise PlanError("Komodo native network differs from the fixed isolated bridge")
    identities = {item["Id"] for item in containers.values()}
    if not set(network["Containers"]).issubset(identities):
        raise PlanError("Komodo native network has foreign endpoints")
    running = {item["Id"] for item in containers.values() if item["State"].get("Running") is True}
    if not running.issubset(network["Containers"]):
        raise PlanError("Komodo running native container network membership is missing")
    for item in containers.values():
        attachments = item.get("NetworkSettings", {}).get("Networks")
        if (not isinstance(attachments, dict) or set(attachments) != {name}
                or not isinstance(attachments[name], dict)):
            raise PlanError("Komodo native container network attachment changed")
        unstarted = (pending and item["State"].get("Status") == "created"
                     and item["State"].get("Running") is False
                     and item["Id"] not in network["Containers"]
                     and attachments[name].get("NetworkID") == "")
        if attachments[name].get("NetworkID") != network["Id"] and not unstarted:
            raise PlanError("Komodo native container network attachment changed")
        if item["Id"] in running:
            endpoint = network["Containers"][item["Id"]]
            identity = attachments[name].get("EndpointID")
            if (type(endpoint) is not dict or type(identity) is not str or not identity
                    or endpoint.get("EndpointID") != identity):
                raise PlanError("Komodo native network attachment identity differs")
    actual = network_fingerprint(network)
    if expected is not None:
        if actual != expected:
            raise PlanError("Komodo retained native network was replaced or changed")
    elif not created:
        raise PlanError("Komodo incumbent network requires exact existing-owner adoption")
    else:
        labels = network.get("Labels") or {}
        if (selected.adopted_network is not None or any(labels.get(key) != value for key, value in {
            "io.fleet-manager.owner": selected.owner,
            "com.docker.compose.project": selected.declaration.to_dict()["project"],
            "com.docker.compose.network": "default",
        }.items())):
            raise PlanError("Komodo created network lacks fixed owner evidence")
    return actual


def _verify_incumbent(network: dict[str, Any] | None, selected: KomodoSelection,
                      containers: dict[str, dict[str, Any]], expected: str | None, *,
                      created: bool) -> str:
    topology = incumbent_topology(selected.material.topology)
    snapshots = {"networks": topology["networks"]}
    if (created or expected is None or type(network) is not dict or set(network) != {"networks"}
            or type(network["networks"]) is not dict
            or set(network["networks"]) != set(topology["networks"])
            or any(type(item) is not dict or type(item.get("Containers")) is not dict
                   for item in network["networks"].values())
            or network_fingerprint(network) != network_fingerprint(snapshots)
            or network_fingerprint(network) != expected):
        raise PlanError("Komodo retained incumbent topology was replaced or changed")
    for service, item in containers.items():
        names = topology["attachments"][service]
        attachments = item.get("NetworkSettings", {}).get("Networks")
        if (type(attachments) is not dict or set(attachments) != set(names)
                or item["HostConfig"].get("NetworkMode") != topology["modes"][service]):
            raise PlanError("Komodo incumbent service network attachment changed")
        for name in names:
            observed = network["networks"][name]
            attachment = attachments[name]
            endpoint = observed["Containers"].get(item["Id"])
            if (type(attachment) is not dict or attachment.get("NetworkID") != observed["Id"]
                    or (item["State"].get("Running") is True and (
                        type(endpoint) is not dict or type(attachment.get("EndpointID")) is not str
                        or not attachment["EndpointID"]
                        or endpoint.get("EndpointID") != attachment["EndpointID"]))):
                raise PlanError("Komodo incumbent network membership or attachment identity differs")
    for name, observed in network["networks"].items():
        allowed = {item["Id"] for service, item in containers.items()
                   if name in topology["attachments"][service]}
        if not set(observed["Containers"]) <= allowed:
            raise PlanError("Komodo incumbent network has foreign service endpoints")
    return network_fingerprint(network)


def retained_network(record: dict[str, Any], selected: KomodoSelection) -> str | None:
    retained = record["retirement"] or record["failed"] or record["good"]
    return retained["network"] if retained is not None else selected.adopted_network

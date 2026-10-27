"""Physical bridge custody, independent of Compose's project-name convention."""

from __future__ import annotations

from typing import Any

from .komodo_selection import KomodoSelection
from .planning import PlanError, digest


def network_fingerprint(network: dict[str, Any]) -> str:
    return digest({key: value for key, value in network.items() if key != "Containers"})


def verify_network(network: dict[str, Any] | None, selected: KomodoSelection,
                   containers: dict[str, dict[str, Any]], expected: str | None, *,
                   created: bool = False, pending: bool = False) -> str | None:
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


def retained_network(record: dict[str, Any], selected: KomodoSelection) -> str | None:
    retained = record["retirement"] or record["failed"] or record["good"]
    return retained["network"] if retained is not None else selected.adopted_network

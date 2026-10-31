"""Observed network inventory, not egress, custody or lab admission proof."""

from __future__ import annotations

import re
from dataclasses import dataclass

from boundary import Experiment


@dataclass(frozen=True)
class NetworkIdentity:
    network_id: str
    created_at: str
    container_ids: tuple[str, ...]
    endpoint_ids: tuple[tuple[str, str], ...]


def selected_containers(subject: str, peers: tuple[str, ...]) -> tuple[str, ...]:
    """Bound an explicitly selected physical inventory, without discovering peers."""
    selected = (subject, *peers)
    if (
        len(peers) > 3
        or any(not isinstance(item, str) or re.fullmatch(r"[0-9a-f]{64}", item) is None
               for item in selected)
        or len(set(selected)) != len(selected)
    ):
        raise ValueError("select a full subject ID and at most three distinct full peer IDs")
    return selected


def inspect_inventory(
    experiment: Experiment, selected: tuple[str, ...], containers: object, networks: object,
) -> NetworkIdentity:
    """Reject incomplete or foreign attachments in bounded Docker inspect output."""
    if experiment.network_mode != "internal":
        raise ValueError("internal network inventory requires the internal mode")
    if not selected:
        raise ValueError("a selected subject container is required")
    selected_containers(selected[0], selected[1:])
    if (
        not isinstance(networks, list) or len(networks) != 1
        or not isinstance(networks[0], dict)
    ):
        raise ValueError("network inspection inventory is incomplete")
    network = networks[0]
    name = f"{experiment.resource_prefix}-internal"
    identity, created = network.get("Id"), network.get("Created")
    endpoints = network.get("Containers")
    if (
        not isinstance(identity, str) or re.fullmatch(r"[0-9a-f]{64}", identity) is None
        or not isinstance(created, str) or not created
        or network.get("Name") != name or network.get("Driver") != "bridge"
        or network.get("Scope") != "local" or network.get("Internal") is not True
        or network.get("Attachable") is not False or network.get("Ingress") is not False
        or "Options" not in network or network["Options"] not in (None, {})
        or not isinstance(endpoints, dict) or set(endpoints) != set(selected)
    ):
        raise ValueError("selected internal network posture or membership differs")
    if not isinstance(containers, list) or len(containers) != len(selected):
        raise ValueError("container network inventory is incomplete")
    seen: set[str] = set()
    for container in containers:
        if not isinstance(container, dict):
            raise ValueError("container network inspection is malformed")
        container_id = container.get("Id")
        if not isinstance(container_id, str) or container_id not in selected or container_id in seen:
            raise ValueError("container network identity is foreign or duplicated")
        seen.add(container_id)
        settings, host = container.get("NetworkSettings"), container.get("HostConfig")
        state = container.get("State")
        if (
            not isinstance(settings, dict) or not isinstance(host, dict)
            or not isinstance(state, dict) or state.get("Running") is not True
            or host.get("NetworkMode") != name
        ):
            raise ValueError("selected container network posture is unavailable")
        attached = settings.get("Networks")
        if not isinstance(attached, dict) or set(attached) != {name}:
            raise ValueError("selected container has missing or foreign network attachments")
        attachment, endpoint = attached[name], endpoints[container_id]
        if (
            not isinstance(attachment, dict) or not isinstance(endpoint, dict)
            or attachment.get("NetworkID") != identity
            or not isinstance(attachment.get("EndpointID"), str)
            or re.fullmatch(r"[0-9a-f]{64}", attachment["EndpointID"]) is None
            or attachment["EndpointID"] != endpoint.get("EndpointID")
        ):
            raise ValueError("physical network endpoint identity differs")
    return NetworkIdentity(
        identity, created, tuple(sorted(selected)),
        tuple(sorted((item, endpoints[item]["EndpointID"]) for item in selected)),
    )

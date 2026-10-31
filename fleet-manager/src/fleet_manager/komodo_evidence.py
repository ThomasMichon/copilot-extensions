"""Order-independent native evidence with exact, non-migrating legacy compatibility."""

from __future__ import annotations

from typing import Any

from .komodo_declaration import fingerprint
from .planning import PlanError, canonical, digest

PREFIX = "mounts-v2:"
FIELDS = ("Id", "Image", "Config", "HostConfig", "Mounts")


def native_reference(value: object) -> str:
    """Validate an explicitly versioned reference or an opaque legacy SHA-256."""
    if type(value) is str and value.startswith(PREFIX):
        fingerprint(value[len(PREFIX):])
        return value
    return fingerprint(value)


def _native(container: dict[str, Any]) -> dict[str, Any]:
    result = {name: container[name] for name in FIELDS}
    mounts = result["Mounts"]
    destinations: set[str] = set()
    if type(mounts) is not list:
        raise PlanError("Komodo native mount custody is ambiguous")
    for mount in mounts:
        if type(mount) is not dict:
            raise PlanError("Komodo native mount custody is ambiguous")
        destination = mount.get("Destination")
        if type(destination) is not str or not destination or destination in destinations:
            raise PlanError("Komodo native mount custody is ambiguous")
        destinations.add(destination)
    result["Mounts"] = sorted(mounts, key=canonical)
    return result


def native_fingerprint(container: dict[str, Any]) -> str:
    """Never persist the secret-bearing native configuration itself."""
    return PREFIX + digest(_native(container))


def native_matches(reference: object, container: dict[str, Any]) -> bool:
    """Legacy hashes prove exact observations only, never a reconstructed mount set."""
    value = native_reference(reference)
    current = native_fingerprint(container)
    if value.startswith(PREFIX):
        return value == current
    return value == digest({name: container[name] for name in FIELDS})


def native_subset_matches(references: dict[str, str], containers: dict[str, dict[str, Any]]) -> bool:
    return set(containers) <= set(references) and all(
        native_matches(references[name], item) for name, item in containers.items())


def native_set_matches(references: dict[str, str], containers: dict[str, dict[str, Any]]) -> bool:
    return set(references) == set(containers) and native_subset_matches(references, containers)


def legacy_evidence_unproved(references: dict[str, str],
                            containers: dict[str, dict[str, Any]]) -> bool:
    return any(not native_reference(references[name]).startswith(PREFIX)
               and not native_matches(references[name], containers[name])
               for name in set(references) & set(containers))


def same_native_observation(before: dict[str, dict[str, Any]],
                            after: dict[str, dict[str, Any]]) -> bool:
    """Compare paired resource and process observations, ignoring only mount order."""
    return set(before) == set(after) and all(
        native_fingerprint(item) == native_fingerprint(after[name])
        and item.get("RestartCount") == after[name].get("RestartCount")
        and all(item["State"].get(key) == after[name]["State"].get(key)
                for key in ("Status", "Running", "Paused", "Restarting", "Dead", "Pid", "StartedAt"))
        for name, item in before.items())

"""Repository deployment selectors; evaluating them does not start a runtime."""
from __future__ import annotations

from collections.abc import Collection, Mapping


def parse_gate_manifest(raw: object, gate: dict[str, set[str]]) -> None:
    """Read both the native plugins list and legacy grouped services."""
    if not isinstance(raw, dict):
        return
    groups = [raw.get("plugins")]
    repos = raw.get("repos")
    if isinstance(repos, dict):
        groups.extend(repo.get("services") for repo in repos.values() if isinstance(repo, dict))
    for entries in groups:
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name, machines = entry.get("name"), entry.get("deploy_machines")
            if name and isinstance(machines, list):
                gate.setdefault(str(name), set()).update(str(machine) for machine in machines)


def allows_runtime(
    scope: str, name: str, machines: Collection[str], gate: Mapping[str, set[str]],
    *, present: bool,
) -> bool:
    if scope == "universal":
        return True
    if scope != "machine-gated":
        return False
    allowed = gate.get(name)
    if allowed is None:
        return not present
    return any(machine in allowed for machine in machines)

"""Stable identities for Worktree Picker data sources."""
from __future__ import annotations

from urllib.parse import quote

MACHINE_SSH_KIND = "machine-ssh"


def machine_ssh_id(machine: str, env: str) -> str:
    """Return the canonical identity for a machine/environment source."""
    machine_part = quote(machine.strip().casefold(), safe="-._~")
    env_part = quote(env.strip().casefold(), safe="-._~")
    return f"{MACHINE_SSH_KIND}:{machine_part}:{env_part}"


def resolve_id(
    kind: str,
    source_id: str | None,
    *,
    machine: str,
    env: str,
) -> str:
    """Resolve and validate a canonical source ID."""
    if kind == MACHINE_SSH_KIND:
        canonical = machine_ssh_id(machine, env)
        if source_id is not None and source_id != canonical:
            raise ValueError(f"machine source id must equal {canonical}")
        return canonical
    if source_id is None:
        raise ValueError(f"{kind} sources require an explicit source id")
    prefix = f"{kind}:"
    if not source_id.startswith(prefix):
        raise ValueError(f"source id must use the {kind}: namespace")
    if source_id == prefix:
        raise ValueError("source id must include a namespace value")
    return source_id


def metadata(kind: str, source_id: str, label: str) -> dict[str, str]:
    """Return the normalized source metadata embedded in each Picker row."""
    return {"kind": kind, "id": source_id, "label": label}

"""Stable identities for Worktree Picker data sources."""
from __future__ import annotations

from urllib.parse import quote

MACHINE_SSH_KIND = "machine-ssh"
PROVIDER_EXEC_KIND = "provider-exec"


def machine_ssh_id(machine: str, env: str) -> str:
    """Return the canonical identity for a machine/environment source."""
    machine_part = quote(machine.strip().casefold(), safe="-._~")
    env_part = quote(env.strip().casefold(), safe="-._~")
    return f"{MACHINE_SSH_KIND}:{machine_part}:{env_part}"


def provider_exec_id(provider: str, target_id: str) -> str:
    """Return the canonical identity for one provider-owned execution target."""
    provider_value = provider.strip()
    target_value = target_id.strip()
    if not provider_value or not target_value:
        raise ValueError("provider and target_id must be non-empty")
    provider_part = quote(provider_value.casefold(), safe="-._~")
    target_part = quote(target_value, safe="-._~")
    return f"{PROVIDER_EXEC_KIND}:{provider_part}:{target_part}"


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

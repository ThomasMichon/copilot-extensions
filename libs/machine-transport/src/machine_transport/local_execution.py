"""Current execution context, independent of optional machine customization."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .execution_identity import IdentityError
from .machine_environment import normalize_environment, split_machine_environment
from .registry import (
    MachineEntry,
    entry_physical_host,
    machine_name,
    normalize_machine_entries,
    resolve_machine_selection,
)


@dataclass(frozen=True)
class LocalExecutionContext:
    """Local naming facts, not a grant to mutate a record or another project."""

    hostname: str
    machine: str
    environment: str
    execution_machine: str
    customization_key: str | None = None


def _name(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IdentityError(f"{label} must be a nonempty string")
    cleaned = value.strip()
    if any(char.isspace() or char in "/\\#" for char in cleaned):
        raise IdentityError(f"{label} must be a single machine name")
    return cleaned


def _physical(entry: MachineEntry, observed: str) -> str:
    if entry.physical_host:
        return entry.physical_host
    if entry.execution_platform:
        return entry_physical_host(entry)
    if not entry.hostname and entry.key.casefold() == observed.casefold():
        return entry.key
    return split_machine_environment(entry.key).machine


def _platform(entry: MachineEntry, observed: str) -> str:
    if entry.execution_platform:
        return entry.execution_platform
    if not entry.hostname and entry.key.casefold() == observed.casefold():
        return ""
    return split_machine_environment(entry.key).environment


def resolve_local_execution_context(
    entries: Mapping[str, MachineEntry],
    *,
    hostname: str,
    environment: str,
    configured_machine: str | None = None,
) -> LocalExecutionContext:
    """Project already-established local OS/configuration authority.

    ``hostname`` and ``environment`` must come from the current execution
    substrate; ``configured_machine`` is the receiving installation's existing
    binding, never a requested target or a stored record's machine. File lookup,
    configuration provenance and receiving-project/ledger fences remain with the
    caller. Registry errors propagate; absence is not an error.
    """
    observed = _name(hostname, "observed hostname")
    platform = normalize_environment(environment)
    if platform not in ("windows", "wsl", "linux"):
        raise IdentityError("local execution environment must be windows, wsl or linux")
    registry = normalize_machine_entries(entries)
    if configured_machine is not None:
        if not isinstance(configured_machine, str) or not configured_machine.strip():
            raise IdentityError("configured machine must be a nonempty string")
        configured = configured_machine.strip()
        selected = resolve_machine_selection(registry, configured, platform)
        execution = selected.entry.key if selected.entry is not None else selected.target.logical_machine
        return LocalExecutionContext(
            observed, _name(selected.target.machine, "physical machine"), platform,
            _name(execution, "execution machine"),
            selected.entry.key if selected.entry is not None else None,
        )

    folded = observed.casefold()
    matches = [
        entry for entry in registry.values()
        if folded in {
            entry.key.casefold(), entry.hostname.casefold(), entry.alias.casefold(),
            entry.physical_host.casefold(),
        }
    ]
    families = {_physical(entry, observed) for entry in matches}
    folded_families = {family.casefold() for family in families}
    candidates = [
        entry for entry in registry.values()
        if _platform(entry, observed) in ("", platform)
        and _physical(entry, observed).casefold() in folded_families
    ]
    exact = [entry for entry in candidates if entry.key.casefold() == folded]
    if exact:
        candidates = exact
    if len(candidates) > 1:
        raise IdentityError(f"observed hostname {observed!r} has ambiguous {platform} entries")
    if candidates:
        entry = candidates[0]
        physical = _physical(entry, observed)
        execution = (
            entry.key if entry.execution_platform else
            f"{physical}-wsl" if platform == "wsl" else machine_name(entry)
        )
        customization_key = entry.key
    else:
        if len(folded_families) > 1:
            raise IdentityError(f"observed hostname {observed!r} has ambiguous physical associations")
        physical = min(families) if families else observed.casefold()
        execution = f"{physical}-wsl" if platform == "wsl" else physical
        customization_key = None
    collision = next(
        (entry for key, entry in registry.items() if key.casefold() == execution.casefold()), None,
    )
    if collision is not None and (
        collision.key != customization_key
        or collision.execution_platform not in ("", platform)
    ):
        raise IdentityError(f"local execution identity {execution!r} conflicts with a registered machine")
    return LocalExecutionContext(
        observed, _name(physical, "physical machine"), platform,
        _name(execution, "execution machine"), customization_key,
    )

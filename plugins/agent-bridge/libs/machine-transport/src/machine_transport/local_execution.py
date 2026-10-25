"""Current execution context, independent of optional machine customization."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from .execution_identity import IdentityError
from .machine_environment import MachineEnvironment, normalize_environment, split_machine_environment
from .registry import (
    MachineEntry,
    MissingExecutionSpaceError,
    PhysicalMachineError,
    _profile_conflicts,
    entry_physical_host,
    find_machine_entry,
    machine_identity_names,
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


def _clean_name(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IdentityError(f"{label} must be a nonempty string")
    cleaned = value.strip()
    if any(char in cleaned for char in ("\r", "\n", "\0")):
        raise IdentityError(f"{label} must be a clean string")
    return cleaned


def _name(value: str, label: str) -> str:
    cleaned = _clean_name(value, label)
    if any(char.isspace() or char in "/\\#" for char in cleaned):
        raise IdentityError(f"{label} must be a single machine name")
    return cleaned


def _fallback_execution(physical: str, observed: str, platform: str) -> str:
    base = _clean_name(physical, "physical machine")
    if any(char.isspace() or char in "/\\#" for char in base):
        base = observed.casefold()
    return MachineEnvironment(base, platform).logical_machine


def _physical(entry: MachineEntry, observed: str) -> str:
    if entry.physical_host:
        return entry.physical_host.strip()
    if entry.execution_platform:
        return entry_physical_host(entry).strip()
    if entry.key.strip().casefold() == observed.casefold() and (
        not entry.hostname or entry.hostname.strip().casefold() == observed.casefold()
    ):
        return entry.key.strip()
    return split_machine_environment(entry.key).machine


def _platform(entry: MachineEntry, observed: str) -> str:
    if entry.execution_platform:
        return entry.execution_platform
    if entry.key.strip().casefold() == observed.casefold() and (
        not entry.hostname or entry.hostname.strip().casefold() == observed.casefold()
    ):
        return ""
    return split_machine_environment(entry.key).environment


def _matches_observation(entry: MachineEntry, observed: str) -> bool:
    return observed.casefold() in {
        entry.key.strip().casefold(), entry.hostname.strip().casefold(), entry.alias.strip().casefold(),
        _physical(entry, observed).casefold(),
        *(alias.strip().casefold() for alias in entry.compatibility_aliases),
    }


def _entry_execution(entry: MachineEntry, observed: str) -> str:
    execution = entry.key.strip()
    presentation = (
        not entry.execution_platform and execution.endswith(")")
        and bool(_platform(entry, observed))
    )
    if presentation:
        execution = split_machine_environment(execution).logical_machine
    return execution


def _context(
    registry: Mapping[str, MachineEntry], observed: str, physical: str,
    platform: str, execution: str, customization_key: str | None,
) -> LocalExecutionContext:
    selected = registry.get(customization_key) if customization_key is not None else None
    physical = _clean_name(physical, "physical machine")
    execution = (
        _entry_execution(selected, observed) if selected is not None and execution == selected.key
        else execution
    )
    execution = _name(execution, "execution machine")
    for collision in registry.values():
        canonical_key = _entry_execution(collision, observed)
        names = {
            name.strip().casefold()
            for name in machine_identity_names(collision, observed_hostname=observed)
        }
        names.add(canonical_key.casefold())
        if execution.casefold() in names and (
            collision.key != customization_key
            or canonical_key.casefold() != execution.casefold()
            or collision.execution_platform not in ("", platform)
        ):
            raise IdentityError(f"local execution identity {execution!r} conflicts with a registered machine")
    return LocalExecutionContext(
        observed, physical, platform, execution, customization_key,
    )


def _literal_binding(
    registry: Mapping[str, MachineEntry], configured: str, observed: str, platform: str,
) -> bool:
    direct = [
        entry for entry in registry.values()
        if configured.casefold() in {
            name.casefold() for name in machine_identity_names(entry, include_metadata=False)
        }
    ]
    if not direct:
        if configured.casefold() == observed.casefold():
            return True
        if not any(
            entry.display_name and entry.display_name.casefold() == configured.casefold()
            for entry in registry.values()
        ):
            split_machine_environment(configured, platform)
        try:
            selected = find_machine_entry(registry, configured, reject_ambiguous=True)
        except (PhysicalMachineError, MissingExecutionSpaceError):
            return False
        if selected is not None:
            direct = [selected]
        else:
            parsed = split_machine_environment(configured)
            if parsed.machine.casefold() == observed.casefold():
                if parsed.environment and parsed.environment != platform:
                    raise IdentityError("configured machine conflicts with the current execution environment")
                return True
    return (
        len(direct) == 1 and direct[0].key.strip().casefold() == observed.casefold()
        and not direct[0].execution_platform and not _platform(direct[0], observed)
    )


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
    observed = _clean_name(hostname, "observed hostname")
    platform = normalize_environment(environment)
    if platform not in ("windows", "wsl", "linux"):
        raise IdentityError("local execution environment must be windows, wsl or linux")
    registry = normalize_machine_entries(entries, observed_hostname=observed)
    if configured_machine is not None:
        if not isinstance(configured_machine, str) or not configured_machine.strip():
            raise IdentityError("configured machine must be a nonempty string")
        configured = configured_machine.strip()
        if "\0" in configured:
            raise IdentityError("configured machine must not contain a null byte")
        keys = [key for key in registry if configured.casefold() == key.strip().casefold()]
        if len(keys) > 1:
            raise IdentityError(f"configured machine {configured!r} has ambiguous normalized keys")
        if not keys and any(
            _profile_conflicts(entry, configured, platform) for entry in registry.values()
        ):
            raise IdentityError("configured SSH profile conflicts with the current execution environment")
        # Keep the OS observation out of target-shaped suffix inference; keys,
        # aliases and locally associated display labels use the shared selector.
        local_displays = {
            entry.display_name.casefold()
            for entry in registry.values() if _matches_observation(entry, observed)
        }
        legacy_display_selector = any(char.isspace() for char in configured)
        target_registry = {
            (key.strip() if key in keys else key): replace(
                entry,
                hostname="" if entry.hostname.strip().casefold() == observed.casefold() else entry.hostname,
                display_name=entry.display_name if (
                    legacy_display_selector or entry.display_name.casefold() in local_displays
                ) else "",
            )
            for key, entry in registry.items()
        }
        if _literal_binding(target_registry, configured, observed, platform):
            configured_machine = None
    if configured_machine is not None:
        try:
            selected = resolve_machine_selection(target_registry, configured, platform)
        except MissingExecutionSpaceError as exc:
            # This binding is already local; a missing customization is not
            # enrollment authority. Explicit key/platform conflicts still raise.
            target = (
                MachineEnvironment(exc.physical_host, platform) if exc.physical_host
                else split_machine_environment(configured, platform)
            )
            execution = (
                _fallback_execution(target.machine, observed, platform) if exc.physical_host
                else target.logical_machine
            )
            return _context(registry, observed, target.machine, platform, execution, None)
        execution = selected.entry.key if selected.entry is not None else selected.target.logical_machine
        if selected.entry is None and any(
            _physical(entry, observed).casefold() == selected.target.machine.casefold()
            for entry in registry.values()
        ):
            execution = _fallback_execution(selected.target.machine, observed, platform)
        return _context(
            registry, observed, selected.target.machine, platform,
            execution,
            selected.entry.key if selected.entry is not None else None,
        )

    folded = observed.casefold()
    matches = [entry for entry in registry.values() if _matches_observation(entry, observed)]
    families = {_physical(entry, observed) for entry in matches}
    folded_families = {family.casefold() for family in families}
    candidates = [
        entry for entry in registry.values()
        if _platform(entry, observed) in ("", platform)
        and _physical(entry, observed).casefold() in folded_families
    ]
    scoped = [entry for entry in candidates if _platform(entry, observed) == platform]
    if scoped:
        candidates = scoped
    exact = [entry for entry in candidates if entry.key.strip().casefold() == folded]
    if exact:
        candidates = exact
    if len(candidates) > 1:
        raise IdentityError(f"observed hostname {observed!r} has ambiguous {platform} entries")
    if candidates:
        entry = candidates[0]
        physical = _physical(entry, observed)
        derived_guest = platform == "wsl" and _platform(entry, observed) != "wsl"
        execution = (
            _fallback_execution(physical, observed, platform) if derived_guest else entry.key
        )
        customization_key = None if derived_guest else entry.key
    else:
        if len(folded_families) > 1:
            raise IdentityError(f"observed hostname {observed!r} has ambiguous physical associations")
        physical = min(families) if families else observed.casefold()
        execution = _fallback_execution(physical, observed, platform)
        customization_key = None
    return _context(registry, observed, physical, platform, execution, customization_key)

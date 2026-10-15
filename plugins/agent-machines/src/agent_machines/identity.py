"""Resolve one canonical machine identity from portable repository topology."""

from __future__ import annotations

import platform
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable

import yaml
from machine_transport import (
    MachineEntry,
    find_machine_entry,
    local_execution_platform,
    machine_identity_names,
    normalize_machine_entries,
    parse_machines_yaml,
    qualify_guest,
    resolve_machine_selection,
)

from .manifest import ManifestError


@dataclass(frozen=True)
class MachineIdentity:
    raw: str
    canonical: str
    accepted: tuple[str, ...]
    topology_path: Path | None = None
    warnings: tuple[str, ...] = ()
    execution_platform: str = ""
    physical_host: str = ""


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    result: dict[str, str] = {}
    for value in values:
        if value:
            result.setdefault(value.casefold(), value)
    return tuple(result.values())


def detect_platform() -> str:
    return local_execution_platform()


def _topology_paths(repos: Iterable[Path]) -> list[Path]:
    paths: list[Path] = []
    seen: set[Path] = set()
    for repo in repos:
        root = Path(repo).expanduser().resolve()
        for candidate in (
            root / ".agent-worktrees" / "machines.yaml",  # marketplace-isolation: allow registry
            root / "machines.yaml",
            root / "config" / "machines.yaml",
            root / ".github" / "machines.yaml",
        ):
            if candidate in seen or not candidate.is_file():
                continue
            seen.add(candidate)
            paths.append(candidate)
    return paths


def resolve_machine(
    value: str | None = None,
    *,
    topology_repos: Iterable[Path] = (),
    execution_platform: str | None = None,
) -> MachineIdentity:
    """Resolve a logical key/profile; automatic local selection knows its platform.

    An explicitly supplied value remains platform-neutral unless the caller
    supplies execution_platform. Physical association never becomes a WSL gate alias.
    """
    selected = value if value is not None else platform.node()
    if not isinstance(selected, str):
        raise ManifestError("machine identity must be a string")
    raw = selected.strip()
    if not raw:
        raise ManifestError("machine identity is empty")
    context = execution_platform
    if value is None and context is None:
        context = detect_platform()
    entries: dict[str, MachineEntry] = {}
    entry_paths: dict[str, Path] = {}
    warnings: list[str] = []
    for path in _topology_paths(topology_repos):
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            warnings.append(f"{path}: cannot read machine topology: {exc}")
            continue
        if not isinstance(document, dict):
            warnings.append(f"{path}: machine topology root must be a mapping")
            continue
        machines = document.get("machines")
        if not isinstance(machines, dict):
            if machines is not None:
                warnings.append(f"{path}: 'machines' must be a mapping")
            continue
        try:
            parsed = normalize_machine_entries(parse_machines_yaml(document))
            for key, entry in parsed.items():
                prior_key = next((k for k in entries if k.casefold() == key.casefold()), None)
                if prior_key is not None:
                    prior = entries[prior_key]
                    if (
                        prior.execution_platform != entry.execution_platform
                        or prior.physical_host != entry.physical_host
                    ):
                        raise ValueError(f"conflicting topology declarations for machine {key!r}")
                    entries[prior_key] = replace(
                        prior, compatibility_aliases=_dedupe((
                            *prior.compatibility_aliases, *machine_identity_names(entry),
                        )),
                    )
                else:
                    entries[key] = entry
                    entry_paths[key] = path
        except ValueError as exc:
            raise ManifestError(str(exc)) from exc

    try:
        entries = normalize_machine_entries(entries)
        name = raw.casefold() if value is None else raw
        selected = resolve_machine_selection(
            entries, name, context, allow_physical_context=value is None,
        )
        entry = selected.entry
        name = selected.target.logical_machine if entry is None else entry.key
        if context == "wsl" and (entry is None or not entry.execution_platform):
            native = entry
            name = qualify_guest(native.key if native else name)
            entry = find_machine_entry(entries, name, reject_ambiguous=True)
            if entry is not None and selected.target.environment != "wsl":
                raise ValueError(
                    f"guest identity {name!r} collides with native machine {entry.key!r}; "
                    "configure a separate guest identity"
                )
            if entry is None and entries:
                warnings.append(
                    f"machine topology has no guest entry for {name!r}; using qualified identity"
                )
    except ValueError as exc:
        raise ManifestError(str(exc)) from exc
    if entry is None:
        return MachineIdentity(
            raw=raw,
            canonical=name,
            accepted=(name,),
            warnings=tuple(warnings),
        )
    accepted = machine_identity_names(entry, entries=entries)
    if not entry.execution_platform and context != "wsl":
        accepted = (*accepted, raw)
    return MachineIdentity(
        raw=raw,
        canonical=entry.key,
        accepted=_dedupe(accepted),
        topology_path=entry_paths[entry.key],
        warnings=tuple(warnings),
        execution_platform=entry.execution_platform,
        physical_host=entry.physical_host,
    )

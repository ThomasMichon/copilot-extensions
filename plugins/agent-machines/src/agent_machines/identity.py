"""Resolve one canonical machine identity from portable repository topology."""

from __future__ import annotations

import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml
from machine_transport import (
    IdentityError,
    TopologyEntry,
    detect_platform,
    resolve_identity,
)

from .manifest import ManifestError


@dataclass(frozen=True)
class MachineIdentity:
    raw: str
    canonical: str
    accepted: tuple[str, ...]
    topology_path: Path | None = None
    warnings: tuple[str, ...] = ()


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
) -> MachineIdentity:
    """Resolve an explicit target, or default to this environment's execution key."""
    selected = value if value is not None else platform.node()
    if not isinstance(selected, str):
        raise ManifestError("machine identity must be a string")
    raw = selected.strip()
    if not raw:
        raise ManifestError("machine identity is empty")
    entries: list[TopologyEntry] = []
    sources: dict[str, Path] = {}
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
        for raw_key, raw_entry in machines.items():
            key = str(raw_key).strip()
            if not key:
                warnings.append(f"{path}: machine key must be non-empty")
                continue
            if not isinstance(raw_entry, dict):
                warnings.append(f"{path}: machine {key!r} must be a mapping")
                continue
            entry = raw_entry
            entries.append(TopologyEntry(
                key,
                **{
                    field: entry[field].strip() if isinstance(entry.get(field), str) else ""
                    for field in ("hostname", "alias", "display_name")
                },
            ))
            sources.setdefault(key.casefold(), path)

    try:
        resolved = resolve_identity(
            raw.casefold() if value is None else raw,
            entries,
            guest=value is None and detect_platform() == "wsl",
        )
    except IdentityError as exc:
        raise ManifestError(str(exc)) from exc
    return MachineIdentity(
        raw=raw,
        canonical=resolved.canonical,
        accepted=resolved.accepted,
        topology_path=sources.get((resolved.topology_key or "").casefold()),
        warnings=tuple(warnings) + resolved.warnings,
    )

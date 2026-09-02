"""Resolve one canonical machine identity from portable repository topology."""

from __future__ import annotations

import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import yaml

from .manifest import ManifestError


@dataclass(frozen=True)
class MachineIdentity:
    raw: str
    canonical: str
    accepted: tuple[str, ...]
    topology_path: Path | None = None
    warnings: tuple[str, ...] = ()


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = value.strip()
        folded = cleaned.casefold()
        if cleaned and folded not in seen:
            seen.add(folded)
            out.append(cleaned)
    return tuple(out)


def _topology_paths(repos: Iterable[Path]) -> list[Path]:
    paths: list[Path] = []
    seen: set[Path] = set()
    for repo in repos:
        root = Path(repo).expanduser().resolve()
        for candidate in (
            root / ".agent-worktrees" / "machines.yaml",
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
    """Resolve ``value`` through topology key/hostname/alias/display-name fields."""
    raw = (value or platform.node()).strip()
    if not raw:
        raise ManifestError("machine identity is empty")
    folded_raw = raw.casefold()
    entries: dict[str, tuple[str, list[str], Path]] = {}
    identity_owners: dict[str, set[str]] = {}
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
            identities = [
                key,
                *(
                    str(entry.get(field)).strip()
                    for field in ("hostname", "alias", "display_name")
                    if isinstance(entry.get(field), str)
                ),
            ]
            accepted = list(_dedupe(identities))
            match_key = key.casefold()
            if match_key in entries:
                prior_key, prior_aliases, prior_path = entries[match_key]
                entries[match_key] = (
                    prior_key,
                    list(_dedupe((*prior_aliases, *accepted))),
                    prior_path,
                )
            else:
                entries[match_key] = (key, accepted, path)
            for identity in accepted:
                identity_owners.setdefault(identity.casefold(), set()).add(match_key)

    conflicts = {
        identity: owners
        for identity, owners in identity_owners.items()
        if len(owners) > 1
    }
    if conflicts:
        identity, owners = sorted(conflicts.items())[0]
        keys = ", ".join(sorted(entries[owner][0] for owner in owners))
        raise ManifestError(
            f"topology identity {identity!r} is ambiguous across machine entries: {keys}"
        )

    matches = {
        key: entry
        for key, entry in entries.items()
        if folded_raw in {item.casefold() for item in entry[1]}
    }
    if not matches:
        return MachineIdentity(
            raw=raw,
            canonical=raw,
            accepted=(raw,),
            warnings=tuple(warnings),
        )
    if len(matches) > 1:
        keys = ", ".join(sorted(match[0] for match in matches.values()))
        raise ManifestError(f"machine identity {raw!r} is ambiguous across entries: {keys}")
    canonical, accepted, path = next(iter(matches.values()))
    return MachineIdentity(
        raw=raw,
        canonical=canonical,
        accepted=_dedupe((*accepted, raw)),
        topology_path=path,
        warnings=tuple(warnings),
    )

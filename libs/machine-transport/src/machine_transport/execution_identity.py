"""Portable execution identity: ambiguity-checked topology resolution and
WSL-guest execution-key qualification.

Native Windows and Linux use the host identity. A WSL execution guest uses
``<host>-wsl``, adding the suffix only once. Topology keys are canonical;
the ``hostname`` and ``alias`` fields are identity lookup labels. A guest
entry may share the host's raw ``hostname``: that field is guest-qualified
during lookup. SSH environment aliases are transport labels and never
inputs to this resolver.
"""

from __future__ import annotations

import platform
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from .registry import MachineIdentity

__all__ = [
    "IdentityError",
    "Resolution",
    "TopologyEntry",
    "detect_platform",
    "machine_entries_to_topology",
    "qualify_guest",
    "resolve_identity",
    "resolve_machine_identity",
]


class IdentityError(ValueError):
    """Topology cannot unambiguously identify an execution machine."""


def detect_platform() -> str:
    """Classify execution roots, not a container's shared host kernel or env."""
    if platform.system() == "Windows":
        return "windows"
    if platform.system() == "Linux":
        if any(Path(marker).is_file() for marker in ("/.dockerenv", "/run/.containerenv")):
            return "linux"
        if "microsoft" in platform.release().casefold():
            return "wsl"
        try:
            with open(Path("/proc/version"), encoding="utf-8") as stream:
                if "microsoft" in stream.read().casefold():
                    return "wsl"
        except OSError:
            pass
    return "linux"


def qualify_guest(value: str) -> str:
    return value if value.casefold().endswith("-wsl") else f"{value}-wsl"


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    out: dict[str, str] = {}
    for value in values:
        cleaned = value.strip()
        if cleaned:
            out.setdefault(cleaned.casefold(), cleaned)
    return tuple(out.values())


@dataclass(frozen=True)
class TopologyEntry:
    key: str
    hostname: str = ""
    alias: str = ""
    display_name: str = ""

    @property
    def accepted(self) -> tuple[str, ...]:
        hostname = self.hostname
        if hostname and self.key.casefold().endswith("-wsl"):
            hostname = qualify_guest(hostname)
        return _dedupe((self.key, hostname, self.alias))


@dataclass(frozen=True)
class Resolution:
    canonical: str
    accepted: tuple[str, ...]
    topology_key: str | None = None
    warnings: tuple[str, ...] = ()


def resolve_identity(
    value: str,
    entries: Iterable[TopologyEntry] = (),
    *,
    guest: bool = False,
) -> Resolution:
    """Resolve a local execution identity or an unqualified explicit target."""
    if not isinstance(value, str):
        raise IdentityError("machine identity must be a string")
    raw = value.strip()
    if not raw:
        raise IdentityError("machine identity is empty")
    labels: dict[str, tuple[str, list[str]]] = {}
    owners: dict[str, set[str]] = {}
    display_owners: dict[str, set[str]] = {}
    display_labels: dict[str, str] = {}
    for entry in entries:
        folded_key = entry.key.casefold()
        if folded_key in labels:
            key, accepted = labels[folded_key]
            labels[folded_key] = (key, list(_dedupe((*accepted, *entry.accepted))))
        else:
            labels[folded_key] = (entry.key, list(entry.accepted))
        for label in entry.accepted:
            owners.setdefault(label.casefold(), set()).add(folded_key)
        display = entry.display_name.strip()
        if display:
            display_owners.setdefault(display.casefold(), set()).add(folded_key)
            display_labels.setdefault(display.casefold(), display)
    for label, keys in sorted(owners.items()):
        if len(keys) > 1:
            names = ", ".join(sorted(labels[key][0] for key in keys))
            raise IdentityError(
                f"topology identity {label!r} is ambiguous across machine entries: {names}"
            )
    # Unique display-name lookup is legacy compatibility, never identity
    # authority: metadata cannot shadow a key/hostname/alias or reject a pair.
    for label, keys in display_owners.items():
        if len(keys) == 1 and (label not in owners or owners[label] == keys):
            owner = next(iter(keys))
            labels[owner][1].append(display_labels[label])
            owners.setdefault(label, keys)

    def lookup(label: str, *, guest_only: bool = False) -> tuple[str, list[str]] | None:
        keys = owners.get(label.casefold())
        if not keys and len(display_owners.get(label.casefold(), ())) > 1:
            names = ", ".join(sorted(labels[key][0] for key in display_owners[label.casefold()]))
            raise IdentityError(
                f"display label {label!r} names multiple machines: {names}; "
                "use a machine key, hostname, or alias"
            )
        match = labels[next(iter(keys))] if keys else None
        if guest_only and match is not None and not match[0].casefold().endswith("-wsl"):
            raise IdentityError(
                f"guest identity {label!r} collides with native machine {match[0]!r}; "
                "configure a separate guest identity"
            )
        return match

    requested = qualify_guest(raw) if guest else raw
    match = lookup(requested, guest_only=guest)
    if match is not None and (not guest or match[0].casefold().endswith("-wsl")):
        key, accepted = match
        return Resolution(key, _dedupe((*accepted, requested)), key)
    if guest:
        host = lookup(raw[:-4] if raw.casefold().endswith("-wsl") else raw)
        if host is not None:
            requested = qualify_guest(host[0])
            match = lookup(requested, guest_only=True)
            if match is not None and match[0].casefold().endswith("-wsl"):
                key, accepted = match
                return Resolution(key, _dedupe(accepted), key)
        warnings = (
            (f"machine topology has no guest entry for {requested!r}; using qualified identity",)
            if labels
            else ()
        )
        return Resolution(requested, (requested,), warnings=warnings)
    warnings = (
        (f"machine topology has no entry for {raw!r}; using explicit identity",)
        if labels
        else ()
    )
    return Resolution(raw, (raw,), warnings=warnings)


def machine_entries_to_topology(entries: Mapping[str, MachineIdentity]) -> list[TopologyEntry]:
    """Project a ``machines.yaml`` registry onto the generic topology shape
    :func:`resolve_identity` operates over."""
    return [
        TopologyEntry(entry.key, entry.hostname, entry.alias, entry.display_name)
        for entry in entries.values()
    ]


def resolve_machine_identity(
    entries: Mapping[str, MachineIdentity], name: str, *, guest: bool = False,
) -> Resolution:
    """Ambiguity-checked identity resolution directly over a machine registry."""
    return resolve_identity(name, machine_entries_to_topology(entries), guest=guest)

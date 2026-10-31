"""Compatibility between logical WSL keys and physical host/environment pairs."""

from __future__ import annotations

from dataclasses import dataclass
import re

_ENVIRONMENTS = {"win": "windows", "windows": "windows", "wsl": "wsl", "linux": "linux"}
_LEGACY_ENVIRONMENT = re.compile(r"^(.*?)\s*(?:-(win|wsl)|\(\s*(windows|win|wsl)\s*\))$", re.I)


def known_environment_label(value: object) -> str | None:
    """Classify legacy metadata without validating or changing its raw value."""
    return _ENVIRONMENTS.get(value.strip().casefold()) if isinstance(value, str) else None


def normalize_environment(environment: str | None) -> str:
    label = (environment or "").strip().casefold()
    if label and label not in _ENVIRONMENTS:
        raise ValueError(f"Unknown environment: {environment}")
    return _ENVIRONMENTS.get(label, "")


def legacy_alias_forms(machine: str, environment: str) -> tuple[str, ...]:
    base = split_machine_environment(machine).machine
    selected = normalize_environment(environment)
    if selected == "wsl":
        return (f"{base}-wsl", f"{base} (WSL)")
    if selected == "windows":
        return (base, f"{base}-win", f"{base} (Windows)", f"{base} (Win)")
    return (base,)


@dataclass(frozen=True)
class MachineEnvironment:
    machine: str
    environment: str = ""

    @property
    def logical_machine(self) -> str:
        return f"{self.machine}-wsl" if self.environment == "wsl" else self.machine


def split_machine_environment(
    machine: str, environment: str | None = None,
) -> MachineEnvironment:
    """Decompose legacy labels without making native and guest execution local."""
    name = machine.strip()
    if not name:
        raise ValueError("machine identity is empty")
    selected = normalize_environment(environment)
    match = _LEGACY_ENVIRONMENT.fullmatch(name)
    if match:
        name = match.group(1).strip()
        inferred = normalize_environment(match.group(2) or match.group(3))
        if not name:
            raise ValueError("machine identity has no physical hostname")
        if selected and selected != inferred:
            raise ValueError("machine identity conflicts with the requested environment")
        selected = inferred
    return MachineEnvironment(name, selected)

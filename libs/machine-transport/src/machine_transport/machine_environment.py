"""Compatibility between logical WSL keys and physical host/environment pairs."""

from __future__ import annotations

from dataclasses import dataclass

_ENVIRONMENTS = {"win": "windows", "windows": "windows", "wsl": "wsl", "linux": "linux"}


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
    """Decompose a WSL key without making native and guest execution local."""
    name = machine.strip()
    if not name:
        raise ValueError("machine identity is empty")
    label = (environment or "").strip().casefold()
    if label and label not in _ENVIRONMENTS:
        raise ValueError(f"Unknown environment: {environment}")
    selected = _ENVIRONMENTS.get(label, "")
    if name.casefold().endswith("-wsl"):
        name = name[:-4]
        if not name:
            raise ValueError("WSL machine identity has no physical hostname")
        if selected and selected != "wsl":
            raise ValueError("WSL machine identity conflicts with the requested environment")
        selected = "wsl"
    return MachineEnvironment(name, selected)

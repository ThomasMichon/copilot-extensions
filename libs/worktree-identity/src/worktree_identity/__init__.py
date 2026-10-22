"""Pure tuple-first identity codec; metadata alone grants no authority."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

MACHINE_IDENTITY_VERSION = 1


class WorktreeMachineIdentityError(ValueError):
    """Explicit tuple metadata is unsupported, incomplete or internally inconsistent."""


def _name(value: object, field: str) -> str:
    if (
        not isinstance(value, str) or not value or value != value.strip()
        or any(character in value for character in ("\r", "\n", "\0"))
    ):
        raise WorktreeMachineIdentityError(
            f"machine identity {field} must be a nonempty clean string",
        )
    return value


def _environment(value: object, field: str) -> str:
    name = _name(value, field)
    label = name.casefold()
    if label not in {"win", "windows", "wsl", "linux"}:
        error = ValueError(f"Unknown environment: {name}")
        raise WorktreeMachineIdentityError(f"machine identity {field} is unsupported") from error
    normalized = "windows" if label == "win" else label
    if name != normalized:
        raise WorktreeMachineIdentityError(
            f"machine identity {field} must use a canonical environment",
        )
    return normalized


@dataclass(frozen=True)
class WorktreeMachineIdentity:
    """Physical tuple and one-term execution owner, not proof of local authority."""

    machine: str
    environment: str
    execution_machine: str

    def __post_init__(self) -> None:
        _name(self.machine, "machine")
        _environment(self.environment, "environment")
        _name(self.execution_machine, "execution_machine")

    def to_record_fields(self) -> dict[str, object]:
        return {
            "machine_identity_version": MACHINE_IDENTITY_VERSION,
            "machine": self.machine,
            "environment": self.environment,
            "execution_machine": self.execution_machine,
            "platform": self.environment,
        }


def parse_machine_identity(data: Mapping[str, object]) -> WorktreeMachineIdentity | None:
    """Decode only explicitly versioned tuples; leave historical metadata untouched."""
    if "machine_identity_version" not in data:
        return None
    version = data["machine_identity_version"]
    if type(version) is not int or version != MACHINE_IDENTITY_VERSION:
        raise WorktreeMachineIdentityError("unsupported machine identity version")
    identity = WorktreeMachineIdentity(
        machine=_name(data.get("machine"), "machine"),
        environment=_environment(data.get("environment"), "environment"),
        execution_machine=_name(data.get("execution_machine"), "execution_machine"),
    )
    if _environment(data.get("platform"), "platform") != identity.environment:
        raise WorktreeMachineIdentityError("machine identity environment conflicts with platform")
    return identity


def execution_machine_from_fields(data: Mapping[str, object]) -> str | None:
    """Read an execution slot without treating malformed tuple metadata as legacy."""
    identity = parse_machine_identity(data)
    if identity is not None:
        return identity.execution_machine
    machine = data.get("machine")
    return machine if isinstance(machine, str) else None


def projected_machine_fields(data: Mapping[str, object]) -> dict[str, object]:
    identity = parse_machine_identity(data)
    return identity.to_record_fields() if identity is not None else {
        "machine": data.get("machine"), "platform": data.get("platform"),
    }

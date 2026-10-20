"""Pure tuple-first identity codec, not a tracking-format or authority opt-in.

Tracking loaders and creation producers remain unchanged until their consumers
and retained-allocation paths support the tuple representation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from machine_transport import normalize_environment

MACHINE_IDENTITY_VERSION = 1


class WorktreeMachineIdentityError(ValueError):
    """Explicit tuple metadata is unsupported, incomplete or internally inconsistent."""


def _name(value: object, field: str) -> str:
    if (
        not isinstance(value, str) or not value or value != value.strip()
        or any(character in value for character in ("\r", "\n", "\0"))
    ):
        raise WorktreeMachineIdentityError(f"machine identity {field} must be a nonempty clean string")
    return value


def _environment(value: object, field: str) -> str:
    name = _name(value, field)
    try:
        normalized = normalize_environment(name)
    except ValueError as error:
        raise WorktreeMachineIdentityError(f"machine identity {field} is unsupported") from error
    if name != normalized:
        raise WorktreeMachineIdentityError(f"machine identity {field} must use a canonical environment")
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

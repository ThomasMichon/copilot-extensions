"""Adopter-owned effect revocation for an acknowledged execution handoff.

This boundary does not close Dispatch execution admission or transfer tasks.
Its acknowledgement is one prerequisite for the coordinator's durable handoff.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

EffectState = Literal["pending", "blocked", "acknowledged", "unsupported"]
ExecutionMode = Literal["automatic", "manual"]
PROTOCOL_VERSION = 1


class EffectAdapterError(ValueError):
    """An adopter response cannot establish the requested revocation."""


def _token(value: str, name: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 512
        or value != value.strip()
        or any(ord(char) < 33 or ord(char) > 126 for char in value)
    ):
        raise EffectAdapterError(f"{name} must be a nonempty bounded ASCII token")


@dataclass(frozen=True)
class ExecutionScope:
    """Owner-resolved scope, not authority derived from a caller's label."""

    installation: str
    coordinator: str
    repository: str
    producer_registration: str

    def __post_init__(self) -> None:
        for name in ("installation", "coordinator", "repository", "producer_registration"):
            _token(getattr(self, name), name)


@dataclass(frozen=True)
class EffectRevocation:
    """One durable operation retiring effect admission for an old generation."""

    scope: ExecutionScope
    operation_id: str
    generation: int
    retiring_mode: ExecutionMode
    destination_mode: ExecutionMode

    def __post_init__(self) -> None:
        if not isinstance(self.scope, ExecutionScope):
            raise EffectAdapterError("scope must be owner-resolved")
        _token(self.operation_id, "operation_id")
        if type(self.generation) is not int or self.generation < 1:
            raise EffectAdapterError("generation must be a positive integer")
        modes = {"automatic", "manual"}
        if self.retiring_mode not in modes or self.destination_mode not in modes:
            raise EffectAdapterError("execution mode must be automatic or manual")
        if self.retiring_mode == self.destination_mode:
            raise EffectAdapterError("handoff must change execution mode")


@dataclass(frozen=True)
class EffectObservation:
    """Current adopter result, never a copied store of domain effect outcomes.

    The adopter serializes revocation against final mutation admission. It must
    retain pending or ambiguous accepted effects until their authoritative
    outcomes are reconciled. Identifiers below are diagnostic references only.
    """

    request: EffectRevocation
    state: EffectState
    admission_closed: bool | None
    enumeration_complete: bool
    unresolved_effects: tuple[str, ...] = ()
    legacy_writers: tuple[str, ...] = ()
    detail: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.request, EffectRevocation):
            raise EffectAdapterError("observation must identify its revocation request")
        if self.state not in {"pending", "blocked", "acknowledged", "unsupported"}:
            raise EffectAdapterError("unknown effect observation state")
        if self.admission_closed is not None and type(self.admission_closed) is not bool:
            raise EffectAdapterError("admission_closed must be boolean or unknown")
        if type(self.enumeration_complete) is not bool:
            raise EffectAdapterError("enumeration_complete must be boolean")
        for name in ("unresolved_effects", "legacy_writers"):
            values = getattr(self, name)
            if not isinstance(values, tuple):
                raise EffectAdapterError(f"{name} must be an immutable tuple")
            for value in values:
                _token(value, name)
            if len(set(values)) != len(values):
                raise EffectAdapterError(f"{name} contains duplicate references")
        if not isinstance(self.detail, str):
            raise EffectAdapterError("detail must be text")
        if self.state == "acknowledged" and (
            self.admission_closed is not True
            or not self.enumeration_complete
            or self.unresolved_effects
            or self.legacy_writers
        ):
            raise EffectAdapterError("acknowledgement requires a complete, settled effect barrier")
        if self.state != "acknowledged" and not self.detail.strip():
            raise EffectAdapterError("an unacknowledged result must explain its blocker")


class EffectAdapter(Protocol):
    """Optional adopter boundary; neither a scheduler nor an ownership grant."""

    async def protocol_version(self, scope: ExecutionScope) -> int:
        """Return supported semantics for this qualified scope; zero means unsupported."""
        ...

    async def revoke(self, request: EffectRevocation) -> EffectObservation:
        """Idempotently close old-generation effects and observe the same operation.

        Repeating a durable request must not increment a generation or replay an
        effect. The adopter must reject conflicting operations rather than
        replacing an in-progress revocation. A lost reply is re-observed here.
        """
        ...


async def observe_effect_revocation(
    adapter: EffectAdapter | None,
    request: EffectRevocation,
) -> EffectObservation:
    """Negotiate and validate one result without granting destination admission.

    Transport failures and timeouts propagate to the owning transition, which
    must remain unacknowledged. They are never converted into an empty drain.
    """
    if adapter is None:
        return EffectObservation(
            request,
            "unsupported",
            None,
            False,
            detail="no adopter effect adapter",
        )
    version = await adapter.protocol_version(request.scope)
    if type(version) is not int or version < 0:
        raise EffectAdapterError("adopter returned an invalid protocol version")
    if version != PROTOCOL_VERSION:
        return EffectObservation(
            request,
            "unsupported",
            None,
            False,
            detail=f"unsupported adopter effect protocol: {version}",
        )
    observation = await adapter.revoke(request)
    if not isinstance(observation, EffectObservation):
        raise EffectAdapterError("adopter returned no structured effect observation")
    if observation.request != request:
        raise EffectAdapterError(
            "adopter response belongs to another scope, operation or generation"
        )
    return observation

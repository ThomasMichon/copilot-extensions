"""Distinct checkpoint decisions within one original repair invocation."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from .enrolled_identity import EnrolledIdentity
from .machine_identity import MachineIdentity
from .operations import OperationIntent
from .runtime_admission import AdmissionError, CallerIdentity, Permit
from .runtime_identity import VerifiedIdentity

if TYPE_CHECKING:
    from .komodo_repair import KomodoRepairOwner, _Resolution

RepairIdentityProducer = Callable[
    [CallerIdentity, OperationIntent, str, str, str], CallerIdentity
]


def _identity_binding(identity: CallerIdentity) -> tuple[object, ...]:
    if type(identity) not in (VerifiedIdentity, EnrolledIdentity, MachineIdentity):
        raise AdmissionError("invalid-identity")
    return (
        type(identity), identity.profile, identity.issuer, identity.subject,
        identity.audience, identity.proof_key,
        identity.kind if isinstance(identity, (EnrolledIdentity, MachineIdentity)) else None,
        identity.resource if isinstance(identity, MachineIdentity) else None,
        identity.binding if isinstance(identity, MachineIdentity) else None,
    )


def _permit_binding(permit: Permit) -> tuple[object, ...]:
    return (
        permit.authority, permit.fleet, permit.enrollment.binding, permit.action,
        permit.request_digest, permit.resources,
    )


class RepairAuthorization:
    """Retain exact local handles; never renew a failed admitted phase."""

    def __init__(self, owner: KomodoRepairOwner, resolution: _Resolution,
                 identity: CallerIdentity, permit: Permit, action: str) -> None:
        self.owner, self.resolution = owner, resolution
        self.identity, self.permit, self.action = identity, permit, action
        self.identity_binding = _identity_binding(identity)
        self.permit_binding = _permit_binding(permit)
        self.enabled = owner.fresh_identity is not None
        self.active = self.enabled
        self.failed = self.completed = False

    def begin(self) -> None:
        if self.failed or self.completed or self.active:
            raise AdmissionError("repair-phase-unavailable")
        try:
            self._acquire()
        except BaseException:
            self.abort()
            raise

    def _acquire(self) -> None:
        producer = self.owner.fresh_identity
        if producer is None:
            raise AdmissionError("repair-checkpoint-auth-unavailable")
        self.owner.admission.discard(self.permit)
        identity = producer(
            self.identity, self.resolution.request,
            self.resolution.role, self.resolution.installation, self.action)
        if _identity_binding(identity) != self.identity_binding:
            raise AdmissionError("repair-identity-changed")
        self.resolution.identity = identity
        self.permit = self.owner.admission.authorize(
            identity, self.resolution.request,
            role=self.resolution.role, installation=self.resolution.installation,
            checkpoint=self.resolution.refresh)
        if _permit_binding(self.permit) != self.permit_binding:
            self.abort()
            raise AdmissionError("repair-enrollment-changed")
        self.active = True

    def end(self) -> None:
        self.active = False

    def revalidate(self) -> None:
        try:
            self.permit = self.owner.admission.revalidate(
                self.permit, self.resolution.request)
        except BaseException:
            self.abort()
            raise

    def complete(self) -> None:
        try:
            self.owner.admission.complete(self.permit, self.resolution.request)
        except BaseException:
            self.abort()
            raise
        self.completed = True
        self.active = False

    def abort(self) -> None:
        self.failed = True
        self.owner.admission.discard(self.permit)

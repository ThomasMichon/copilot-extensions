"""Finite reverse-listener coordination through one explicitly trusted owner.

No production SSH adapter is supplied. The owner must compose the existing
finite-forward leaf, authenticate both forwarding directions, enforce deadlines,
and atomically fence its existing effect journal. Parsed values are not authority.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import time
from typing import Literal, Protocol

from .planning import PlanError, digest, identifier, integer
from .release import SHA256

MAX_CANDIDATES = 16
OPERATION_SECONDS = 30.0
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
RejectionState = Literal["refused", "unavailable", "conflict"]
RejectionReason = Literal[
    "policy-refused", "stale-generation", "unsupported", "candidates-exhausted",
    "readiness-refused", "request-conflict",
]


@dataclass(frozen=True)
class Listener:
    host: str
    port: int

    def __post_init__(self):
        if not isinstance(self.host, str) or self.host not in LOOPBACK_HOSTS:
            raise PlanError("a literal loopback listener is required")
        integer(self.port, "reverse listener port", minimum=1)
        if self.port > 65535:
            raise PlanError("invalid reverse listener port")


@dataclass(frozen=True)
class ReservationRequest:
    """Immutable desired effect; the selected owner independently admits it."""

    correlation: str
    participant: str
    session: str
    owner_epoch: str
    policy_digest: str
    target_identity: str
    expected_generation: int
    permitted: tuple[Listener, ...]

    def __post_init__(self):
        for name in ("correlation", "participant", "session", "owner_epoch"):
            identifier(getattr(self, name), "reservation " + name)
        for value in (self.policy_digest, self.target_identity):
            if not isinstance(value, str) or not SHA256.fullmatch(value):
                raise PlanError("reservation digests must be lowercase SHA-256")
        integer(self.expected_generation, "reservation generation")
        if (type(self.permitted) is not tuple
                or not 1 <= len(self.permitted) <= MAX_CANDIDATES
                or any(not isinstance(item, Listener) for item in self.permitted)
                or len(set(self.permitted)) != len(self.permitted)):
            raise PlanError("a bounded unique immutable listener grant is required")

    @property
    def request_digest(self) -> str:
        return digest({
            "correlation": self.correlation, "participant": self.participant,
            "session": self.session, "owner_epoch": self.owner_epoch,
            "policy_digest": self.policy_digest, "target_identity": self.target_identity,
            "expected_generation": self.expected_generation,
            "permitted": [{"host": item.host, "port": item.port} for item in self.permitted],
        })


@dataclass(frozen=True)
class Admission:
    """Owner-held journal claim, not a transferable or serialized permission."""

    request: ReservationRequest
    valid_until: int
    custody: bytes = field(repr=False)

    def __post_init__(self):
        if not isinstance(self.request, ReservationRequest):
            raise PlanError("typed reservation request required")
        integer(self.valid_until, "reservation admission expiry")
        if type(self.custody) is not bytes or not 1 <= len(self.custody) <= 16384:
            raise PlanError("bounded reservation custody required")


@dataclass(frozen=True)
class Candidate:
    admission: Admission
    listener: Listener
    resource: str

    def __post_init__(self):
        if (not isinstance(self.admission, Admission)
                or not isinstance(self.listener, Listener)
                or self.listener not in self.admission.request.permitted):
            raise PlanError("candidate differs from the admitted finite grant")
        identifier(self.resource, "reservation resource")


@dataclass(frozen=True)
class Publication:
    """An owner-confirmed journal commit, not a claim of continuing liveness."""

    candidate: Candidate
    generation: int

    def __post_init__(self):
        if not isinstance(self.candidate, Candidate):
            raise PlanError("typed reservation candidate required")
        integer(self.generation, "published reservation generation", minimum=1)
        if self.generation <= self.candidate.admission.request.expected_generation:
            raise PlanError("publication did not advance the expected generation")


@dataclass(frozen=True)
class Rejected:
    request: ReservationRequest
    state: RejectionState
    reason: RejectionReason

    def __post_init__(self):
        if (not isinstance(self.request, ReservationRequest)
                or not isinstance(self.state, str)
                or self.state not in {"refused", "unavailable", "conflict"}
                or not isinstance(self.reason, str) or self.reason not in {
                    "policy-refused", "stale-generation", "unsupported",
                    "candidates-exhausted", "readiness-refused", "request-conflict",
                }):
            raise PlanError("invalid confirmed no-publication result")


@dataclass(frozen=True)
class Pending:
    request: ReservationRequest

    def __post_init__(self):
        if not isinstance(self.request, ReservationRequest):
            raise PlanError("typed pending reservation request required")


class ReservationOwner(Protocol):
    """Coordinator-facing seam, not an assertion about an existing SSH API.

    Every method enforces the remaining timeout at its effect boundary.
    Admission authenticates current participant/session/target/policy, both
    permitlisten and peer permitopen, the exact finite grant and owner epoch.
    It atomically deduplicates correlation AND full request bytes in the existing
    journal. A previously admitted/unresolved request returns Pending, never a
    fresh admission. Completed requests return their retained terminal receipt.
    Rejection is terminal only after any unpublished candidate is confirmed
    discarded; interrupted rejection/cleanup remains Pending.
    """

    async def admit(self, request: ReservationRequest, *, timeout: float
                    ) -> Admission | Publication | Rejected | Pending:
        ...

    async def reserve(self, admission: Admission, *, timeout: float
                      ) -> Candidate | Rejected | Pending:
        """Consume admission once through the accepted finite-forward leaf.

        Re-admit current policy/identity/epoch before dispatch, then mark the
        original correlation pending. Retain the actual successful resource;
        no probe-close/rebind. Only the leaf classifies a
        real bind conflict or exhausts the existing finite grant. Rejected
        confirms no owned resource or publication remains. An uncertain effect
        stays journaled; cancellation and a fresh caller cannot repeat it.
        """
        ...

    async def publish(self, candidate: Candidate, *, timeout: float
                      ) -> Publication | Rejected | Pending:
        """Verify authenticated candidate-specific readiness, then atomic CAS.

        Re-admit current policy/identity/epoch and compare the exact predecessor
        generation in the existing authoritative journal before publication.
        Rejected is an irrevocable no-publication verdict for this admission.
        Pending retains custody; it never permits speculative disposal.
        """
        ...

    async def discard(self, candidate: Candidate, *, timeout: float
                      ) -> Literal["discarded", "unknown"]:
        """Close only this confirmed unpublished resource under exact custody.

        Recheck admission/epoch/correlation/resource and rejected journal state
        atomically; never close a possibly published candidate or a successor.
        Retirement of published predecessors is outside this coordinator.
        """
        ...


@dataclass(frozen=True)
class ReservationResult:
    correlation: str
    state: Literal["published", "refused", "unavailable", "conflict", "unknown"]
    reason: str
    publication: Publication | None = None

    @property
    def reobserve_required(self) -> bool:
        return self.state == "unknown"

    def to_dict(self) -> dict:
        published = self.publication
        return {
            "schema": "fleet-manager.reverse-listener-result", "version": 1,
            "correlation": self.correlation, "state": self.state, "reason": self.reason,
            "reobserve_required": self.reobserve_required,
            "publication": None if published is None else {
                "generation": published.generation,
                "listener": {
                    "host": published.candidate.listener.host,
                    "port": published.candidate.listener.port,
                },
            },
        }


async def reserve_reverse_listener(request: ReservationRequest, *, owner: ReservationOwner
                                   ) -> ReservationResult:
    """One bounded attempt; the supplied owner retains all resource/journal state.

    No automatic retry, supervisor, persistence, transport selection or cleanup
    of unknown effects. Reobservation belongs to the same owner's journal under
    this exact correlation. Unexpected programming errors propagate; the owner
    must journal dispatch before an effect, even if its caller is interrupted.
    """
    if not isinstance(request, ReservationRequest):
        raise PlanError("typed reservation request required")
    deadline = time.monotonic() + OPERATION_SECONDS

    def remaining() -> float:
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError
        return min(OPERATION_SECONDS, left)

    def unknown(reason: str) -> ReservationResult:
        return ReservationResult(request.correlation, "unknown", reason)

    def rejected(value: Rejected) -> ReservationResult:
        if value.request != request:
            return unknown("owner-receipt-mismatch")
        return ReservationResult(request.correlation, value.state, value.reason)

    def publication(value: Publication, *, candidate: Candidate | None = None
                    ) -> ReservationResult:
        if (value.candidate.admission.request != request
                or (candidate is not None and value.candidate != candidate)):
            return unknown("owner-receipt-mismatch")
        return ReservationResult(request.correlation, "published", "owner-committed", value)

    stage = "admission"
    try:
        admitted = await owner.admit(request, timeout=remaining())
        remaining()
        if isinstance(admitted, Publication):
            return publication(admitted)
        if isinstance(admitted, Rejected):
            return rejected(admitted)
        if isinstance(admitted, Pending):
            return unknown("owner-reobservation-required")
        if not isinstance(admitted, Admission) or admitted.request != request:
            return unknown("owner-admission-invalid")
        if time.time() >= admitted.valid_until:
            return unknown("owner-admission-expired")
        stage = "reservation"
        bound = await owner.reserve(admitted, timeout=remaining())
        remaining()
        if isinstance(bound, Rejected):
            return rejected(bound)
        if isinstance(bound, Pending):
            return unknown("owner-reobservation-required")
        if not isinstance(bound, Candidate) or bound.admission != admitted:
            return unknown("owner-candidate-invalid")
        if time.time() >= admitted.valid_until:
            return unknown("owner-admission-expired")
        stage = "publication"
        committed = await owner.publish(bound, timeout=remaining())
        remaining()
        if isinstance(committed, Publication):
            return publication(committed, candidate=bound)
        if isinstance(committed, Rejected):
            if committed.request != request:
                return unknown("owner-receipt-mismatch")
            stage = "discard"
            discarded = await owner.discard(bound, timeout=remaining())
            remaining()
            if discarded == "discarded":
                return rejected(committed)
            return unknown("owner-discard-unconfirmed")
        if isinstance(committed, Pending):
            return unknown("owner-reobservation-required")
        return unknown("owner-publication-invalid")
    except (asyncio.TimeoutError, TimeoutError):
        return unknown(stage + "-timeout")
    except OSError:
        return unknown(stage + "-outcome-unknown")

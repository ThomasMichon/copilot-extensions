"""Explicit fenced point reads, not a serving adapter or wire authorization."""

from dataclasses import dataclass
from .observation_store import ObservationStore
from .runtime_admission import (
    AdmissionAuthority, AdmissionError, AdmissionFence, FENCE_WAIT_SECONDS,
    ObservationRead, ObservationScope,
)
from .runtime_identity import VerifiedIdentity

StateValue = str | bool | None


@dataclass(frozen=True)
class ScopedObservation:
    scope: ObservationScope
    generation: int | None
    source_revision: int | None
    observed_at: int | float | None
    availability: str
    reason: str | None
    coverage: str
    state: tuple[tuple[str, StateValue], ...] | None


class ScopedObservationReader:
    """A trusted owner supplies the authority, store and shared policy fence.

    Every participating policy/enrollment/revocation writer must use that fence.
    The result describes one local read at completion, not later wire emission.
    """

    def __init__(self, *, authority: AdmissionAuthority, store: ObservationStore,
                 fence: AdmissionFence):
        if (not isinstance(authority, AdmissionAuthority)
                or not isinstance(store, ObservationStore)
                or authority.fence is not fence
                or not callable(getattr(fence, "acquire", None))
                or not callable(getattr(fence, "release", None))):
            raise AdmissionError("invalid-record")
        self._authority = authority
        self._store = store
        self._fence = fence

    def read(self, identity: VerifiedIdentity, request: ObservationRead, *,
             role: str, installation: str) -> ScopedObservation:
        if (type(request) is not ObservationRead or request.scopes is None
                or len(request.scopes) != 1):
            raise AdmissionError("unsupported-read")
        if not self._fence.acquire(timeout=FENCE_WAIT_SECONDS):
            raise AdmissionError("authority-busy")
        try:
            permit = self._authority.authorize(
                identity, request, role=role, installation=installation,
            )
            try:
                if permit.authority != self._store.authority:
                    raise AdmissionError("authority-mismatch")
                scope = request.scopes[0]
                found = self._store.read(
                    origin=scope.origin, project=scope.project, entity=scope.entity,
                )
                row = found["row"]
                if row is None:
                    return ScopedObservation(
                        scope, None, None, None, "unknown", "unobserved",
                        "point-observations-only", None,
                    )
                update = row["update"]
                state = update["state"]
                return ScopedObservation(
                    scope, update["generation"], update["source_revision"],
                    update["observed_at"], update["availability"], update["reason"],
                    row["coverage"],
                    None if state is None else tuple(
                        (field, state[field]) for field in scope.fields if field in state
                    ),
                )
            finally:
                self._authority.complete(permit, request)
        finally:
            self._fence.release()

"""Explicit fenced current reads and recovery, not serving or wire authority."""

from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import hmac
import re
import secrets
from typing import Literal

from .observation_store import MAX_POINT_READS, ObservationStore
from .planning import canonical
from .runtime_admission import (
    AdmissionAuthority, AdmissionError, AdmissionFence, FENCE_WAIT_SECONDS,
    ObservationRead, ObservationScope, Permit,
)
from .runtime_identity import VerifiedIdentity

StateValue = str | bool | None
_VALIDATOR = re.compile(r"[0-9a-f]{64}\Z")


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


@dataclass(frozen=True)
class ScopedObservationRefresh:
    validator: str
    status: Literal["snapshot", "unchanged"]
    observations: tuple[ScopedObservation, ...]


def _points(request: ObservationRead) -> tuple[tuple[str, str, str], ...]:
    if (type(request) is not ObservationRead or request.scopes is None
            or not 1 <= len(request.scopes) <= MAX_POINT_READS):
        raise AdmissionError("unsupported-read")
    points = tuple((scope.origin, scope.project, scope.entity) for scope in request.scopes)
    if len(set(points)) != len(points):
        raise AdmissionError("unsupported-read")
    return points


def _validator(key: bytes, identity: VerifiedIdentity, permit: Permit,
               observations: tuple[ScopedObservation, ...]) -> str:
    value = hmac.new(key, digestmod=hashlib.sha256)
    binding = {
        "schema": "fleet-manager.scoped-refresh", "version": 1,
        "authority": permit.authority, "fleet": permit.fleet,
        "issuer": identity.issuer, "audience": identity.audience,
        "subject": identity.subject, "enrollment": list(permit.enrollment.binding),
        "request": permit.request_digest,
    }

    def append(record):
        raw = canonical(record)
        value.update(len(raw).to_bytes(4, "big"))
        value.update(raw)

    append(binding)
    # Frame each bounded point separately, not one aggregate-size-limited record.
    for point in observations:
        record = asdict(point)
        record["scope"]["fields"] = list(point.scope.fields)
        record["state"] = None if point.state is None else [list(pair) for pair in point.state]
        append(record)
    return value.hexdigest()


def _project(scope: ObservationScope, found) -> ScopedObservation:
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
        self._refresh_key = secrets.token_bytes(32)

    def read(self, identity: VerifiedIdentity, request: ObservationRead, *,
             role: str, installation: str) -> ScopedObservation:
        if (type(request) is not ObservationRead or request.scopes is None
                or len(request.scopes) != 1):
            raise AdmissionError("unsupported-read")
        with self._admitted(identity, request, role=role, installation=installation):
            scope = request.scopes[0]
            return _project(scope, self._store.read(
                origin=scope.origin, project=scope.project, entity=scope.entity,
            ))

    def snapshot(self, identity: VerifiedIdentity, request: ObservationRead, *,
                 role: str, installation: str) -> tuple[ScopedObservation, ...]:
        points = _points(request)
        with self._admitted(identity, request, role=role, installation=installation):
            return self._snapshot(request, points)

    def refresh(self, identity: VerifiedIdentity, request: ObservationRead, *,
                role: str, installation: str,
                validator: str | None = None) -> ScopedObservationRefresh:
        points = _points(request)
        if validator is not None and (
            type(validator) is not str or _VALIDATOR.fullmatch(validator) is None
        ):
            raise AdmissionError("unsupported-read")
        with self._admitted(identity, request, role=role, installation=installation) as permit:
            observations = self._snapshot(request, points)
            current = _validator(self._refresh_key, identity, permit, observations)
            unchanged = validator is not None and hmac.compare_digest(validator, current)
            return ScopedObservationRefresh(
                current, "unchanged" if unchanged else "snapshot",
                () if unchanged else observations,
            )

    def _snapshot(self, request, points):
        return tuple(
            _project(scope, found) for scope, found in zip(
                request.scopes, self._store.read_points(points), strict=True,
            )
        )

    @contextmanager
    def _admitted(self, identity, request, *, role, installation):
        if not self._fence.acquire(timeout=FENCE_WAIT_SECONDS):
            raise AdmissionError("authority-busy")
        try:
            permit = self._authority.authorize(
                identity, request, role=role, installation=installation,
            )
            try:
                if permit.authority != self._store.authority:
                    raise AdmissionError("authority-mismatch")
                yield permit
            finally:
                self._authority.complete(permit, request)
        finally:
            self._fence.release()

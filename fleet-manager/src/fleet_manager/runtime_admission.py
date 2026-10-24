"""Single-owner current-policy admission, not policy provenance or delegation."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, replace
import re
import threading
from typing import Protocol

from .observations import STATE_FIELDS
from .operations import COMMAND_FIELDS, OperationIntent
from .planning import PlanError, digest, identifier
from .runtime_identity import VerifiedIdentity

MAX_ENROLLMENTS = 64
MAX_GRANTS = 256
MAX_PERMITS = 4096
PERMIT_SECONDS = 30
REQUEST_SECONDS = 90
FENCE_WAIT_SECONDS = 1


class AdmissionFence(Protocol):
    def acquire(self, *, timeout: float) -> bool: ...
    def release(self) -> None: ...


class AdmissionError(ValueError):
    """Value-free refusal; no supplied identity, resource or policy is disclosed."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _name(value):
    try:
        identifier(value, "admission identifier")
    except PlanError:
        raise AdmissionError("invalid-record") from None


def _text(value, maximum):
    if (type(value) is not str or not 0 < len(value) <= maximum
            or any(not 32 < ord(char) < 127 for char in value)):
        raise AdmissionError("invalid-record")


def _time(value):
    if type(value) is not int or not 0 <= value < 2**53:
        raise AdmissionError("invalid-record")


def _generation(value):
    _time(value)
    if value == 0:
        raise AdmissionError("invalid-record")


def _items(value, item_type, maximum, *, empty=True):
    if (type(value) is not tuple or len(value) > maximum
            or (not empty and not value)
            or any(type(item) is not item_type for item in value)
            or len(set(value)) != len(value)):
        raise AdmissionError("invalid-record")


@dataclass(frozen=True)
class ObservationScope:
    """One correlated branch, never independent cross-product filter sets."""

    origin: str
    project: str
    entity: str
    fields: tuple[str, ...]

    def __post_init__(self):
        for value in (self.origin, self.project, self.entity):
            _name(value)
        _items(self.fields, str, len(STATE_FIELDS), empty=False)
        if any(field not in STATE_FIELDS for field in self.fields):
            raise AdmissionError("unsupported-scope")
        object.__setattr__(self, "fields", tuple(sorted(self.fields)))

    def includes(self, requested: ObservationScope) -> bool:
        return (
            (self.origin, self.project, self.entity)
            == (requested.origin, requested.project, requested.entity)
            and all(field in self.fields for field in requested.fields)
        )


def _scope_key(scope: ObservationScope):
    return scope.origin, scope.project, scope.entity, scope.fields


@dataclass(frozen=True)
class OperationGrant:
    command: str
    target: str
    generation: int
    request_digest: str | None = None

    def __post_init__(self):
        _name(self.command)
        _name(self.target)
        _generation(self.generation)
        if self.command not in COMMAND_FIELDS:
            raise AdmissionError("unsupported-action")
        if self.command == "runner.converge":
            if (type(self.request_digest) is not str
                    or re.fullmatch(r"[0-9a-f]{64}", self.request_digest) is None):
                raise AdmissionError("invalid-record")
        elif self.request_digest is not None:
            raise AdmissionError("unsupported-scope")


@dataclass(frozen=True)
class Enrollment:
    subject: str
    principal: str
    account: str
    kind: str
    role: str
    installation: str
    generation: int
    status: str
    reads: tuple[ObservationScope, ...] = ()
    operations: tuple[OperationGrant, ...] = ()

    def __post_init__(self):
        _text(self.subject, 256)
        for value in (self.principal, self.account, self.role, self.installation):
            _name(value)
        _generation(self.generation)
        if (self.kind not in ("operator", "automation", "connector", "service", "provider")
                or self.status not in ("active", "revoked")):
            raise AdmissionError("invalid-record")
        _items(self.reads, ObservationScope, MAX_GRANTS)
        _items(self.operations, OperationGrant, MAX_GRANTS)
        object.__setattr__(self, "reads", tuple(sorted(self.reads, key=_scope_key)))
        object.__setattr__(self, "operations", tuple(sorted(
            self.operations, key=lambda item: (
                item.command, item.target, item.generation, item.request_digest or "",
            ),
        )))

    @property
    def binding(self):
        return (
            self.subject, self.principal, self.account, self.kind, self.role,
            self.installation, self.generation,
        )


@dataclass(frozen=True)
class AuthoritySnapshot:
    """Trusted owner assertion. Constructing this record does not authenticate it."""

    authority: str
    fleet: str
    issuer: str
    audience: str
    revision: int
    valid_from: int
    valid_until: int
    identity_trust_until: int
    enrollments: tuple[Enrollment, ...]

    def __post_init__(self):
        _name(self.authority)
        _name(self.fleet)
        _text(self.issuer, 2048)
        _text(self.audience, 512)
        _generation(self.revision)
        for value in (self.valid_from, self.valid_until, self.identity_trust_until):
            _time(value)
        if not self.valid_from < self.valid_until:
            raise AdmissionError("invalid-record")
        _items(self.enrollments, Enrollment, MAX_ENROLLMENTS)
        keys, actors = set(), {}
        grants = 0
        for item in self.enrollments:
            key = (item.subject, item.role, item.installation)
            actor = (item.principal, item.account)
            if key in keys or (item.subject in actors and actors[item.subject] != actor):
                raise AdmissionError("invalid-record")
            keys.add(key)
            actors[item.subject] = actor
            grants += len(item.reads) + len(item.operations)
        if grants > MAX_GRANTS:
            raise AdmissionError("invalid-record")
        object.__setattr__(self, "enrollments", tuple(sorted(
            self.enrollments, key=lambda item: (item.subject, item.role, item.installation),
        )))


@dataclass(frozen=True)
class ObservationRead:
    fleet: str
    scopes: tuple[ObservationScope, ...] | None = None

    def __post_init__(self):
        _name(self.fleet)
        if self.scopes is not None:
            _items(self.scopes, ObservationScope, MAX_GRANTS)
            object.__setattr__(self, "scopes", tuple(sorted(self.scopes, key=_scope_key)))


Request = ObservationRead | OperationIntent


def _request(request: Request):
    if type(request) is ObservationRead:
        return "observation.read", digest({
            "schema": "fleet-manager.observation-read", "version": 1,
            "fleet": request.fleet,
            "scopes": None if request.scopes is None else [
                {"origin": item.origin, "project": item.project,
                 "entity": item.entity, "fields": list(item.fields)}
                for item in request.scopes
            ],
        })
    if type(request) is OperationIntent:
        return request.to_dict()["command"], request.request_digest
    raise AdmissionError("unsupported-action")


@dataclass(frozen=True)
class Permit:
    """Owner-retained internal handle; reconstructing its fields grants nothing."""

    authority: str
    fleet: str
    enrollment: Enrollment
    action: str
    request_digest: str
    resources: tuple[ObservationScope, ...] | OperationGrant
    policy_revision: int
    issued_at: int
    expires_at: int


@dataclass(frozen=True)
class _Issued:
    permit: Permit
    identity: VerifiedIdentity
    request: Request


class AdmissionAuthority:
    """One trusted serving owner, with no wire/parser/network or backend access.

    The callback must supply independently authenticated CURRENT policy,
    enrollment and issuer/provider revocation freshness on EVERY call.
    None explicitly means unavailable. Exceptions propagate, never cached allow.
    This API is internal: untrusted requests must never supply identity/snapshot
    records or invoke the callback themselves.
    """

    def __init__(self, *, snapshot: Callable[[], AuthoritySnapshot | None],
                 clock: Callable[[], int], capacity: int = MAX_PERMITS,
                 fence: AdmissionFence | None = None):
        if (not callable(snapshot) or not callable(clock) or type(capacity) is not int
                or not 1 <= capacity <= MAX_PERMITS
                or (fence is not None and (
                    not callable(getattr(fence, "acquire", None))
                    or not callable(getattr(fence, "release", None))))):
            raise AdmissionError("invalid-record")
        self._snapshot = snapshot
        self._clock = clock
        self._capacity = capacity
        self._last_time = None
        self._last_snapshot = None
        self._issued: dict[int, _Issued] = {}
        self._evidence: dict[str, int] = {}
        self._lock = threading.Lock()
        self._fence = fence

    @property
    def fence(self) -> AdmissionFence | None:
        return self._fence

    @contextmanager
    def _guard(self):
        if self._fence is not None and not self._fence.acquire(timeout=FENCE_WAIT_SECONDS):
            raise AdmissionError("authority-busy")
        try:
            with self._lock:
                yield
        finally:
            if self._fence is not None:
                self._fence.release()

    def _now(self):
        now = self._clock()
        _time(now)
        if self._last_time is not None and now < self._last_time:
            raise AdmissionError("clock-rollback")
        self._last_time = now
        return now

    def _current(self):
        current = self._snapshot()
        if current is None:
            raise AdmissionError("authority-unavailable")
        if type(current) is not AuthoritySnapshot:
            raise AdmissionError("invalid-record")
        prior = self._last_snapshot
        if prior is not None:
            if ((current.authority, current.fleet, current.issuer, current.audience)
                    != (prior.authority, prior.fleet, prior.issuer, prior.audience)):
                raise AdmissionError("authority-changed")
            if current.revision < prior.revision:
                raise AdmissionError("stale-policy")
            if current.revision == prior.revision and current != prior:
                raise AdmissionError("conflicting-policy")
            previous = {
                (item.subject, item.role, item.installation): item
                for item in prior.enrollments
            }
            for item in current.enrollments:
                before = previous.get((item.subject, item.role, item.installation))
                if before is None:
                    continue
                if item.generation < before.generation:
                    raise AdmissionError("stale-enrollment")
                if item.generation == before.generation:
                    if item.binding != before.binding:
                        raise AdmissionError("conflicting-enrollment")
                    if before.status == "revoked" and item.status == "active":
                        raise AdmissionError("stale-enrollment")
        self._last_snapshot = current
        return current

    @staticmethod
    def _identity(identity):
        if type(identity) is not VerifiedIdentity or identity.profile != "jwt-pop-v1":
            raise AdmissionError("invalid-identity")
        for value, bound in ((identity.issuer, 2048), (identity.subject, 256),
                             (identity.audience, 512), (identity.proof_key, 43)):
            _text(value, bound)
        for value in (identity.issued_at, identity.expires_at, identity.verified_at):
            _time(value)
        if (identity.issued_at > identity.verified_at + 30
                or identity.expires_at <= identity.verified_at
                or type(identity.evidence) is not str
                or re.fullmatch(r"[0-9a-f]{64}", identity.evidence) is None):
            raise AdmissionError("invalid-identity")

    @staticmethod
    def _valid(current, identity, now):
        if not current.valid_from <= now < current.valid_until:
            raise AdmissionError("stale-policy")
        if now >= current.identity_trust_until:
            raise AdmissionError("stale-identity-trust")
        if (identity.issuer, identity.audience) != (current.issuer, current.audience):
            raise AdmissionError("invalid-identity")
        if now >= identity.expires_at:
            raise AdmissionError("expired-identity")
        if now < identity.verified_at:
            raise AdmissionError("invalid-identity")

    @staticmethod
    def _enrolled(current, identity, role, installation):
        match = next((item for item in current.enrollments if (
            item.subject, item.role, item.installation
        ) == (identity.subject, role, installation)), None)
        if match is None:
            raise AdmissionError("unknown-enrollment")
        if match.status != "active":
            raise AdmissionError("revoked-enrollment")
        return match

    @staticmethod
    def _resources(current, enrolled, request):
        if type(request) is ObservationRead:
            if request.fleet != current.fleet:
                raise AdmissionError("forbidden-scope")
            selected = request.scopes if request.scopes is not None else enrolled.reads
            if not selected or any(
                not any(grant.includes(branch) for grant in enrolled.reads)
                for branch in selected
            ):
                raise AdmissionError("forbidden-scope")
            return selected
        value = request.to_dict()
        if (value["authority"], value["principal"], value["account"]) != (
            current.authority, enrolled.principal, enrolled.account,
        ):
            raise AdmissionError("actor-mismatch")
        requested = OperationGrant(
            value["command"], value["target"], value["generation"],
            request.request_digest if value["command"] == "runner.converge" else None,
        )
        if requested not in enrolled.operations:
            raise AdmissionError("forbidden-scope")
        return requested

    def authorize(self, identity: VerifiedIdentity, request: Request, *,
                  role: str, installation: str) -> Permit:
        self._identity(identity)
        _name(role)
        _name(installation)
        action, request_digest = _request(request)
        with self._guard():
            now = self._now()
            for key in [
                key for key, item in self._issued.items() if now >= item.permit.expires_at
            ]:
                del self._issued[key]
            for key in [key for key, expiry in self._evidence.items() if now >= expiry]:
                del self._evidence[key]
            if now < identity.verified_at:
                raise AdmissionError("invalid-identity")
            if now >= identity.verified_at + REQUEST_SECONDS:
                raise AdmissionError("stale-identity")
            if identity.evidence in self._evidence:
                raise AdmissionError("identity-reused")
            if len(self._issued) >= self._capacity or len(self._evidence) >= MAX_PERMITS:
                raise AdmissionError("resource-limit")
            self._evidence[identity.evidence] = identity.verified_at + REQUEST_SECONDS
            current = self._current()
            self._valid(current, identity, self._now())
            enrolled = self._enrolled(current, identity, role, installation)
            resources = self._resources(current, enrolled, request)
            now = self._now()
            self._valid(current, identity, now)
            if now >= identity.verified_at + REQUEST_SECONDS:
                raise AdmissionError("stale-identity")
            permit = Permit(
                current.authority, current.fleet, enrolled, action, request_digest,
                resources, current.revision, now,
                min(now + PERMIT_SECONDS, current.valid_until,
                    current.identity_trust_until, identity.expires_at),
            )
            self._issued[id(permit)] = _Issued(permit, identity, request)
            return permit

    def revalidate(self, permit: Permit, request: Request) -> Permit:
        """Consume the old handle; success returns the currently retained handle.

        The caller must bind its exact lookup/effect/emit to this checkpoint.
        This library cannot make an external backend transaction atomic.
        """
        return self._checkpoint(permit, request, retain=True)

    def complete(self, permit: Permit, request: Request) -> None:
        """Consume a terminal handle through the same fresh validation boundary."""
        self._checkpoint(permit, request, retain=False)

    def discard(self, permit: Permit) -> bool:
        """Withdraw only this exact retained handle, without an authority decision.

        Cleanup cannot refund consumed identity evidence or authorize an effect.
        No policy/fence callback is needed to remove a local capability, including
        when that callback is the reason the operation failed.
        """
        if type(permit) is not Permit:
            raise AdmissionError("invalid-record")
        with self._lock:
            issued = self._issued.get(id(permit))
            if issued is None or issued.permit is not permit:
                return False
            del self._issued[id(permit)]
            return True

    def _checkpoint(self, permit: Permit, request: Request, *, retain: bool) -> Permit:
        with self._guard():
            issued = self._issued.get(id(permit))
            if issued is None or issued.permit is not permit:
                raise AdmissionError("unknown-permit")
            del self._issued[id(permit)]
            now = self._now()
            if now >= permit.expires_at:
                raise AdmissionError("expired-permit")
            if (type(request) is not type(issued.request)
                    or _request(request) != (permit.action, permit.request_digest)):
                raise AdmissionError("request-mismatch")
            current = self._current()
            self._valid(current, issued.identity, self._now())
            enrolled = self._enrolled(
                current, issued.identity, permit.enrollment.role,
                permit.enrollment.installation,
            )
            if enrolled.binding != permit.enrollment.binding:
                raise AdmissionError("enrollment-changed")
            if self._resources(current, enrolled, request) != permit.resources:
                raise AdmissionError("scope-changed")
            now = self._now()
            self._valid(current, issued.identity, now)
            if now >= permit.expires_at:
                raise AdmissionError("expired-permit")
            renewed = replace(
                permit, enrollment=enrolled, policy_revision=current.revision,
                expires_at=min(permit.expires_at, current.valid_until,
                               current.identity_trust_until, issued.identity.expires_at),
            )
            if retain:
                self._issued[id(renewed)] = _Issued(renewed, issued.identity, request)
            return renewed

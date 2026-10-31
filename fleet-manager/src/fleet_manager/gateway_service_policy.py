"""Current service/caller authority for one explicitly selected source owner.

Grant records are bounded owner-retained handles, not transferable credentials.
Revalidate immediately before an effect and match the actual route separately;
this local checkpoint does not fence a later external effect or durable restart.
No I/O, provider discovery or clock sampling occurs during construction.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from threading import RLock
from typing import NoReturn
import sqlite3

from .enrolled_identity import EnrolledIdentity, EnrolledIdentityVerifier, PROFILE
from .gateway_manifest import ServiceSlot
from .gateway_identity_provider import IdentityProvider
from .gateway_service_context import (
    ServiceContext, ServicePolicyError, ServiceVerifier, identifier, parse_context, require,
)
from .machine_identity import MachineIdentity, MachineIdentityVerifier, PROFILE as MACHINE_PROFILE
from .runtime_identity import IdentityError, IdentityVerifier, VerifiedIdentity
from .operations import OperationIntent
from .runtime_admission import AdmissionAuthority, Permit
from . import planning
from .gateway_enrollment import EnrollmentPolicy, SCHEMA as ENROLLMENT_SCHEMA
from .ledger import OperationLedger

MAX_CALLER_GRANTS = 256
MAX_SERVICE_CANDIDATES = 64


@dataclass(frozen=True)
class ServiceSnapshot:
    """Immutable policy only; never exposes the verifier or mutable trust keys."""

    context: ServiceContext
    generation: int

    @property
    def revision(self) -> int:
        return self.context.manifest.revision


@dataclass(frozen=True)
class ServicePublication:
    changed: bool
    snapshot: ServiceSnapshot
    settlement_error: str | None = None

    @property
    def revision(self) -> int:
        return self.snapshot.revision

    @property
    def generation(self) -> int:
        return self.snapshot.generation


@dataclass(frozen=True)
class ServiceStatus:
    revision: int | None
    generation: int
    slots: int
    last_error: str | None


@dataclass(frozen=True, eq=False)
class ServiceCandidate:
    """Prepared attachment, not the currently serving slot owner."""

    slot: str
    generation: int
    expires_at: int


@dataclass(frozen=True, eq=False)
class ServiceGrant:
    """Only the exact object retained by its issuing authority has standing."""

    slot: str
    generation: int
    expires_at: int


@dataclass(frozen=True, eq=False)
class CallerGrant:
    """One bounded admission handle; release after the request/effect ends."""

    slot: str
    method: str
    target_uri: str
    generation: int
    expires_at: int


@dataclass(frozen=True)
class CallerScope:
    slot: ServiceSlot
    method: str
    target_uri: str
    principal: str
    expires_at: int


@dataclass(frozen=True)
class _Record:
    grant: ServiceCandidate | ServiceGrant | CallerGrant
    identity: VerifiedIdentity = field(repr=False)
    verifier: ServiceVerifier = field(repr=False)
    slot_fingerprint: str
    slot: str
    caller_fingerprint: str | None = None
    method: str | None = None
    target_uri: str | None = None


class ServiceAuthority:
    """Publish complete contexts and own current, process-local grant custody.

    Source callbacks are trusted embedding inputs, not cryptographic provenance.
    The embedding transport supplies method/HTTPS target, bounds concurrent
    handshakes and matches actual route prefixes. An opt-in enrollment producer
    uses the existing journal; transports offload its bounded synchronous I/O.
    """

    def __init__(self, *, fleet: str, gateway: str, source: Callable[[], bytes],
                 clock: Callable[[], int],
                 identity_provider: IdentityProvider | None = None,
                 enrollment_journal: OperationLedger | None = None) -> None:
        self._fleet, self._gateway = identifier(fleet), identifier(gateway)
        require(callable(source), "invalid-source")
        require(callable(clock), "invalid-clock")
        require(identity_provider is None or type(identity_provider) is IdentityProvider,
                "invalid-identity-provider")
        require(enrollment_journal is None or type(enrollment_journal) is OperationLedger,
                "invalid-enrollment-journal")
        self._identity_provider = identity_provider
        self._journal = enrollment_journal
        self._enrollment: EnrollmentPolicy | None = None
        self._source, self._clock = source, clock
        self._lock = RLock()
        self._publication_lock = RLock()
        self._snapshot: ServiceSnapshot | None = None
        self._verifier: ServiceVerifier | None = None
        self._services: dict[str, _Record] = {}
        self._candidates: dict[int, _Record] = {}
        self._callers: dict[int, _Record] = {}
        self._serial = 0
        self._last_time = 0
        self._last_error: str | None = None

    def _fail(self, code: str) -> NoReturn:
        with self._lock:
            self._last_error = code
        raise ServicePolicyError(code) from None

    def _time(self) -> int:
        now = self._clock()
        require(type(now) is int and 0 <= now < 2**53, "invalid-clock")
        require(now >= self._last_time, "clock-rollback")
        self._last_time = now
        return now

    def _current(self) -> tuple[ServiceContext, ServiceVerifier]:
        require(self._snapshot is not None and self._verifier is not None, "not-configured")
        assert self._snapshot is not None and self._verifier is not None
        return self._snapshot.context, self._verifier

    def _active(self) -> tuple[ServiceContext, ServiceVerifier, int]:
        context, verifier = self._current()
        now = self._time()
        require(now < context.trust_valid_until, "authority-unavailable")
        return context, verifier, now

    def status(self) -> ServiceStatus:
        """Non-secret publication evidence without source/key-reference values."""
        with self._lock:
            current = self._snapshot
            return ServiceStatus(current.revision if current else None,
                                 current.generation if current else 0,
                                 len(current.context.manifest.slots) if current else 0,
                                 self._last_error)

    def reload(self) -> ServicePublication:
        """Read off-lock; serialize only the enrollment journal/publication boundary."""
        try:
            raw = self._source()
        except OSError:
            self._fail("source-unavailable")
        try:
            decoded = planning.decode(raw)
        except planning.PlanError:
            self._fail("invalid-json")
        if self._journal is not None:
            with self._publication_lock:
                return self._reload_candidate(raw, decoded)
        return self._reload_candidate(raw, decoded)

    def _eligible(self, context: ServiceContext) -> int:
        with self._lock:
            require(context.manifest.fleet == self._fleet
                    and context.manifest.gateway == self._gateway, "owner-mismatch")
            current = self._snapshot
            if current:
                require(context.manifest.revision >= current.revision, "stale-revision")
                if context.manifest.revision == current.revision:
                    require(context.digest == current.context.digest, "revision-conflict")
            now = self._time()
            require(now < context.trust_valid_until, "authority-unavailable")
            return now

    def _reload_candidate(self, raw: bytes, decoded: object) -> ServicePublication:
        try:
            enrollment = None
            eligible_at = None
            if isinstance(decoded, dict) and decoded.get("schema") == ENROLLMENT_SCHEMA:
                require(self._journal is not None, "enrollment-journal-required")
                assert self._journal is not None
                enrollment = self._enrollment
                if enrollment is None or planning.digest(decoded) != enrollment.digest:
                    enrollment = EnrollmentPolicy(decoded, self._journal, self._clock)
                require((enrollment.fleet, enrollment.gateway) == (self._fleet, self._gateway),
                        "owner-mismatch")
                def eligible(candidate: ServiceContext) -> None:
                    nonlocal eligible_at
                    eligible_at = self._eligible(candidate)

                context, candidate_verifier = enrollment.publication(eligible)
            else:
                require(self._enrollment is None and self._journal is None,
                        "enrollment-rollback-refused")
                context, candidate_verifier = parse_context(
                    raw, self._clock, identity_provider=self._identity_provider)
            require(context.manifest.fleet == self._fleet
                    and context.manifest.gateway == self._gateway, "owner-mismatch")
        except ServicePolicyError as exc:
            self._fail(exc.code)
        except (planning.PlanError, sqlite3.Error):
            self._fail("enrollment-journal-refused")
        with self._lock:
            # Enrollment eligibility already ran inside the committed journal
            # transaction. Do not reject its durable publication after commit.
            try:
                now = self._eligible(context) if enrollment is None else eligible_at
            except ServicePolicyError as exc:
                self._fail(exc.code)
            assert now is not None
            current = self._snapshot
            changed = current is None or context.manifest.revision != current.revision
            if changed:
                if not current or current.context.trust_digest != context.trust_digest:
                    if not (type(self._verifier) is MachineIdentityVerifier
                            and type(candidate_verifier) is MachineIdentityVerifier
                            and self._verifier.refresh(candidate_verifier)):
                        self._verifier = candidate_verifier
                self._enrollment = enrollment
                self._snapshot = ServiceSnapshot(context, current.generation + 1 if current else 1)
                self._prune(context, now)
            assert self._snapshot is not None
            installed = self._snapshot
            self._last_error = None
        settlement_error = None
        if enrollment is not None:
            try:
                enrollment.reconcile_publication(context)
            except (planning.PlanError, sqlite3.Error):
                settlement_error = "enrollment-settlement-pending"
                with self._lock:
                    self._last_error = settlement_error
        return ServicePublication(changed, installed, settlement_error)

    def enrollment_challenge(self) -> str:
        with self._publication_lock:
            self.reload()
            with self._lock:
                require(self._enrollment is not None, "unsupported-enrollment")
                assert self._enrollment is not None
                self._active()
                return self._enrollment.nonce

    def enroll(self, raw: bytes, *, grant: str, proof: str,
               target_uri: str, destination: str) -> dict[str, object]:
        """Authenticate delegation/possession, persist original operation and publish."""
        with self._publication_lock:
            self.reload()
            with self._lock:
                producer = self._enrollment
                require(producer is not None, "unsupported-enrollment")
                assert producer is not None
                self._active()
            try:
                intent, fingerprint = producer.enroll(
                    raw, grant=grant, proof=proof, target_uri=target_uri, destination=destination)
                self.reload()
                require(self._enrollment is producer, "enrollment-policy-changed")
            except IdentityError as exc:
                self._fail(exc.code)
            except (planning.PlanError, sqlite3.Error):
                self._fail("enrollment-journal-refused")
            return {
                "profile": MACHINE_PROFILE, "operation": intent.to_dict()["operation"],
                "enrollment": fingerprint, "state": "published",
            }

    def challenge(self) -> str:
        """Return the current verifier nonce, not desired inventory."""
        with self._lock:
            _, verifier, _ = self._active()
            return verifier.nonce

    def machine_profile(self) -> bool:
        """Selected runtime capability, never inferred from a presented credential."""
        with self._lock:
            _, verifier, _ = self._active()
            return type(verifier) is MachineIdentityVerifier

    def connect_machine(self, principal: str, proof: str, *, slot: str,
                        target_uri: str, content_digest: str) -> dict[str, str]:
        """Authenticate current outbound access without invoking a service or SSH."""
        identity, verifier = self._verify(
            principal, proof, "POST", target_uri, "caller", slot, content_digest)
        require(type(identity) is MachineIdentity, "unsupported-profile")
        with self._lock:
            context, _ = self._verified_current(identity, verifier)
            caller = context.caller(identity.subject)
            context.slot(slot)
            require(slot in caller.slots
                    and context.key(caller.key_source).thumbprint == identity.proof_key,
                    "caller-denied")
            return {"profile": MACHINE_PROFILE, "state": "connected", "principal": principal}

    def _verify(self, access: str, proof: str, method: str,
                target_uri: str, kind: str, resource: str,
                content_digest: str | None = None) -> tuple[VerifiedIdentity, ServiceVerifier]:
        with self._lock:
            _, verifier, _ = self._active()
        try:
            if type(verifier) is MachineIdentityVerifier:
                require(content_digest is not None, "content-binding-required")
                identity = verifier.verify(
                    access, proof, method=method, target_uri=target_uri, kind=kind,
                    resource=resource, content_digest=content_digest)
                require(type(identity) is MachineIdentity and identity.profile == MACHINE_PROFILE
                        and identity.kind == kind and identity.resource == resource,
                        "invalid-credential")
            elif type(verifier) is EnrolledIdentityVerifier:
                identity = verifier.verify(
                    access, proof, method=method, target_uri=target_uri, kind=kind)
                require(type(identity) is EnrolledIdentity and identity.profile == PROFILE
                        and identity.kind == kind, "invalid-credential")
            else:
                require(type(verifier) is IdentityVerifier, "unsupported-profile")
                identity = verifier.verify(access, proof, method=method, target_uri=target_uri)
                require(type(identity) is VerifiedIdentity and identity.profile == "jwt-pop-v1",
                        "invalid-credential")
        except IdentityError as exc:
            raise ServicePolicyError(exc.code) from None
        return identity, verifier

    def _verified_current(self, identity: VerifiedIdentity, verifier: ServiceVerifier
                          ) -> tuple[ServiceContext, int]:
        context, current, now = self._active()
        require(verifier is current, "trust-changed")
        if type(identity) is MachineIdentity and type(current) is MachineIdentityVerifier:
            require(current.current(identity), "trust-changed")
        require(identity.verified_at <= now < identity.expires_at, "expired-grant")
        return context, now

    def _valid(self, record: _Record, context: ServiceContext, now: int) -> bool:
        identity = record.identity
        return (
            record.verifier is self._verifier
            and (type(identity) is not MachineIdentity
                 or (type(self._verifier) is MachineIdentityVerifier
                     and self._verifier.current(identity)))
            and identity.verified_at <= now < identity.expires_at
            and now < context.trust_valid_until
            and context.slot_fingerprint(record.slot) == record.slot_fingerprint
            and (record.caller_fingerprint is None or context.caller_fingerprint(
                identity.subject) == record.caller_fingerprint)
        )

    def _prune(self, context: ServiceContext, now: int) -> None:
        self._services = {slot: record for slot, record in self._services.items()
                          if self._valid(record, context, now)}
        self._candidates = {serial: record for serial, record in self._candidates.items()
                            if self._valid(record, context, now)}
        self._callers = {serial: record for serial, record in self._callers.items()
                         if self._valid(record, context, now)}

    def _next(self) -> int:
        self._serial += 1
        return self._serial

    def authenticate_service(self, access: str, proof: str, *, target_uri: str,
                             slot: str, content_digest: str | None = None) -> ServiceGrant:
        """Immediately promote a verified service, for non-ticket embeddings."""
        candidate = self.prepare_service(
            access, proof, target_uri=target_uri, slot=slot, content_digest=content_digest)
        return self.promote_service(candidate)

    def prepare_service(self, access: str, proof: str, *, target_uri: str,
                        slot: str, content_digest: str | None = None) -> ServiceCandidate:
        """Prepare attachment without withdrawing a healthy active generation."""
        identity, verifier = self._verify(
            access, proof, "POST", target_uri, "service", slot, content_digest)
        with self._lock:
            context, now = self._verified_current(identity, verifier)
            selected = context.slot(slot)
            require(identity.subject == selected.principal, "service-denied")
            require(identity.proof_key == context.key(selected.key_source).thumbprint,
                    "service-denied")
            self._prune(context, now)
            require(len(self._candidates) < MAX_SERVICE_CANDIDATES, "resource-limit")
            grant = ServiceCandidate(selected.name, self._next(), identity.expires_at)
            fingerprint = context.slot_fingerprint(selected.name)
            assert fingerprint is not None
            self._candidates[grant.generation] = _Record(
                grant, identity, verifier, fingerprint, selected.name)
            return grant

    def promote_service(self, candidate: ServiceCandidate) -> ServiceGrant:
        """Consume one current candidate at the ready channel's activation point."""
        with self._lock:
            context, _, now = self._active()
            require(type(candidate) is ServiceCandidate
                    and type(candidate.generation) is int, "invalid-candidate")
            record = self._candidates.get(candidate.generation)
            require(record is not None and record.grant is candidate, "invalid-candidate")
            assert record is not None
            del self._candidates[candidate.generation]
            require(self._valid(record, context, now), "revoked-candidate")
            active = self._services.get(record.slot)
            require(active is None or candidate.generation > active.grant.generation,
                    "stale-candidate")
            grant = ServiceGrant(
                record.slot, candidate.generation, record.identity.expires_at)
            self._candidates = {
                serial: pending for serial, pending in self._candidates.items()
                if pending.slot != record.slot or serial > candidate.generation
            }
            self._services[record.slot] = _Record(
                grant, record.identity, record.verifier, record.slot_fingerprint, record.slot)
            return grant

    def revalidate_candidate(self, candidate: ServiceCandidate) -> ServiceSlot:
        """Validate ready attachment without evicting the active generation."""
        with self._lock:
            context, _, now = self._active()
            require(type(candidate) is ServiceCandidate
                    and type(candidate.generation) is int, "invalid-candidate")
            record = self._candidates.get(candidate.generation)
            require(record is not None and record.grant is candidate, "invalid-candidate")
            assert record is not None
            require(self._valid(record, context, now), "revoked-candidate")
            active = self._services.get(record.slot)
            require(active is None or candidate.generation > active.grant.generation,
                    "stale-candidate")
            return context.slot(record.slot)

    def release_candidate(self, candidate: ServiceCandidate) -> bool:
        """Expire an unused ticket without touching the active slot owner."""
        with self._lock:
            if type(candidate) is not ServiceCandidate or type(candidate.generation) is not int:
                return False
            record = self._candidates.get(candidate.generation)
            if record is None or record.grant is not candidate:
                return False
            del self._candidates[candidate.generation]
            return True

    def revalidate_service(self, grant: ServiceGrant) -> ServiceSlot:
        """Check exact current generation immediately before using its channel."""
        with self._lock:
            context, _, now = self._active()
            require(type(grant) is ServiceGrant and isinstance(grant.slot, str),
                    "invalid-grant")
            record = self._services.get(grant.slot)
            require(record is not None and record.grant is grant, "invalid-grant")
            assert record is not None
            if not self._valid(record, context, now):
                del self._services[grant.slot]
                raise ServicePolicyError("revoked-grant")
            return context.slot(record.slot)

    def release_service(self, grant: ServiceGrant) -> bool:
        """Retire only this exact handle; an old close cannot retire replacement."""
        with self._lock:
            if type(grant) is not ServiceGrant or not isinstance(grant.slot, str):
                return False
            record = self._services.get(grant.slot)
            if record is None or record.grant is not grant:
                return False
            del self._services[grant.slot]
            return True

    def authenticate_caller(self, access: str, proof: str, *, method: str,
                            target_uri: str, slot: str, request_method: str,
                            content_digest: str | None = None) -> CallerGrant:
        """Admit only an actual method present in both explicit caller and slot.

        No method translation is implied: request_method must equal the
        transport-derived method authenticated by PoP. Route-prefix matching
        remains the runtime's obligation, including at the effect checkpoint.
        """
        require(method == request_method, "method-denied")
        identity, verifier = self._verify(
            access, proof, method, target_uri, "caller", slot, content_digest)
        with self._lock:
            context, now = self._verified_current(identity, verifier)
            selected, caller = context.slot(slot), context.caller(identity.subject)
            require(identity.proof_key == context.key(caller.key_source).thumbprint,
                    "caller-denied")
            require(selected.name in caller.slots and request_method in caller.methods
                    and any(request_method in route.methods for route in selected.routes),
                    "caller-denied")
            self._prune(context, now)
            require(len(self._callers) < MAX_CALLER_GRANTS, "resource-limit")
            grant = CallerGrant(selected.name, request_method, target_uri,
                                self._next(), identity.expires_at)
            slot_fingerprint = context.slot_fingerprint(selected.name)
            caller_fingerprint = context.caller_fingerprint(identity.subject)
            assert slot_fingerprint is not None and caller_fingerprint is not None
            self._callers[grant.generation] = _Record(
                grant, identity, verifier, slot_fingerprint, selected.name,
                caller_fingerprint, request_method, target_uri)
            return grant

    def revalidate_caller(self, grant: CallerGrant) -> CallerScope:
        """Current lookup/effect checkpoint, not a transferable offline permit."""
        with self._lock:
            context, _, now = self._active()
            require(type(grant) is CallerGrant and type(grant.generation) is int,
                    "invalid-grant")
            record = self._callers.get(grant.generation)
            require(record is not None and record.grant is grant, "invalid-grant")
            assert record is not None
            if not self._valid(record, context, now):
                del self._callers[grant.generation]
                raise ServicePolicyError("revoked-grant")
            assert record.method is not None and record.target_uri is not None
            return CallerScope(
                context.slot(record.slot), record.method, record.target_uri,
                record.identity.subject, min(record.identity.expires_at, context.trust_valid_until))

    def release_caller(self, grant: CallerGrant) -> bool:
        """Release one exact retained request handle without evicting others."""
        with self._lock:
            if type(grant) is not CallerGrant or type(grant.generation) is not int:
                return False
            record = self._callers.get(grant.generation)
            if record is None or record.grant is not grant:
                return False
            del self._callers[grant.generation]
            return True

    def authorize_operation(
        self, grant: CallerGrant, intent: OperationIntent, admission: AdmissionAuthority, *,
        authority: str, role: str, installation: str, checkpoint: Callable[[], None],
    ) -> Permit:
        """Bind local execution to the original verified request, never wire context.

        The embedding checkpoint must also match the actual current route/local
        owner and request lifetime. It performs no I/O and remains attached to
        every permit checkpoint. Separate operation policy grants the effect.
        Authority names the registered backend, not this Gateway's routing name.
        """
        require(type(intent) is OperationIntent and type(admission) is AdmissionAuthority
                and callable(checkpoint), "invalid-operation-binding")
        identifier(authority)
        with self._lock:
            scope = self.revalidate_caller(grant)
            require(scope.method == "POST", "method-denied")
            record = self._callers[grant.generation]
            identity = record.identity

        def current() -> None:
            self.revalidate_caller(grant)
            require(checkpoint() is None, "invalid-operation-binding")

        permit = admission.authorize(
            identity, intent, role=role, installation=installation, checkpoint=current)
        if (permit.fleet, permit.authority) != (self._fleet, authority):
            admission.discard(permit)
            raise ServicePolicyError("operation-owner-mismatch")
        return permit

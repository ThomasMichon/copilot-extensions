"""Opt-in application tokens plus independently enrolled proof keys.

Authenticated complete policy owns these mappings, never a presented token,
proof key or provider callback. This is not issuer-bound DPoP enrollment.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hmac

from . import planning
from .bearer_identity import (
    ClaimRequirement, _check_requirements, _claim, _key, _requirements_valid, _rs256_claims,
)
from .runtime_identity import (
    IdentityError, IdentityVerifier, MAX_REPLAYS, VerifiedIdentity, _AccessBinding, _text, _time,
)

PROFILE = "enrolled-pop-v1"
RS256_PROFILE = "enrolled-rs256-pop-v1"
MAX_AUDIENCES = 16
MAX_ENROLLMENTS = 128
KINDS = frozenset({"service", "caller"})


@dataclass(frozen=True, repr=False)
class EnrolledBinding:
    """Resolved public enrollment, with no mutable policy or key aliases."""

    subject: str
    client_id: str
    principal: str
    kind: str
    resource: str
    key_source: str
    proof_key: str
    expires_at: int


@dataclass(frozen=True)
class EnrolledIdentity(VerifiedIdentity):
    """Canonical identity whose admission kind is independently enrolled."""

    profile: str = PROFILE
    kind: str = ""


class EnrolledIdentityVerifier(IdentityVerifier):
    """The common crypto/replay engine with an explicit enrollment snapshot."""

    _profile = PROFILE

    def __init__(self, *, issuer: str, audience: str, jwks: object,
                 trust_valid_until: int, clock: Callable[[], int],
                 allowed_audiences: tuple[str, ...],
                 enrollments: tuple[EnrolledBinding, ...],
                 replay_capacity: int = MAX_REPLAYS) -> None:
        super().__init__(issuer=issuer, audience=audience, jwks=jwks,
                         trust_valid_until=trust_valid_until, clock=clock,
                         replay_capacity=replay_capacity)
        if (type(allowed_audiences) is not tuple
                or not 1 <= len(allowed_audiences) <= MAX_AUDIENCES
                or not all(_text(item, 512) for item in allowed_audiences)
                or len(set(allowed_audiences)) != len(allowed_audiences)
                or self._audience not in allowed_audiences
                or type(enrollments) is not tuple
                or len(enrollments) > MAX_ENROLLMENTS):
            raise IdentityError("invalid-enrollment")
        bindings: dict[tuple[str, str, str, str], EnrolledBinding] = {}
        for item in enrollments:
            if type(item) is not EnrolledBinding:
                raise IdentityError("invalid-enrollment")
            if (not _text(item.subject) or not _text(item.client_id)
                    or not isinstance(item.kind, str) or item.kind not in KINDS
                    or item.resource != self._audience
                    or not _text(item.proof_key, 43) or len(item.proof_key) != 43
                    or not _time(item.expires_at)):
                raise IdentityError("invalid-enrollment")
            try:
                planning.identifier(item.principal, "principal")
                planning.identifier(item.key_source, "key-source")
            except planning.PlanError:
                raise IdentityError("invalid-enrollment") from None
            lookup = (item.subject, item.client_id, item.kind, item.resource)
            if lookup in bindings:
                raise IdentityError("ambiguous-enrollment")
            bindings[lookup] = item
        self._allowed_audiences = frozenset(allowed_audiences)
        self._enrollments = bindings

    def _audience_matches(self, audience: object) -> bool:
        audiences: list[str]
        if isinstance(audience, str):
            audiences = [audience]
        elif isinstance(audience, list):
            if not 1 <= len(audience) <= MAX_AUDIENCES:
                return False
            audiences = []
            for item in audience:
                if not isinstance(item, str) or not _text(item, 512):
                    return False
                audiences.append(item)
        else:
            return False
        return (
            1 <= len(audiences) <= MAX_AUDIENCES
            and all(_text(item, 512) for item in audiences)
            and len(set(audiences)) == len(audiences)
            and self._audience in audiences
            and set(audiences) <= self._allowed_audiences
        )

    def _client(self, claims: Mapping[str, object]) -> object:
        return claims.get("client_id")

    def _access_binding(self, claims: Mapping[str, object],
                        kind: str | None) -> _AccessBinding:
        if not isinstance(kind, str) or kind not in KINDS:
            raise IdentityError("invalid-request-context")
        subject, client, expires = self._subject(claims), self._client(claims), claims.get("exp")
        if (not isinstance(subject, str) or not isinstance(client, str)
                or not _text(client) or type(expires) is not int):
            raise IdentityError("invalid-credential")
        binding = self._enrollments.get((subject, client, kind, self._audience))
        if binding is None:
            raise IdentityError("invalid-key-binding")
        if "cnf" in claims:
            cnf = claims["cnf"]
            if (not isinstance(cnf, dict) or set(cnf) != {"jkt"}
                    or not isinstance(cnf["jkt"], str)
                    or not _text(cnf["jkt"], 43)
                    or not hmac.compare_digest(cnf["jkt"], binding.proof_key)):
                raise IdentityError("invalid-key-binding")
        return _AccessBinding(binding.principal, binding.proof_key,
                              min(expires, binding.expires_at))

    def _identity(self, claims: Mapping[str, object], binding: _AccessBinding,
                  now: int, evidence: str, kind: str | None) -> EnrolledIdentity:
        assert isinstance(kind, str)
        identity = super()._identity(claims, binding, now, evidence, kind)
        return EnrolledIdentity(
            issuer=identity.issuer, subject=identity.subject, audience=identity.audience,
            proof_key=identity.proof_key, issued_at=identity.issued_at,
            expires_at=identity.expires_at, verified_at=identity.verified_at,
            evidence=identity.evidence, kind=kind, profile=self._profile,
        )


class EnrolledRS256IdentityVerifier(EnrolledIdentityVerifier):
    """Original RSA token plus independently approved P-256 request enrollment."""

    _profile = RS256_PROFILE
    _signing_key = staticmethod(_key)

    def __init__(self, *, issuer: str, audience: str, jwks: object,
                 trust_valid_until: int, clock: Callable[[], int],
                 allowed_audiences: tuple[str, ...], enrollments: tuple[EnrolledBinding, ...],
                 subject_claim: str, client_claim: str,
                 required_claims: tuple[ClaimRequirement, ...],
                 replay_capacity: int = MAX_REPLAYS) -> None:
        if (not _claim(subject_claim) or not _claim(client_claim)
                or not _requirements_valid(required_claims)):
            raise IdentityError("invalid-trust")
        self._subject_claim, self._client_claim = subject_claim, client_claim
        self._required = required_claims
        super().__init__(
            issuer=issuer, audience=audience, jwks=jwks, trust_valid_until=trust_valid_until,
            clock=clock, allowed_audiences=allowed_audiences, enrollments=enrollments,
            replay_capacity=replay_capacity)

    def _access_claims(self, access_token: str) -> dict[str, object]:
        return _rs256_claims(access_token, self._keys)

    def _subject(self, claims: Mapping[str, object]) -> object:
        return claims.get(self._subject_claim)

    def _client(self, claims: Mapping[str, object]) -> object:
        return claims.get(self._client_claim)

    def _access_binding(self, claims: Mapping[str, object],
                        kind: str | None) -> _AccessBinding:
        _check_requirements(claims, self._required)
        return super()._access_binding(claims, kind)

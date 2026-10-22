"""Trusted, pure public-trust adapters; never replacement request verifiers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from . import planning
from .runtime_identity import IdentityError, IdentityVerifier

PROFILE = "jwt-pop-v1"


class IdentityProviderError(ValueError):
    """Adapters raise this value-free error for unavailable/invalid trust."""

    def __init__(self) -> None:
        super().__init__("provider-trust-unavailable")


@dataclass(frozen=True)
class IdentityTrust:
    """Public, immutable candidate; the common verifier still validates it."""

    issuer: str
    audience: str
    jwks: bytes = field(repr=False)
    trust_valid_until: int


@dataclass(frozen=True)
class IdentityProvider:
    """Explicit code registration, frozen for one authority's lifetime.

    ``prepare`` receives canonical bounded JSON bytes and returns public trust,
    without I/O, clock sampling, login or other effects. Authenticated snapshot
    acquisition/publication belongs outside this validation callback.
    """

    name: str
    prepare: Callable[[bytes], IdentityTrust] = field(repr=False)

    def __post_init__(self) -> None:
        try:
            planning.identifier(self.name, "provider")
        except planning.PlanError:
            raise IdentityError("invalid-identity-provider") from None
        if not callable(self.prepare):
            raise IdentityError("invalid-identity-provider")


def prepare_identity(value: object, provider: IdentityProvider | None,
                     clock: Callable[[], int]
                     ) -> tuple[dict[str, object], IdentityVerifier, int]:
    """Normalize trust without admitting a plugin-created authenticated identity."""
    if not isinstance(value, dict):
        raise IdentityError("invalid-trust")
    if set(value) != {"provider", "profile", "configuration"}:
        raise IdentityError("invalid-trust")
    if provider is None or value["provider"] != provider.name:
        raise IdentityError("unsupported-identity-provider")
    if value["profile"] != PROFILE:
        raise IdentityError("unsupported-profile")
    if not isinstance(value["configuration"], dict):
        raise IdentityError("invalid-trust")
    candidate = provider.prepare(planning.canonical(value["configuration"]))
    if type(candidate) is not IdentityTrust or type(candidate.jwks) is not bytes:
        raise IdentityError("invalid-provider-trust")
    trust = {
        "issuer": candidate.issuer, "audience": candidate.audience,
        "jwks": planning.decode(candidate.jwks),
        "trust_valid_until": candidate.trust_valid_until,
    }
    jwks = trust["jwks"]
    if (not isinstance(jwks, dict) or set(jwks) != {"keys"}
            or not isinstance(jwks["keys"], list) or not 1 <= len(jwks["keys"]) <= 16):
        raise IdentityError("invalid-trust")
    normalized = dict(trust, jwks={"keys": sorted(jwks["keys"], key=planning.canonical)})
    verifier = IdentityVerifier(**normalized, clock=clock)
    expires = trust["trust_valid_until"]
    assert type(expires) is int
    return {
        "provider": provider.name, "profile": PROFILE,
        "configuration": value["configuration"], "trust": normalized,
    }, verifier, expires

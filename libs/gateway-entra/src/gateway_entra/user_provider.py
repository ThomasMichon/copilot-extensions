"""Entra's strict claim policy through the common Fleet caller verifier."""

import json
from hashlib import sha256
from threading import RLock
from typing import Any

from .trust import EntraApplication, EntraTrustError, TrustSnapshot, _guid, _public_keys


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


class EntraUserProvider:
    """Explicitly published public trust, never a token or authenticated identity.

    The hosting owner fetches and publishes trust before atomically publishing
    its matching complete Fleet policy. Provider preparation performs no I/O,
    clock sampling, discovery, login, claim rewriting or permit creation.
    """

    def __init__(self, application: EntraApplication) -> None:
        if type(application) is not EntraApplication:
            raise EntraTrustError("invalid-application")
        try:
            from fleet_manager.bearer_identity import (
                PROFILE,
                BearerIdentityVerifier,
                ClaimRequirement,
            )
            from fleet_manager.gateway_identity_provider import (
                IdentityProvider,
                IdentityProviderError,
                IdentityTrust,
            )
            from fleet_manager.runtime_identity import IdentityError
            requirements = (
                ClaimRequirement("tid", (application.tenant,), shape="scalar"),
                ClaimRequirement("roles", (application.required_role,), shape="list"),
                ClaimRequirement("scp", (application.required_scope,), shape="scalar"),
            )
        except (ImportError, TypeError):
            raise EntraTrustError("unsupported-user-profile") from None
        if PROFILE != "user-bearer-v1":
            raise EntraTrustError("unsupported-user-profile")
        self.application = application
        self._requirements = requirements
        self._verifier_type = BearerIdentityVerifier
        self._trust_type, self._provider_error = IdentityTrust, IdentityProviderError
        self._identity_error = IdentityError
        self._lock = RLock()
        self._snapshot: TrustSnapshot | None = None
        self._configuration: bytes | None = None
        self.identity_provider = IdentityProvider("entra-user", self._prepare)

    def publish(self, snapshot: TrustSnapshot) -> None:
        """Select a fetched immutable snapshot; this is not policy publication."""
        if (type(snapshot) is not TrustSnapshot
                or (snapshot.issuer, snapshot.audience)
                != (self.application.issuer, self.application.audience)
                or type(snapshot.fetched_at) is not int
                or type(snapshot.trust_valid_until) is not int
                or not 0 <= snapshot.fetched_at < snapshot.trust_valid_until
                <= min(snapshot.fetched_at + 300, 2**53 - 1)):
            raise EntraTrustError("invalid-public-trust")
        normalized = _public_keys(snapshot.jwks)
        if normalized != snapshot.jwks:
            raise EntraTrustError("invalid-public-trust")
        try:
            self._verifier_type(
                issuer=snapshot.issuer, audience=snapshot.audience,
                jwks=json.loads(snapshot.jwks), trust_valid_until=snapshot.trust_valid_until,
                clock=lambda: snapshot.fetched_at, principal_claim="oid",
                required_claims=self._requirements, mappings=(),
            )
        except self._identity_error as error:
            if error.code == "unsupported-profile":
                raise EntraTrustError("unsupported-user-profile") from None
            raise EntraTrustError("invalid-public-trust") from None
        except ValueError:
            raise EntraTrustError("invalid-public-trust") from None
        reference = sha256(_canonical({
            "issuer": snapshot.issuer, "audience": snapshot.audience,
            "jwks": json.loads(snapshot.jwks), "fetched_at": snapshot.fetched_at,
            "trust_valid_until": snapshot.trust_valid_until,
        })).hexdigest()
        with self._lock:
            self._snapshot = snapshot
            self._configuration = _canonical({"snapshot": reference})

    def _prepare(self, configuration: object) -> Any:
        with self._lock:
            if (type(configuration) is not bytes or self._snapshot is None
                    or configuration != self._configuration):
                raise self._provider_error()
            snapshot = self._snapshot
            return self._trust_type(
                snapshot.issuer, snapshot.audience, snapshot.jwks, snapshot.trust_valid_until)

    def user_callers(self, *, mappings: list[Any], callers: list[Any]) -> dict[str, Any]:
        """Build caller-policy inputs; the current source and grants remain separate."""
        if (type(mappings) is not list or len(mappings) > 128
                or type(callers) is not list or len(callers) > 128):
            raise EntraTrustError("invalid-user-policy")
        try:
            for mapping in mappings:
                if type(mapping) is not dict or set(mapping) != {"subject", "principal"}:
                    raise EntraTrustError("invalid-user-policy")
                _guid(mapping["subject"])
            selected = json.loads(_canonical({"mappings": mappings, "callers": callers}))
        except (ValueError, TypeError, OverflowError):
            raise EntraTrustError("invalid-user-policy") from None
        with self._lock:
            if self._snapshot is None or self._configuration is None:
                raise EntraTrustError("public-trust-unavailable")
            return {
                "identity": {
                    "provider": self.identity_provider.name, "profile": "user-bearer-v1",
                    "configuration": json.loads(self._configuration),
                },
                "principal_claim": "oid",
                "required_claims": [
                    {"claim": "tid", "values": [self.application.tenant], "shape": "scalar"},
                    {"claim": "roles", "values": [self.application.required_role], "shape": "list"},
                    {"claim": "scp", "values": [self.application.required_scope], "shape": "scalar"},
                ],
                **selected,
            }

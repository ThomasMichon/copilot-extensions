"""Explicit public-key request authentication; no application token or issuer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import hmac
import re
import secrets
import time
from typing import Any

from .planning import PlanError, canonical, decode
from .runtime_identity import (
    IdentityError, MAX_PROOF_BYTES, MAX_REPLAYS, ProofFence, VerifiedIdentity,
    _https, _jose, _parts, _public_key, _text, _time, _verify,
)

PROFILE = "machine-key-v1"
PROOF_TYPE = "fleet-machine-proof+jwt"
MAX_MACHINES = 128


def make_machine_proof(key: Any, *, principal: str, audience: str, kind: str,
                       resource: str, method: str, target_uri: str, nonce: str,
                       body: bytes, now: int | None = None) -> str:
    """Sign one exact request; this proof is not a reusable application token."""
    jwt, _, _ = _jose()
    return jwt.encode(
        {"alg": "ES256", "typ": PROOF_TYPE, "kid": key.thumbprint()},
        {"sub": principal, "aud": audience, "kind": kind, "resource": resource,
         "htm": method, "htu": target_uri, "nonce": nonce,
         "iat": int(time.time()) if now is None else now, "jti": secrets.token_urlsafe(24),
         "sha256": hashlib.sha256(body).hexdigest()}, key)


def machine_headers(principal: str, proof: str) -> dict[str, str]:
    scheme = "Fleet-" + "Machine "
    return {"Authorization": scheme + principal, "Fleet-Machine-Proof": proof}


@dataclass(frozen=True)
class MachineBinding:
    principal: str
    kind: str
    resource: str
    public_key: bytes
    expires_at: int


@dataclass(frozen=True)
class MachineIdentity(VerifiedIdentity):
    profile: str = PROFILE
    kind: str = ""
    resource: str = ""
    binding: MachineBinding | None = None


class MachineIdentityVerifier(ProofFence):
    """Authenticate only currently published, independently revocable keys."""

    def __init__(self, *, audience: str, bindings: tuple[MachineBinding, ...],
                 trust_valid_until: int, clock: Callable[[], int],
                 replay_capacity: int = MAX_REPLAYS) -> None:
        super().__init__(trust_valid_until=trust_valid_until, clock=clock,
                         replay_capacity=replay_capacity)
        if (not _text(audience, 512) or type(bindings) is not tuple
                or len(bindings) > MAX_MACHINES):
            raise IdentityError("invalid-enrollment")
        self._audience = audience
        self._bindings = {}
        for binding in bindings:
            if (type(binding) is not MachineBinding or not _text(binding.principal)
                    or binding.kind not in ("caller", "service")
                    or not _text(binding.resource) or not _time(binding.expires_at)
                    or type(binding.public_key) is not bytes):
                raise IdentityError("invalid-enrollment")
            try:
                value = decode(binding.public_key)
                if canonical(value) != binding.public_key:
                    raise IdentityError("invalid-key")
            except PlanError:
                raise IdentityError("invalid-key") from None
            key = _public_key(value)
            key.get_op_key("verify")
            lookup = (binding.principal, binding.kind, binding.resource)
            if lookup in self._bindings:
                raise IdentityError("ambiguous-enrollment")
            self._bindings[lookup] = (binding, key)

    def refresh(self, candidate: MachineIdentityVerifier) -> bool:
        """Replace current bindings without resetting an unchanged proof epoch."""
        if (self._audience != candidate._audience
                or self._trust_valid_until != candidate._trust_valid_until):
            return False
        with self._lock:
            self._bindings = candidate._bindings.copy()
        return True

    def current(self, identity: MachineIdentity) -> bool:
        """Previously admitted work retains standing only for the exact binding."""
        with self._lock:
            selected = self._bindings.get((identity.subject, identity.kind, identity.resource))
            return selected is not None and selected[0] == identity.binding

    def verify(self, principal: str, proof: str, *, method: str, target_uri: str,
               kind: str, resource: str, content_digest: str) -> MachineIdentity:
        """Bind a proof to the actual buffered body and full server-derived URI."""
        if (not _https(target_uri) or not isinstance(method, str)
                or not re.fullmatch(r"[A-Z]{1,16}", method)
                or not isinstance(content_digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", content_digest)
                or not _text(principal) or not isinstance(kind, str)
                or kind not in ("service", "caller") or not _text(resource)):
            raise IdentityError("invalid-request-context")
        with self._lock:
            self._now_locked()
            selected = self._bindings.get((principal, kind, resource))
        if selected is None:
            raise IdentityError("invalid-key-binding")
        binding, key = selected
        return self._verify_binding(
            binding, key, proof, method=method, target_uri=target_uri,
            content_digest=content_digest)

    def _verify_binding(self, binding: MachineBinding, key: Any, proof: str, *,
                        method: str, target_uri: str, content_digest: str) -> MachineIdentity:
        """Also used after the enrollment owner authenticates an exact delegation."""
        principal, kind, resource = binding.principal, binding.kind, binding.resource
        header, claims = _parts(proof, MAX_PROOF_BYTES)
        if (set(header) != {"alg", "typ", "kid"} or header["alg"] != "ES256"
                or header["typ"] != PROOF_TYPE or header["kid"] != key.thumbprint()):
            raise IdentityError("invalid-proof")
        _verify(proof, key)
        expected = {
            "sub": principal, "aud": self._audience, "kind": kind, "resource": resource,
            "htm": method, "htu": target_uri, "sha256": content_digest,
        }
        if (set(claims) != set(expected) | {"iat", "nonce", "jti"}
                or any(claims[name] != value for name, value in expected.items())
                or not _time(claims["iat"]) or not _text(claims["jti"], 128)
                or not _text(claims["nonce"], 43)
                or not hmac.compare_digest(claims["nonce"], self.nonce)):
            raise IdentityError("invalid-proof")
        with self._lock:
            now = self._now_locked()
            if now >= binding.expires_at:
                raise IdentityError("expired-credential")
            self._consume_locked(key.thumbprint(), claims["jti"], claims["iat"], now)
        return MachineIdentity(
            issuer=PROFILE, subject=principal, audience=self._audience,
            proof_key=key.thumbprint(), issued_at=claims["iat"],
            expires_at=min(binding.expires_at, self._trust_valid_until),
            verified_at=now, evidence=hashlib.sha256(proof.encode("ascii")).hexdigest(),
            kind=kind, resource=resource,
            binding=binding,
        )

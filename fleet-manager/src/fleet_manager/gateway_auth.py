"""Shared network identity admission and separate Ed25519 receipt authentication."""

from __future__ import annotations

import hashlib
import base64
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from jwcrypto import jwk

from .gateway_contract import (
    Enrollment,
    GatewayConfig,
    GatewayError,
    encode,
    number,
    require,
    text,
)
from .runtime_identity import IdentityError, IdentityVerifier, VerifiedIdentity

REQUIRED = ["iss", "aud", "sub", "iat", "nbf", "exp", "jti"]


def thumbprint(key: dict) -> str:
    try:
        return jwk.JWK(**key).thumbprint()
    except (ValueError, TypeError):
        raise GatewayError("invalid-public-key") from None


def verify(token: str, keys: dict, issuer: str, audience: str, *, lifetime: int = 300,
           revoked: tuple[str, ...] = ()) -> dict:
    text(token, 65536)
    try:
        header = jwt.get_unverified_header(token)
        require(set(header) <= {"alg", "kid", "typ"} and header.get("alg") == "EdDSA",
                "unsupported-algorithm")
        kid = text(header.get("kid"))
        require(kid not in revoked, "revoked")
        key = next((item for item in keys["keys"] if item["kid"] == kid), None)
        require(key is not None, "unknown-key")
        claims = jwt.decode(token, jwt.PyJWK.from_dict(key).key, algorithms=["EdDSA"],
                            issuer=issuer, audience=audience, options={"require": REQUIRED,
                                                                    "strict_aud": True})
        now = int(time.time())
        for field in ("iat", "nbf", "exp"):
            number(claims[field], 0)
        require(claims["iat"] <= now < claims["exp"] and claims["nbf"] <= now
                and 0 < claims["exp"] - claims["iat"] <= lifetime, "invalid-time")
        text(claims["sub"])
        text(claims["jti"])
        require(claims["jti"] not in revoked and claims["sub"] not in revoked, "revoked")
        encode(claims)
        return claims
    except (jwt.PyJWTError, ValueError, TypeError, KeyError):
        raise GatewayError("authentication-denied") from None


class Signer:
    def __init__(self, key: Any, kid: str, issuer: str, subject: str):
        self._key = key
        self.kid, self.issuer, self.subject = kid, issuer, subject

    def sign(self, payload: dict, audience: str, ttl: int = 30, *, expires_at: int | None = None) -> str:
        import secrets

        now = int(time.time())
        number(ttl, 1, 300)
        expiry = now + ttl if expires_at is None else min(now + ttl, number(expires_at))
        require(expiry > now, "signing-deadline-expired")
        claims = payload | {"iss": self.issuer, "aud": audience, "sub": self.subject,
                            "iat": now, "nbf": now, "exp": expiry, "jti": secrets.token_hex(16)}
        encode(claims)
        return jwt.encode(claims, self._key, algorithm="EdDSA",
                          headers={"kid": self.kid, "typ": "JWT"})


@dataclass(frozen=True)
class AuthContext:
    peer: Enrollment
    expires_at: int
    identity: VerifiedIdentity | None = None


class Admission:
    def __init__(self, config: GatewayConfig):
        self.config = config
        identity = config.identity
        try:
            self.verifier = IdentityVerifier(
                issuer=identity["issuer"], audience=config.audience,
                jwks={"keys": [key for key in identity["jwks"]["keys"]
                               if key["kid"] not in config.revoked]},
                trust_valid_until=identity["trust_valid_until"], clock=lambda: int(time.time()),
            )
        except IdentityError as exc:
            raise GatewayError(exc.code) from None
        try:
            for key in config.jwks["keys"]:
                jwt.PyJWK.from_dict(key)
            for peer in config.enrollments:
                jwt.PyJWK.from_dict(peer.proof_jwk | {"alg": "ES256"})
                if peer.receipt_jwk is not None:
                    jwt.PyJWK.from_dict(peer.receipt_jwk)
                if peer.recipient_jwk is not None:
                    key = jwk.JWK(**peer.recipient_jwk)
                    public = serialization.load_pem_public_key(key.export_to_pem())
                    require(isinstance(public, RSAPublicKey) and 2048 <= public.key_size <= 4096,
                            "invalid-recipient-key")
        except (jwt.PyJWTError, ValueError, TypeError):
            raise GatewayError("invalid-trust-key") from None
        self._budgets: dict[str | None, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def budget(self, principal: str | None = None) -> None:
        rate, capacity = (128, 256) if principal is None else (16, 32)
        now = time.monotonic()
        with self._lock:
            available, sampled = self._budgets.get(principal, (capacity, now))
            available = min(capacity, available + max(0, now - sampled) * rate)
            require(available >= 1, "admission-unavailable")
            self._budgets[principal] = (available - 1, now)

    def authenticate(self, access: str, proof: str, target_uri: str) -> AuthContext:
        self.budget()
        try:
            identity = self.verifier.verify(access, proof, method="POST", target_uri=target_uri)
        except IdentityError as exc:
            raise GatewayError(exc.code) from None
        config = self.config
        peer = next((e for e in config.enrollments if e.principal == identity.subject), None)
        require(peer is not None and peer.expires_at > time.time(), "enrollment-unavailable")
        require(peer.principal not in config.revoked
                and identity.proof_key == thumbprint(peer.proof_jwk)
                and identity.proof_key not in config.revoked
                and (peer.receipt_jwk is None or thumbprint(peer.receipt_jwk) not in config.revoked)
                and (peer.recipient_jwk is None or thumbprint(peer.recipient_jwk) not in config.revoked),
                "revoked")
        # The exact credential was already signature/type/time verified above.
        claims = jwt.decode(access, options={"verify_signature": False})
        if "jti" in claims:
            text(claims["jti"])
        require(claims.get("jti") not in config.revoked, "revoked")
        self.budget(peer.principal)
        return AuthContext(peer, min(identity.expires_at, peer.expires_at), identity)


def make_proof(key: Any, access: str, *, target_uri: str, nonce: str,
               method: str = "POST") -> str:
    require(method in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"),
            "invalid-proof-method")
    try:
        public = key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        proof_key = jwk.JWK.from_pem(public).export_public(as_dict=True)
        require(proof_key.get("kty") == "EC" and proof_key.get("crv") == "P-256",
                "unsupported-profile")
        token_hash = base64.urlsafe_b64encode(
            hashlib.sha256(access.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        return jwt.encode(
            {"htm": method, "htu": target_uri, "iat": int(time.time()),
             "jti": secrets.token_hex(16), "ath": token_hash, "nonce": text(nonce, 43)},
            key, algorithm="ES256", headers={"typ": "dpop+jwt", "jwk": proof_key},
        )
    except (jwt.PyJWTError, ValueError, TypeError, AttributeError):
        raise GatewayError("invalid-proof-key") from None

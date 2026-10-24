"""Explicit jwt-pop-v1 verification, not enrollment or authorization."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import hmac
import re
import secrets
import threading
from urllib.parse import urlsplit

from .planning import PlanError, canonical, decode

MAX_ACCESS_BYTES = 8192
MAX_PROOF_BYTES = 4096
MAX_KEYS = 16
MAX_REPLAYS = 4096
PROOF_AGE = 60
CLOCK_SKEW = 30
_COMPACT = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\Z")


class IdentityError(ValueError):
    """A value-free refusal category; credentials never appear in diagnostics."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class VerifiedIdentity:
    issuer: str
    subject: str
    audience: str
    proof_key: str
    issued_at: int
    expires_at: int
    verified_at: int
    evidence: str
    profile: str = "jwt-pop-v1"


@dataclass(frozen=True, repr=False)
class _AccessBinding:
    subject: str
    proof_key: str
    expires_at: int


def _text(value, maximum=256):
    return (
        isinstance(value, str) and 0 < len(value) <= maximum
        and all(32 < ord(char) < 127 for char in value)
    )


def _time(value):
    return type(value) is int and 0 <= value < 2**53


def _json(raw):
    try:
        return decode(raw)
    except PlanError:
        raise IdentityError("invalid-credential") from None


def _parts(value, maximum):
    if (not isinstance(value, str) or len(value) > maximum
            or not _COMPACT.fullmatch(value)):
        raise IdentityError("invalid-credential")
    decoded = []
    for part in value.split(".")[:2]:
        try:
            raw = base64.b64decode(part + "=" * (-len(part) % 4),
                                   altchars=b"-_", validate=True)
        except (ValueError, binascii.Error):
            raise IdentityError("invalid-credential") from None
        if base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != part:
            raise IdentityError("invalid-credential")
        item = _json(raw)
        if not isinstance(item, dict):
            raise IdentityError("invalid-credential")
        decoded.append(item)
    return decoded


def _https(value, *, target=False):
    if not _text(value, 2048) or "\\" in value:
        return False
    try:
        uri = urlsplit(value)
        port = uri.port
    except ValueError:
        return False
    return (
        uri.scheme == "https" and bool(uri.hostname) and not uri.username
        and not uri.password and port != 0 and not uri.fragment
        and (not target or ("?" not in value and "#" not in value))
        and (not target or bool(uri.path))
    )


def _jose():
    try:
        from joserfc import jwt
        from joserfc.errors import JoseError
        from joserfc.jwk import ECKey
    except ImportError:
        raise IdentityError("unsupported-profile") from None
    return jwt, JoseError, ECKey


def _public_key(value):
    if (not isinstance(value, dict)
            or not {"kty", "crv", "x", "y"} <= value.keys()
            or value.keys() - {"kty", "crv", "x", "y", "kid", "alg", "use", "key_ops"}
            or value["kty"] != "EC" or value["crv"] != "P-256"
            or value.get("alg", "ES256") != "ES256"
            or value.get("use", "sig") != "sig"
            or value.get("key_ops", ["verify"]) != ["verify"]
            or not _text(value["x"], 43) or len(value["x"]) != 43
            or not _text(value["y"], 43) or len(value["y"]) != 43
            or ("kid" in value and not _text(value["kid"], 128))):
        raise IdentityError("invalid-key")
    _, error, key_type = _jose()
    try:
        return key_type.import_key(value)
    except (error, ValueError, TypeError):
        raise IdentityError("invalid-key") from None


def _verify(value, key):
    jwt, error, _ = _jose()
    try:
        jwt.decode(value, key, algorithms=["ES256"])
    except (error, ValueError, TypeError):
        raise IdentityError("invalid-signature") from None


class IdentityVerifier:
    """One serving owner's bounded proof fence and explicit trusted key snapshot.

    Construct only at the trusted transport boundary. A new instance gets a new
    unpredictable nonce epoch; sharing traffic requires sharing this same owner,
    not independent replicas with a common nonce or independent replay caches.
    """

    _issuer: str
    _audience: str

    def __init__(self, *, issuer, audience, jwks, trust_valid_until, clock,
                 replay_capacity=MAX_REPLAYS):
        if (not _https(issuer) or not _text(audience, 512)
                or not _time(trust_valid_until) or not callable(clock)
                or type(replay_capacity) is not int
                or not 1 <= replay_capacity <= MAX_REPLAYS):
            raise IdentityError("invalid-trust")
        try:
            raw = canonical(jwks)
        except PlanError:
            raise IdentityError("invalid-trust") from None
        if len(raw) > 65536:
            raise IdentityError("invalid-trust")
        snapshot = _json(raw)
        if (not isinstance(snapshot, dict) or set(snapshot) != {"keys"}
                or not isinstance(snapshot["keys"], list)
                or not 1 <= len(snapshot["keys"]) <= MAX_KEYS):
            raise IdentityError("invalid-trust")
        keys = {}
        for value in snapshot["keys"]:
            key = _public_key(value)
            kid = value.get("kid")
            if not _text(kid, 128) or kid in keys:
                raise IdentityError("invalid-trust")
            keys[kid] = key
        self._issuer = issuer
        self._audience = audience
        self._keys = keys
        self._trust_valid_until = trust_valid_until
        self._clock = clock
        self._capacity = replay_capacity
        self._nonce = secrets.token_urlsafe(32)
        self._replays = {}
        self._last_time = 0
        self._lock = threading.Lock()

    @property
    def nonce(self):
        """Server challenge value; expose only on the adopted protected transport."""
        return self._nonce

    def _now_locked(self):
        now = self._clock()
        if not _time(now):
            raise IdentityError("invalid-clock")
        if now < self._last_time:
            raise IdentityError("clock-rollback")
        self._last_time = now
        if now >= self._trust_valid_until:
            raise IdentityError("authority-unavailable")
        return now

    def _audience_matches(self, audience: object) -> bool:
        return audience == self._audience

    def _access_binding(self, claims: Mapping[str, object],
                        kind: str | None) -> _AccessBinding:
        subject, expires = claims.get("sub"), claims.get("exp")
        if not isinstance(subject, str) or type(expires) is not int:
            raise IdentityError("invalid-credential")
        cnf = claims.get("cnf")
        if (not isinstance(cnf, dict) or set(cnf) != {"jkt"}
                or not isinstance(cnf["jkt"], str)
                or not _text(cnf["jkt"], 43) or len(cnf["jkt"]) != 43):
            raise IdentityError("invalid-key-binding")
        return _AccessBinding(subject, cnf["jkt"], expires)

    def _identity(self, claims: Mapping[str, object], binding: _AccessBinding,
                  now: int, evidence: str, kind: str | None) -> VerifiedIdentity:
        issued = claims["iat"]
        assert type(issued) is int
        return VerifiedIdentity(
            issuer=self._issuer, subject=binding.subject, audience=self._audience,
            proof_key=binding.proof_key, issued_at=issued,
            expires_at=binding.expires_at, verified_at=now, evidence=evidence,
        )

    def verify(self, access_token: str, proof: str, *, method: str,
               target_uri: str, kind: str | None = None) -> VerifiedIdentity:
        """Verify one request using server-derived method/HTTPS URI and owned clock.

        The caller must establish encrypted server-authenticated transport and
        canonical external routing before this call, never from request-supplied
        identity objects or untrusted forwarding headers. No network I/O occurs.
        """
        if (not _https(target_uri, target=True)
                or not isinstance(method, str)
                or not re.fullmatch(r"[A-Z]{1,16}", method)):
            raise IdentityError("invalid-request-context")
        with self._lock:
            self._now_locked()
        header, claims = _parts(access_token, MAX_ACCESS_BYTES)
        if (set(header) != {"alg", "typ", "kid"} or header["alg"] != "ES256"
                or header["typ"] != "at+jwt" or not _text(header["kid"], 128)):
            raise IdentityError("invalid-credential")
        key = self._keys.get(header["kid"])
        if key is None:
            raise IdentityError("unknown-key")
        _verify(access_token, key)
        if (claims.get("iss") != self._issuer or not _text(claims.get("sub"))
                or not self._audience_matches(claims.get("aud"))
                or not _time(claims.get("iat")) or not _time(claims.get("exp"))
                or claims["exp"] <= claims["iat"]):
            raise IdentityError("invalid-credential")
        if "nbf" in claims and (
                not _time(claims["nbf"]) or claims["nbf"] >= claims["exp"]):
            raise IdentityError("invalid-credential")
        binding = self._access_binding(claims, kind)
        proof_header, proof_claims = _parts(proof, MAX_PROOF_BYTES)
        if (set(proof_header) != {"alg", "typ", "jwk"}
                or proof_header["alg"] != "ES256"
                or proof_header["typ"] != "dpop+jwt"):
            raise IdentityError("invalid-proof")
        proof_key = _public_key(proof_header["jwk"])
        _verify(proof, proof_key)
        if not hmac.compare_digest(proof_key.thumbprint(), binding.proof_key):
            raise IdentityError("invalid-key-binding")
        required = {"jti", "htm", "htu", "iat", "ath", "nonce"}
        if (set(proof_claims) != required or not _text(proof_claims["jti"], 128)
                or proof_claims["htm"] != method or proof_claims["htu"] != target_uri
                or not _time(proof_claims["iat"])
                or not _text(proof_claims["nonce"], 43)
                or not hmac.compare_digest(proof_claims["nonce"], self._nonce)):
            raise IdentityError("invalid-proof")
        token_hash = base64.urlsafe_b64encode(
            hashlib.sha256(access_token.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        if (not _text(proof_claims["ath"], 43)
                or not hmac.compare_digest(proof_claims["ath"], token_hash)):
            raise IdentityError("invalid-token-binding")
        replay_id = (binding.proof_key, proof_claims["jti"])
        with self._lock:
            now = self._now_locked()
            if now >= binding.expires_at:
                raise IdentityError("expired-credential")
            if (claims["iat"] > now + CLOCK_SKEW
                    or claims.get("nbf", 0) > now + CLOCK_SKEW):
                raise IdentityError("invalid-credential")
            if not now - PROOF_AGE - CLOCK_SKEW <= proof_claims["iat"] <= now + CLOCK_SKEW:
                raise IdentityError("invalid-proof")
            self._replays = {
                key: expiry for key, expiry in self._replays.items() if expiry >= now
            }
            if replay_id in self._replays:
                raise IdentityError("proof-replayed")
            if len(self._replays) >= self._capacity:
                raise IdentityError("resource-limit")
            self._replays[replay_id] = proof_claims["iat"] + PROOF_AGE + CLOCK_SKEW
        return self._identity(
            claims, binding, now, hashlib.sha256(proof.encode("ascii")).hexdigest(), kind)

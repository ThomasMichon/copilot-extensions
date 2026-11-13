"""Explicit caller-only RS256 bearer verification; never proof of possession."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable
from dataclasses import dataclass, field
import re
import secrets
from threading import Lock

from . import planning
from .runtime_identity import (
    CLOCK_SKEW, MAX_ACCESS_BYTES, MAX_KEYS, IdentityError, _https, _parts, _text, _time,
)

PROFILE = "user-bearer-v1"
MAX_CLAIMS = 16
MAX_VALUES = 64
MAX_MAPPINGS = 128
_CLAIM = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,63}\Z")


@dataclass(frozen=True)
class BearerIdentity:
    """Canonical mapped subject only; no token, raw claims or pretend proof key.

    Evidence identifies this verification event, not a replay-proof credential.
    """

    issuer: str
    subject: str
    audience: str
    issued_at: int
    expires_at: int
    verified_at: int
    evidence: str
    profile: str = PROFILE


@dataclass(frozen=True)
class ClaimRequirement:
    claim: str
    values: tuple[str, ...] = field(repr=False)
    shape: str | None = None


@dataclass(frozen=True)
class PrincipalMapping:
    subject: str = field(repr=False)
    principal: str


def _claim(value: object) -> bool:
    return isinstance(value, str) and _CLAIM.fullmatch(value) is not None


def _values(value: object) -> bool:
    return (
        type(value) is tuple and 1 <= len(value) <= MAX_VALUES
        and all(_text(item) for item in value) and len(set(value)) == len(value)
    )


def _binary(value: object, maximum: int) -> bytes:
    if not _text(value, maximum):
        raise IdentityError("invalid-key")
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4),
                               altchars=b"-_", validate=True)
    except (ValueError, binascii.Error):
        raise IdentityError("invalid-key") from None
    if base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != value:
        raise IdentityError("invalid-key")
    return raw


def _thumbprint_metadata(name: str, value: object) -> None:
    if len(_binary(value, 64)) != (20 if name == "x5t" else 32):
        raise IdentityError("invalid-key")


def _jose():
    try:
        from joserfc import jwt
        from joserfc.errors import JoseError
        from joserfc.jwk import RSAKey
    except ImportError:
        raise IdentityError("unsupported-profile") from None
    return jwt, JoseError, RSAKey


def _key(value: object):
    allowed = {"kty", "n", "e", "kid", "alg", "use", "key_ops", "x5t", "x5t#S256"}
    if (not isinstance(value, dict) or not {"kty", "n", "e", "kid"} <= value.keys()
            or value.keys() - allowed or value["kty"] != "RSA"
            or value.get("alg", "RS256") != "RS256"
            or value.get("use", "sig") != "sig"
            or value.get("key_ops", ["verify"]) != ["verify"]
            or not _text(value["kid"], 128)):
        raise IdentityError("invalid-key")
    n, e = _binary(value["n"], 683), _binary(value["e"], 8)
    modulus, exponent = int.from_bytes(n, "big"), int.from_bytes(e, "big")
    if (not n or n[0] == 0 or not e or e[0] == 0
            or not 2048 <= modulus.bit_length() <= 4096
            or not 65537 <= exponent < 2**32 or exponent % 2 == 0):
        raise IdentityError("invalid-key")
    for name in ("x5t", "x5t#S256"):
        if name in value:
            _thumbprint_metadata(name, value[name])
    _, error, key_type = _jose()
    try:
        key = key_type.import_key(value)
        key.get_op_key("verify")
        return key
    except (error, ValueError, TypeError):
        raise IdentityError("invalid-key") from None


def _requirements_valid(required: object) -> bool:
    return (
        type(required) is tuple and len(required) <= MAX_CLAIMS
        and all(type(item) is ClaimRequirement and _claim(item.claim)
                and (item.shape is None or (
                    type(item.shape) is str and item.shape in ("scalar", "list")))
                and _values(item.values) for item in required)
        and len({item.claim for item in required}) == len(required)
    )


def _check_requirements(claims, required: tuple[ClaimRequirement, ...]) -> None:
    for item in required:
        actual = claims.get(item.claim)
        if ((item.shape == "scalar" and type(actual) is not str)
                or (item.shape == "list" and type(actual) is not list)):
            raise IdentityError("required-claim-denied")
        if isinstance(actual, str):
            values = (actual,)
        elif isinstance(actual, list):
            values = tuple(actual)
        else:
            raise IdentityError("required-claim-denied")
        if not _values(values) or not any(value in item.values for value in values):
            raise IdentityError("required-claim-denied")


def _rs256_claims(access_token: str, keys) -> dict[str, object]:
    header, claims = _parts(access_token, MAX_ACCESS_BYTES)
    if (not {"alg", "kid"} <= header.keys()
            or header.keys() - {"alg", "typ", "kid", "x5t", "x5t#S256"}
            or header["alg"] != "RS256" or header.get("typ", "JWT") != "JWT"
            or not _text(header["kid"], 128)):
        raise IdentityError("invalid-credential")
    for name in ("x5t", "x5t#S256"):
        if name in header:
            _thumbprint_metadata(name, header[name])
    key = keys.get(header["kid"])
    if key is None:
        raise IdentityError("unknown-key")
    jwt, error, _ = _jose()
    try:
        jwt.decode(access_token, key, algorithms=["RS256"])
    except (error, ValueError, TypeError):
        raise IdentityError("invalid-signature") from None
    return claims


class BearerIdentityVerifier:
    """Pure pinned public trust and claim policy; real signature on every request.

    The trusted owner must establish TLS and authenticated current policy before
    using this internal SDK. No acquisition, discovery, cookies or network I/O.
    """

    def __init__(
        self, *, issuer: str, audience: str, jwks: object, trust_valid_until: int,
        clock: Callable[[], int], principal_claim: str,
        required_claims: tuple[ClaimRequirement, ...], mappings: tuple[PrincipalMapping, ...],
    ) -> None:
        if (not _https(issuer) or not _text(audience, 512)
                or not _time(trust_valid_until) or not callable(clock)
                or not _claim(principal_claim)
                or not _requirements_valid(required_claims)
                or type(mappings) is not tuple or len(mappings) > MAX_MAPPINGS
                or any(type(item) is not PrincipalMapping or not _text(item.subject)
                       for item in mappings)
                or len({item.subject for item in mappings}) != len(mappings)):
            raise IdentityError("invalid-trust")
        try:
            for item in mappings:
                planning.identifier(item.principal, "principal")
            raw = planning.canonical(jwks)
            snapshot = planning.decode(raw)
        except planning.PlanError:
            raise IdentityError("invalid-trust") from None
        if (len(raw) > 65536 or not isinstance(snapshot, dict) or set(snapshot) != {"keys"}
                or not isinstance(snapshot["keys"], list)
                or not 1 <= len(snapshot["keys"]) <= MAX_KEYS):
            raise IdentityError("invalid-trust")
        keys = {}
        for value in snapshot["keys"]:
            key = _key(value)
            if value["kid"] in keys:
                raise IdentityError("invalid-trust")
            keys[value["kid"]] = key
        self._issuer, self._audience, self._keys = issuer, audience, keys
        self._expires, self._clock = trust_valid_until, clock
        self._claim, self._required, self._mappings = principal_claim, required_claims, mappings
        self._last_time = 0
        self._lock = Lock()

    def _now(self) -> int:
        with self._lock:
            now = self._clock()
            if not _time(now):
                raise IdentityError("invalid-clock")
            if now < self._last_time:
                raise IdentityError("clock-rollback")
            self._last_time = now
            if now >= self._expires:
                raise IdentityError("authority-unavailable")
            return now

    def verify(self, access_token: str, *, method: str, target_uri: str,
               kind: str) -> BearerIdentity:
        """Verify the original token for explicit caller use, never service use."""
        if kind != "caller":
            raise IdentityError("unsupported-profile")
        if not _https(target_uri, target=True) or not isinstance(method, str) \
                or re.fullmatch(r"[A-Z]{1,16}", method) is None:
            raise IdentityError("invalid-request-context")
        self._now()
        claims = _rs256_claims(access_token, self._keys)
        if (claims.get("iss") != self._issuer or claims.get("aud") != self._audience
                or not _time(claims.get("iat")) or not _time(claims.get("exp"))
                or claims["exp"] <= claims["iat"] or "cnf" in claims):
            raise IdentityError("invalid-credential")
        if "nbf" in claims and (
                not _time(claims["nbf"]) or claims["nbf"] >= claims["exp"]):
            raise IdentityError("invalid-credential")
        _check_requirements(claims, self._required)
        subject = claims.get(self._claim)
        if not _text(subject):
            raise IdentityError("unmapped-user")
        mapping = next((item for item in self._mappings if item.subject == subject), None)
        if mapping is None:
            raise IdentityError("unmapped-user")
        now = self._now()
        if now >= claims["exp"]:
            raise IdentityError("expired-credential")
        if claims["iat"] > now + CLOCK_SKEW or claims.get("nbf", 0) > now + CLOCK_SKEW:
            raise IdentityError("invalid-credential")
        return BearerIdentity(
            self._issuer, mapping.principal, self._audience,
            claims["iat"], claims["exp"], now, secrets.token_hex(32))

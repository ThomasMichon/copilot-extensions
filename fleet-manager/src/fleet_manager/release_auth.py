"""Explicit release authentication; never installation or activation authority."""

import base64
import binascii
import hashlib

from .planning import PlanError, boolean, canonical, decode, identifier, integer, record
from .release import ROLES, validate_release

SIGNING_DOMAIN = b"fleet-manager.release-signature.v1\0"
METHODS = ("pinned-key", "github-attestation")


def _fields(value, fields, label):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise PlanError(f"invalid {label} fields")


def _binary(value, size, label):
    if not isinstance(value, str) or len(value) != 4 * ((size + 2) // 3):
        raise PlanError(f"invalid {label} encoding")
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise PlanError(f"invalid {label} encoding") from None
    if len(raw) != size or base64.b64encode(raw).decode("ascii") != value:
        raise PlanError(f"invalid {label} encoding")
    return raw


def _window(value, label):
    integer(value["valid_from"], f"{label} start")
    integer(value["valid_until"], f"{label} expiry", minimum=value["valid_from"] + 1)


def _policy(value, *, authority, installation, role, now):
    record(value, "fleet-manager.release-trust-policy",
           {"id", "revision", "trust_revision", "method", "authority", "installation",
            "role", "valid_from", "valid_until", "minimum_sequence", "keys"})
    for field in ("id", "revision", "trust_revision", "authority", "installation"):
        identifier(value[field], f"policy {field}")
    if value["method"] not in METHODS:
        raise PlanError("unsupported required release authentication method")
    if (value["authority"], value["installation"], value["role"]) != (
            authority, installation, role):
        raise PlanError("release trust policy scope mismatch")
    _window(value, "policy")
    if not value["valid_from"] <= now < value["valid_until"]:
        raise PlanError("release trust policy is not currently valid")
    integer(value["minimum_sequence"], "minimum release sequence", minimum=1)
    keys = value["keys"]
    if not isinstance(keys, list) or not keys or len(keys) > 256:
        raise PlanError("signing keys must be a nonempty bounded list")
    seen = set()
    for key in keys:
        _fields(key, ("id", "public_key", "roles", "valid_from", "valid_until", "revoked"),
                "signing key")
        identifier(key["id"], "signing key id")
        if key["id"] in seen:
            raise PlanError("duplicate signing key identity")
        seen.add(key["id"])
        _binary(key["public_key"], 32, "Ed25519 public key")
        roles = key["roles"]
        if (not isinstance(roles, list) or not roles or len(roles) > len(ROLES)
                or any(item not in ROLES for item in roles) or len(set(roles)) != len(roles)):
            raise PlanError("invalid signing key role scope")
        _window(key, "signing key")
        boolean(key["revoked"], "signing key revocation")


def _verify_pinned(manifest_digest, proof, policy, *, role, now):
    record(proof, "fleet-manager.release-proof", {"method", "payload", "signature"})
    if proof["method"] != policy["method"]:
        raise PlanError("proof does not match required authentication method")
    payload = proof["payload"]
    record(payload, "fleet-manager.release-signature",
           {"key_id", "authority", "release_digest", "sequence", "issued_at", "expires_at"})
    identifier(payload["key_id"], "proof key id")
    identifier(payload["authority"], "proof authority")
    if payload["authority"] != policy["authority"]:
        raise PlanError("signed release authority mismatch")
    if payload["release_digest"] != manifest_digest:
        raise PlanError("signed manifest digest mismatch")
    integer(payload["sequence"], "signed release sequence", minimum=1)
    if payload["sequence"] < policy["minimum_sequence"]:
        raise PlanError("signed release is below configured sequence floor")
    integer(payload["issued_at"], "signature issue time")
    integer(payload["expires_at"], "signature expiry", minimum=payload["issued_at"] + 1)
    if not payload["issued_at"] <= now < payload["expires_at"]:
        raise PlanError("release signature is not currently valid")
    key = next((key for key in policy["keys"] if key["id"] == payload["key_id"]), None)
    if key is None:
        raise PlanError("release signing key is not configured")
    if key["revoked"] or role not in key["roles"]:
        raise PlanError("release signing key is revoked or outside role scope")
    if (payload["issued_at"] < key["valid_from"]
            or payload["expires_at"] > key["valid_until"]):
        raise PlanError("release signature exceeds signing key validity")
    public_key = _binary(key["public_key"], 32, "Ed25519 public key")
    signature = _binary(proof["signature"], 64, "Ed25519 signature")
    try:
        from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:
        raise PlanError("selected pinned-key verification capability is unavailable") from None
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature, SIGNING_DOMAIN + canonical(payload))
    except InvalidSignature:
        raise PlanError("release signature verification failed") from None
    except UnsupportedAlgorithm:
        raise PlanError("selected Ed25519 verification is unsupported") from None
    return {
        "signer_key": key["id"], "signer_key_sha256": hashlib.sha256(public_key).hexdigest(),
        "sequence": payload["sequence"], "issued_at": payload["issued_at"],
        "valid_until": min(payload["expires_at"], policy["valid_until"]),
    }


def authenticate_release(manifest_bytes, proof, policy, *, authority, installation, role, now):
    """Authenticate canonical bytes under explicit trusted policy, without side effects."""
    identifier(authority, "expected authority")
    identifier(installation, "expected installation")
    if role not in ROLES:
        raise PlanError("unsupported installation role")
    integer(now, "verification time")
    manifest = decode(manifest_bytes)
    validate_release(manifest)
    if canonical(manifest) != manifest_bytes:
        raise PlanError("release authentication requires exact canonical manifest bytes")
    policy = decode(canonical(policy))
    proof = decode(canonical(proof))
    # GitHub verification must not accept the pinned-key policy shape or a fake result.
    if isinstance(policy, dict) and policy.get("method") == "github-attestation":
        raise PlanError("selected GitHub attestation verification capability is not implemented")
    _policy(policy, authority=authority, installation=installation, role=role, now=now)
    manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
    evidence = _verify_pinned(manifest_digest, proof, policy, role=role, now=now)
    result = {
        "schema": "fleet-manager.release-authentication", "version": 1,
        "status": "authenticated", "method": policy["method"],
        "policy": policy["id"], "policy_revision": policy["revision"],
        "trust_revision": policy["trust_revision"],
        "policy_digest": hashlib.sha256(canonical(policy)).hexdigest(),
        "authority": authority, "installation": installation, "role": role,
        "release": manifest["id"], "release_digest": manifest_digest,
        "verified_at": now, **evidence, "authenticity_verified": True,
        "artifact_integrity_verified": False, "activation_authorized": False,
        "live_readiness_verified": False, "mutations_performed": False,
    }
    canonical(result)
    return result

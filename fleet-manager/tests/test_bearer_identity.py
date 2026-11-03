"""Original synthetic RSA signatures, not provider login or live trust qualification."""

import base64
import copy
from dataclasses import FrozenInstanceError
import json

from joserfc import jwt
from joserfc.jwk import RSAKey
import pytest

from fleet_manager.bearer_identity import (
    PROFILE, BearerIdentity, BearerIdentityVerifier, ClaimRequirement, PrincipalMapping,
)
from fleet_manager.runtime_identity import IdentityError, VerifiedIdentity
from test_runtime_identity import AUDIENCE, ISSUER, NOW, TARGET, Clock


@pytest.fixture(scope="module")
def rsa_keys():
    return RSAKey.generate_key(2048), RSAKey.generate_key(2048)


def public_jwks(key):
    return {"keys": [{**key.as_dict(private=False), "kid": "issuer-rsa"}]}


def user_verifier(key, *, clock=None, **changes):
    return BearerIdentityVerifier(**{
        "issuer": ISSUER, "audience": AUDIENCE, "jwks": public_jwks(key),
        "trust_valid_until": NOW + 600, "clock": clock or Clock(),
        "principal_claim": "user_id",
        "required_claims": (
            ClaimRequirement("realm", ("realm-a",)), ClaimRequirement("roles", ("gateway-user",))),
        "mappings": (PrincipalMapping("issuer-user-a", "user-a"),),
        **changes,
    })


def token(key, *, now=NOW, header=None, claims=None):
    return jwt.encode(
        {"alg": "RS256", "typ": "JWT", "kid": "issuer-rsa", **(header or {})},
        {"iss": ISSUER, "aud": AUDIENCE, "iat": now, "exp": now + 300,
         "sub": "not-the-policy-selected-id", "user_id": "issuer-user-a", "realm": "realm-a",
         "roles": ["other", "gateway-user"], **(claims or {})}, key)


def verify(verifier, access, **changes):
    return verifier.verify(access, **{
        "method": "POST", "target_uri": TARGET, "kind": "caller", **changes})


def rewrite(access, section, **changes):
    parts = access.split(".")
    value = json.loads(base64.urlsafe_b64decode(parts[section] + "=" * (-len(parts[section]) % 4)))
    value.update(changes)
    parts[section] = base64.urlsafe_b64encode(
        json.dumps(value, separators=(",", ":")).encode()).rstrip(b"=").decode()
    return ".".join(parts)


def test_real_signature_typed_canonical_subject_and_per_request_evidence(rsa_keys):
    access = token(rsa_keys[0])
    verifier = user_verifier(rsa_keys[0])
    first, second = verify(verifier, access), verify(verifier, access)
    assert type(first) is BearerIdentity and not isinstance(first, VerifiedIdentity)
    assert first.subject == "user-a" and first.issuer == ISSUER and first.audience == AUDIENCE
    assert first.profile == PROFILE and first.evidence != second.evidence
    assert not hasattr(first, "proof_key")
    assert access not in repr(first) and "issuer-user-a" not in repr(first)
    with pytest.raises(FrozenInstanceError):
        first.subject = "another-user"
    # A stolen bearer can replay until expiry; this is intentionally NOT a PoP test.
    assert verify(verifier, access).subject == first.subject
    with pytest.raises(IdentityError, match="invalid-signature"):
        verify(verifier, rewrite(access, 1, user_id="another-user"))


@pytest.mark.parametrize("change", [
    {"iss": "https://foreign.example"}, {"aud": "foreign"},
    {"aud": [AUDIENCE]}, {"realm": "foreign"}, {"realm": {}},
    {"roles": ["ungranted"]}, {"roles": []}, {"roles": ["gateway-user"] * 2},
    {"roles": [False]}, {"roles": ["gateway-user"] + ["x"] * 64},
    {"user_id": "unknown"}, {"user_id": None}, {"exp": NOW},
    {"exp": True}, {"iat": NOW + 31}, {"nbf": NOW + 31},
    {"nbf": NOW + 300}, {"cnf": {"jkt": "not-bearer"}},
])
def test_signed_invalid_claims_and_mapping_are_value_free(rsa_keys, change):
    access = token(rsa_keys[0], claims=change)
    with pytest.raises(IdentityError) as caught:
        verify(user_verifier(rsa_keys[0]), access)
    assert access not in str(caught.value) and str(caught.value) == caught.value.code


@pytest.mark.parametrize("change", [
    {"alg": "HS256"}, {"alg": "none"}, {"alg": "RS512"}, {"kid": "unknown"},
    {"kid": "x" * 129}, {"typ": "dpop+jwt"}, {"jku": "https://keys.example"},
    {"x5u": "https://keys.example"}, {"jwk": {}}, {"crit": ["arbitrary"]},
    {"x5t": "malformed"},
])
def test_token_cannot_choose_algorithms_keys_or_remote_discovery(rsa_keys, change):
    access = rewrite(token(rsa_keys[0]), 0, **change)
    with pytest.raises(IdentityError):
        verify(user_verifier(rsa_keys[0]), access)


def test_wrong_signature_missing_claims_and_optional_public_header_metadata(rsa_keys):
    verifier = user_verifier(rsa_keys[0])
    with pytest.raises(IdentityError, match="invalid-signature"):
        verify(verifier, token(rsa_keys[1]))
    for name in ("realm", "roles", "user_id", "iat", "exp", "iss", "aud"):
        access = jwt.encode(
            {"alg": "RS256", "typ": "JWT", "kid": "issuer-rsa"},
            {key: value for key, value in {
                "iss": ISSUER, "aud": AUDIENCE, "iat": NOW, "exp": NOW + 300,
                "realm": "realm-a", "roles": ["gateway-user"], "user_id": "issuer-user-a",
            }.items() if key != name}, rsa_keys[0])
        with pytest.raises(IdentityError):
            verify(verifier, access)
    thumbprint = base64.urlsafe_b64encode(b"x" * 20).rstrip(b"=").decode()
    assert verify(verifier, token(rsa_keys[0], header={"x5t": thumbprint})).subject == "user-a"
    assert verify(verifier, token(rsa_keys[0], claims={"roles": "gateway-user"})).subject == "user-a"


def test_immutable_public_keys_and_policy_current_trust_and_request_kind(rsa_keys):
    jwks = public_jwks(rsa_keys[0])
    clock = Clock()
    verifier = user_verifier(rsa_keys[0], jwks=jwks, clock=clock)
    jwks["keys"].clear()
    assert verify(verifier, token(rsa_keys[0])).subject == "user-a"
    with pytest.raises(IdentityError, match="unsupported-profile"):
        verify(verifier, token(rsa_keys[0]), kind="service")
    with pytest.raises(IdentityError, match="invalid-request-context"):
        verify(verifier, token(rsa_keys[0]), target_uri="http://gateway.example/api")
    clock.value = NOW + 600
    with pytest.raises(IdentityError, match="authority-unavailable"):
        verify(verifier, token(rsa_keys[0]))
    clock.value = NOW
    with pytest.raises(IdentityError, match="clock-rollback"):
        verify(verifier, token(rsa_keys[0]))
    for changes in (
        {"jwks": {"keys": [{**rsa_keys[0].as_dict(private=True), "kid": "private"}]}},
        {"mappings": (PrincipalMapping("issuer-user-a", "user-a"),) * 2},
        {"required_claims": (ClaimRequirement("roles", ("gateway-user",)),) * 2},
        {"required_claims": (ClaimRequirement("roles", ("gateway-user",) * 2),)},
    ):
        with pytest.raises(IdentityError):
            user_verifier(rsa_keys[0], **copy.deepcopy(changes))

"""Original application tokens and independent enrollment use real JOSE."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
import base64
import hashlib
import json

from joserfc import jwt
from joserfc.jwk import ECKey
import pytest

from fleet_manager.enrolled_identity import (
    EnrolledBinding, EnrolledIdentity, EnrolledIdentityVerifier,
)
from fleet_manager.runtime_identity import IdentityError
from test_runtime_identity import AUDIENCE, ISSUER, NOW, TARGET, Clock


def application_pair(keys, nonce, *, now=NOW, access=None, proof=None,
                     access_header=None, proof_header=None, signer=None, proof_signer=None):
    """Sign the original application token, without adding a cnf claim."""
    token = jwt.encode(
        {"alg": "ES256", "typ": "at+jwt", "kid": "issuer-1", **(access_header or {})},
        {"iss": ISSUER, "sub": "issuer-subject", "client_id": "application-client",
         "aud": AUDIENCE, "iat": now, "exp": now + 3600, **(access or {})},
        signer or keys[0])
    token_hash = base64.urlsafe_b64encode(hashlib.sha256(token.encode("ascii")).digest()
                                         ).rstrip(b"=").decode("ascii")
    attestation = jwt.encode(
        {"alg": "ES256", "typ": "dpop+jwt", "jwk": keys[1].as_dict(private=False),
         **(proof_header or {})},
        {"jti": "proof-1", "htm": "GET", "htu": TARGET, "iat": now,
         "ath": token_hash, "nonce": nonce, **(proof or {})},
        proof_signer or keys[1])
    return token, attestation


@pytest.fixture
def keys():
    return ECKey.generate_key("P-256"), ECKey.generate_key("P-256")


def binding(keys):
    return EnrolledBinding(
        "issuer-subject", "application-client", "canonical-principal", "caller",
        AUDIENCE, "source-a", keys[1].thumbprint(), NOW + 600)


def verifier(keys, **kwargs):
    return EnrolledIdentityVerifier(
        issuer=ISSUER, audience=AUDIENCE,
        jwks={"keys": [{**keys[0].as_dict(private=False), "kid": "issuer-1"}]},
        trust_valid_until=NOW + 10000, clock=kwargs.pop("clock", Clock()),
        allowed_audiences=kwargs.pop("allowed_audiences", (AUDIENCE, "another-resource")),
        enrollments=kwargs.pop("enrollments", (binding(keys),)), **kwargs)


def check(owner, credentials, *, kind="caller", method="GET", target_uri=TARGET):
    return owner.verify(*credentials, method=method, target_uri=target_uri, kind=kind)


def refuses(owner, credentials, code, **kwargs):
    with pytest.raises(IdentityError, match=f"^{code}$") as caught:
        check(owner, credentials, **kwargs)
    assert caught.value.code == code
    assert all(value not in repr(caught.value) for value in credentials)


@pytest.mark.parametrize("audience", [AUDIENCE, [AUDIENCE], [AUDIENCE, "another-resource"],
                                     ["another-resource", AUDIENCE]])
def test_original_token_maps_exact_application_to_canonical_identity(keys, audience):
    owner = verifier(keys)
    credentials = application_pair(keys, owner.nonce, access={"aud": audience})
    token = credentials[0]
    original = json.loads(base64.urlsafe_b64decode(
        token.split(".")[1] + "=" * (-len(token.split(".")[1]) % 4)))
    assert "cnf" not in original and original["sub"] != "canonical-principal"
    identity = check(owner, credentials)
    assert type(identity) is EnrolledIdentity
    assert (identity.subject, identity.audience, identity.profile, identity.kind) == (
        "canonical-principal", AUDIENCE, "enrolled-pop-v1", "caller")
    assert identity.proof_key == keys[1].thumbprint()
    assert identity.expires_at == NOW + 600
    assert credentials[0] == token
    assert all(value not in repr(identity) for value in credentials)
    with pytest.raises(FrozenInstanceError):
        identity.kind = "service"
    with pytest.raises(FrozenInstanceError):
        binding(keys).principal = "replacement"
    assert "issuer-subject" not in repr(binding(keys))


@pytest.mark.parametrize("claims,code", [
    ({"iss": "https://other.example"}, "invalid-credential"),
    ({"sub": ""}, "invalid-credential"),
    ({"sub": ["issuer-subject"]}, "invalid-credential"),
    ({"sub": "unknown"}, "invalid-key-binding"),
    ({"sub": "canonical-principal"}, "invalid-key-binding"),
    ({"client_id": None}, "invalid-credential"),
    ({"client_id": []}, "invalid-credential"),
    ({"client_id": ""}, "invalid-credential"),
    ({"client_id": "x" * 257}, "invalid-credential"),
    ({"client_id": "other-client"}, "invalid-key-binding"),
    ({"aud": []}, "invalid-credential"),
    ({"aud": [AUDIENCE, AUDIENCE]}, "invalid-credential"),
    ({"aud": [AUDIENCE, "unapproved-resource"]}, "invalid-credential"),
    ({"aud": ["another-resource"]}, "invalid-credential"),
    ({"aud": [AUDIENCE, ""]}, "invalid-credential"),
    ({"aud": [AUDIENCE, {}]}, "invalid-credential"),
    ({"aud": [AUDIENCE] * 17}, "invalid-credential"),
    ({"aud": "x" * 513}, "invalid-credential"),
    ({"aud": None}, "invalid-credential"),
    ({"exp": NOW}, "expired-credential"),
    ({"exp": True}, "invalid-credential"),
    ({"exp": NOW - 100}, "invalid-credential"),
    ({"iat": NOW + 31}, "invalid-credential"),
    ({"iat": 1.1}, "invalid-credential"),
    ({"nbf": NOW + 31}, "invalid-credential"),
    ({"nbf": NOW + 3600}, "invalid-credential"),
    ({"cnf": None}, "invalid-key-binding"),
    ({"cnf": {}}, "invalid-key-binding"),
    ({"cnf": {"jkt": "x" * 43}}, "invalid-key-binding"),
    ({"cnf": {"jkt": "x" * 43, "other": True}}, "invalid-key-binding"),
])
def test_access_refusals(keys, claims, code):
    owner = verifier(keys)
    refuses(owner, application_pair(
        keys, owner.nonce, access={"iat": NOW - 100, **claims}), code)


def test_missing_client_is_not_guessed_and_matching_cnf_is_permitted(keys):
    owner = verifier(keys)
    token, _ = application_pair(keys, owner.nonce)
    claims = json.loads(base64.urlsafe_b64decode(
        token.split(".")[1] + "=" * (-len(token.split(".")[1]) % 4)))
    del claims["client_id"]
    token = jwt.encode({"alg": "ES256", "typ": "at+jwt", "kid": "issuer-1"}, claims, keys[0])
    refuses(owner, (token, application_pair(keys, owner.nonce)[1]), "invalid-credential")
    identity = check(owner, application_pair(
        keys, owner.nonce, access={"cnf": {"jkt": keys[1].thumbprint()}}))
    assert identity.subject == "canonical-principal"


@pytest.mark.parametrize("kind", [None, "", "service", [], "administrator"])
def test_kind_is_trusted_admission_context_not_token_authority(keys, kind):
    owner = verifier(keys)
    code = "invalid-key-binding" if kind == "service" else "invalid-request-context"
    refuses(owner, application_pair(
        keys, owner.nonce, access={"kind": kind, "roles": ["administrator"]}), code, kind=kind)


def test_independent_proof_key_never_enrolls_itself(keys):
    owner = verifier(keys)
    attacker = ECKey.generate_key("P-256")
    for access in ({}, {"cnf": {"jkt": attacker.thumbprint()}}):
        refuses(owner, application_pair((keys[0], attacker), owner.nonce, access=access),
                "invalid-key-binding")
    assert check(owner, application_pair(keys, owner.nonce)).subject == "canonical-principal"


def test_producer_principal_and_kind_claims_are_not_mapping_authority(keys):
    owner = verifier(keys)
    credentials = application_pair(keys, owner.nonce, access={
        "principal": "different-principal", "principal_kind": "service", "kind": "service",
    })
    identity = check(owner, credentials)
    assert identity.subject == "canonical-principal" and identity.kind == "caller"
    refuses(owner, credentials, "invalid-key-binding", kind="service")


@pytest.mark.parametrize("header", [
    {"typ": "JWT"}, {"jku": "https://untrusted.example/keys"}, {"jwk": {}},
])
def test_header_refusals(keys, header):
    owner = verifier(keys)
    refuses(owner, application_pair(keys, owner.nonce, access_header=header),
            "invalid-credential")


@pytest.mark.parametrize("raw", [
    b'{"alg":"none","typ":"at+jwt","kid":"issuer-1"}',
    b'{"alg":"HS256","typ":"at+jwt","kid":"issuer-1"}',
    b'{"alg":"ES256","alg":"none","typ":"at+jwt","kid":"issuer-1"}',
])
def test_no_algorithm_or_duplicate_json_bypass(keys, raw):
    owner = verifier(keys)
    token, proof = application_pair(keys, owner.nonce)
    head = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    refuses(owner, (head + "." + ".".join(token.split(".")[1:]), proof), "invalid-credential")


def test_both_signatures_are_mandatory(keys):
    owner = verifier(keys)
    other = ECKey.generate_key("P-256")
    refuses(owner, application_pair(keys, owner.nonce, signer=other), "invalid-signature")
    refuses(owner, application_pair(keys, owner.nonce, proof_signer=other), "invalid-signature")
    refuses(owner, application_pair(keys, owner.nonce, access_header={"kid": "unknown"}),
            "unknown-key")
    refuses(owner, application_pair(keys, owner.nonce, proof_header={"typ": "JWT"}),
            "invalid-proof")


@pytest.mark.parametrize("claims,code", [
    ({"nonce": "old"}, "invalid-proof"), ({"htm": "POST"}, "invalid-proof"),
    ({"htu": TARGET + "/other"}, "invalid-proof"),
    ({"ath": "x" * 43}, "invalid-token-binding"),
    ({"iat": NOW - 91}, "invalid-proof"), ({"iat": NOW + 31}, "invalid-proof"),
    ({"jti": ""}, "invalid-proof"), ({"extra": True}, "invalid-proof"),
])
def test_common_request_proof_checks(keys, claims, code):
    owner = verifier(keys)
    refuses(owner, application_pair(keys, owner.nonce, proof=claims), code)


def test_original_token_hash_cannot_be_replaced_by_an_equivalent_token(keys):
    owner = verifier(keys)
    token, proof = application_pair(keys, owner.nonce)
    changed, _ = application_pair(keys, owner.nonce, access={"exp": NOW + 7200})
    assert changed != token
    refuses(owner, (changed, proof), "invalid-token-binding")


def test_atomic_shared_replay_capacity_and_clock_fence(keys):
    owner = verifier(keys, replay_capacity=1)
    credentials = application_pair(keys, owner.nonce)

    def attempt(_):
        try:
            return check(owner, credentials).subject
        except IdentityError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))
    assert results.count("canonical-principal") == 1
    assert results.count("proof-replayed") == 7
    refuses(owner, application_pair(keys, owner.nonce, proof={"jti": "next"}), "resource-limit")
    owner._clock.value = NOW + 91
    assert check(owner, application_pair(keys, owner.nonce, now=NOW + 91)).verified_at == NOW + 91
    owner._clock.value = NOW + 90
    refuses(owner, application_pair(keys, owner.nonce), "clock-rollback")


def test_replay_fence_spans_application_clients_and_admission_kinds(keys):
    first = binding(keys)
    owner = verifier(keys, enrollments=(
        first, replace(first, client_id="second-client"),
        replace(first, kind="service"),
    ))
    check(owner, application_pair(keys, owner.nonce))
    refuses(owner, application_pair(
        keys, owner.nonce, access={"client_id": "second-client"}), "proof-replayed")
    refuses(owner, application_pair(keys, owner.nonce), "proof-replayed", kind="service")


def test_shorter_original_token_expiry_is_not_extended_by_enrollment(keys):
    owner = verifier(keys)
    identity = check(owner, application_pair(keys, owner.nonce, access={"exp": NOW + 2}))
    assert identity.expires_at == NOW + 2
    owner._clock.value = NOW + 2
    refuses(owner, application_pair(keys, owner.nonce, access={"exp": NOW + 2},
                                     proof={"jti": "next"}), "expired-credential")


@pytest.mark.parametrize("expiry", [NOW, NOW + 1])
def test_expired_enrollment_refuses_without_extending_token_lifetime(keys, expiry):
    owner = verifier(keys, enrollments=(replace(binding(keys), expires_at=expiry),))
    credentials = application_pair(keys, owner.nonce)
    if expiry > NOW:
        assert check(owner, credentials).expires_at == expiry
    owner._clock.value = expiry
    refuses(owner, application_pair(keys, owner.nonce, proof={"jti": "new"}), "expired-credential")


def test_mapping_expiry_rechecked_after_real_crypto(keys, monkeypatch):
    from fleet_manager import runtime_identity

    owner = verifier(keys, enrollments=(replace(binding(keys), expires_at=NOW + 1),))
    real = runtime_identity._verify

    def advance(token, key):
        real(token, key)
        owner._clock.value = NOW + 1

    monkeypatch.setattr(runtime_identity, "_verify", advance)
    refuses(owner, application_pair(keys, owner.nonce), "expired-credential")
    assert not owner._replays


def test_invalid_and_ambiguous_immutable_enrollments(keys):
    for enrollments in (
        (binding(keys), binding(keys)), [binding(keys)], ({},),
        (replace(binding(keys), kind=[]),), (replace(binding(keys), expires_at=True),),
        (replace(binding(keys), resource="other"),), (replace(binding(keys), principal="bad/"),),
    ):
        with pytest.raises(IdentityError):
            verifier(keys, enrollments=enrollments)

"""Real synthetic-key JOSE verification, bounds and single-owner proof fencing."""

import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

from joserfc import jwt
from joserfc.jwk import ECKey
import pytest

from fleet_manager.runtime_identity import (
    IdentityError, IdentityVerifier, MAX_ACCESS_BYTES, MAX_PROOF_BYTES, VerifiedIdentity,
)

NOW = 1_900_000_000
ISSUER = "https://issuer.example"
AUDIENCE = "example-gateway"
TARGET = "https://gateway.example/v1/observations"


class Clock:
    def __init__(self, value=NOW):
        self.value = value

    def __call__(self):
        return self.value


@pytest.fixture
def keys():
    return ECKey.generate_key("P-256"), ECKey.generate_key("P-256")


def verifier(keys, **kwargs):
    public = {**keys[0].as_dict(private=False), "kid": "issuer-1"}
    return IdentityVerifier(
        issuer=ISSUER, audience=AUDIENCE, jwks={"keys": [public]},
        trust_valid_until=kwargs.pop("trust_valid_until", NOW + 86400 * 31),
        clock=kwargs.pop("clock", Clock()), **kwargs,
    )


def pair(keys, owner, *, now=NOW, access=None, proof=None, access_header=None,
         proof_header=None, signer=None, proof_signer=None):
    claims = {
        "iss": ISSUER, "sub": "principal-1", "aud": AUDIENCE,
        "iat": now, "exp": now + 3600, "cnf": {"jkt": keys[1].thumbprint()},
        **(access or {}),
    }
    token = jwt.encode(
        {"alg": "ES256", "typ": "at+jwt", "kid": "issuer-1", **(access_header or {})},
        claims, signer or keys[0],
    )
    token_hash = base64.urlsafe_b64encode(
        hashlib.sha256(token.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    attestation = jwt.encode(
        {"alg": "ES256", "typ": "dpop+jwt", "jwk": keys[1].as_dict(private=False),
         **(proof_header or {})},
        {"jti": "proof-1", "htm": "GET", "htu": TARGET, "iat": now,
         "ath": token_hash, "nonce": owner.nonce, **(proof or {})},
        proof_signer or keys[1],
    )
    return token, attestation


def check(owner, credentials, *, now=None, method="GET", target_uri=TARGET):
    if now is not None:
        owner._clock.value = now
    return owner.verify(*credentials, method=method, target_uri=target_uri)


def refuses(owner, credentials, code, **kwargs):
    with pytest.raises(IdentityError) as caught:
        check(owner, credentials, **kwargs)
    assert caught.value.code == code
    assert str(caught.value) == code
    assert all(value not in str(caught.value) for value in credentials)


def test_identity_is_immutable_nonsecret_and_not_enrollment(keys):
    owner = verifier(keys)
    credentials = pair(keys, owner)
    identity = check(owner, credentials)
    assert isinstance(identity, VerifiedIdentity)
    assert (identity.issuer, identity.subject, identity.audience) == (
        ISSUER, "principal-1", AUDIENCE,
    )
    assert identity.proof_key == keys[1].thumbprint()
    assert identity.expires_at == NOW + 3600
    assert identity.profile == "jwt-pop-v1"
    assert not hasattr(identity, "roles") and not hasattr(identity, "permit")
    assert all(value not in repr(identity) for value in credentials)
    with pytest.raises(FrozenInstanceError):
        identity.subject = "other"


@pytest.mark.parametrize("claims,code", [
    ({"iss": "https://other.example"}, "invalid-credential"),
    ({"aud": "other"}, "invalid-credential"),
    ({"aud": [AUDIENCE, "other"]}, "invalid-credential"),
    ({"sub": ""}, "invalid-credential"),
    ({"sub": ["principal-1"]}, "invalid-credential"),
    ({"exp": NOW}, "expired-credential"),
    ({"exp": True}, "invalid-credential"),
    ({"iat": NOW + 31}, "invalid-credential"),
    ({"iat": 1.5}, "invalid-credential"),
    ({"nbf": NOW + 31}, "invalid-credential"),
    ({"nbf": NOW + 3600}, "invalid-credential"),
    ({"cnf": {"jkt": "x" * 43}}, "invalid-key-binding"),
    ({"cnf": {}}, "invalid-key-binding"),
    ({"cnf": {"jkt": "x" * 43, "extra": "bad"}}, "invalid-key-binding"),
])
def test_access_claim_refusals(keys, claims, code):
    owner = verifier(keys)
    refuses(owner, pair(keys, owner, access={"iat": NOW - 100, **claims}), code)


@pytest.mark.parametrize("header,code", [
    ({"typ": "JWT"}, "invalid-credential"),
    ({"kid": "unknown"}, "unknown-key"),
    ({"jku": "https://attacker.example/keys"}, "invalid-credential"),
    ({"jwk": {}}, "invalid-credential"),
    ({"x5u": "https://attacker.example/key"}, "invalid-credential"),
    ({"crit": ["custom"]}, "invalid-credential"),
])
def test_no_header_discovery_or_type_confusion(keys, header, code):
    owner = verifier(keys)
    # Unknown critical extensions are refused by the signer too.
    if "crit" in header:
        token, proof = pair(keys, owner)
        chunks = token.split(".")
        raw = json.dumps({**json.loads(base64.urlsafe_b64decode(
            chunks[0] + "=" * (-len(chunks[0]) % 4))), **header}).encode()
        chunks[0] = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
        credentials = ".".join(chunks), proof
    else:
        credentials = pair(keys, owner, access_header=header)
    refuses(owner, credentials, code)


def test_wrong_signatures_and_stolen_token(keys):
    owner = verifier(keys)
    other = ECKey.generate_key("P-256")
    refuses(owner, pair(keys, owner, signer=other), "invalid-signature")
    refuses(owner, pair(keys, owner, proof_signer=other), "invalid-signature")
    token, _ = pair(keys, owner)
    _, different_proof = pair((keys[0], other), owner)
    refuses(owner, (token, different_proof), "invalid-key-binding")


@pytest.mark.parametrize("claims", [
    {"htm": "POST"}, {"htu": "https://gateway.example/other"},
    {"htu": TARGET + "?foreign=1"}, {"htu": TARGET + "#fragment"},
    {"iat": NOW - 91}, {"iat": NOW + 31}, {"iat": True},
    {"nonce": "old-epoch"}, {"jti": ""}, {"jti": "x" * 129}, {"extra": "bad"},
])
def test_request_nonce_and_time_binding(keys, claims):
    owner = verifier(keys)
    refuses(owner, pair(keys, owner, proof=claims), "invalid-proof")


def test_token_hash_binding(keys):
    owner = verifier(keys)
    refuses(owner, pair(keys, owner, proof={"ath": "x" * 43}), "invalid-token-binding")
    first, proof = pair(keys, owner)
    renewed, _ = pair(keys, owner, access={"exp": NOW + 7200})
    assert renewed != first
    refuses(owner, (renewed, proof), "invalid-token-binding")


def test_expiry_is_not_extended_by_clock_skew(keys):
    owner = verifier(keys)
    refuses(owner, pair(keys, owner, access={"iat": NOW - 100, "exp": NOW}),
            "expired-credential")
    refuses(owner, pair(keys, owner), "authority-unavailable",
            now=NOW + 86400 * 31)


def test_atomic_replay_no_live_eviction_and_fresh_retry(keys):
    owner = verifier(keys, replay_capacity=1)
    credentials = pair(keys, owner)
    def attempt(_):
        try:
            return check(owner, credentials).subject
        except IdentityError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))
    assert results.count("principal-1") == 1
    assert results.count("proof-replayed") == 7
    refuses(owner, pair(keys, owner, proof={"jti": "second"}), "resource-limit")
    refuses(owner, credentials, "proof-replayed", now=NOW + 90)
    check(owner, pair(keys, owner, now=NOW + 91, proof={"jti": "second"}), now=NOW + 91)
    refuses(owner, pair(keys, owner, now=NOW + 90, proof={"jti": "third"}),
            "clock-rollback", now=NOW + 90)


def test_nonce_epoch_rejects_old_proof_after_owner_restart(keys):
    first, replacement = verifier(keys), verifier(keys)
    credentials = pair(keys, first)
    check(first, credentials)
    assert first.nonce != replacement.nonce
    refuses(replacement, credentials, "invalid-proof")
    check(replacement, pair(keys, replacement))


def test_silent_long_lived_renewal_has_no_gateway_presence_timer(keys):
    owner = verifier(keys)
    for day in range(30):
        now = NOW + day * 86400
        # A provider-renewed token may be long-lived; no universal five-minute cap.
        identity = check(owner, pair(keys, owner, now=now, access={"exp": now + 86400},
                                    proof={"jti": f"proof-{day}"}), now=now)
        assert identity.subject == "principal-1"
        assert identity.expires_at == now + 86400
    assert not hasattr(owner, "presence_deadline")


@pytest.mark.parametrize("token,proof", [
    ("x" * (MAX_ACCESS_BYTES + 1), "a.b.c"),
    ("a.b.c", "x" * (MAX_PROOF_BYTES + 1)),
    ("a.b.c", None), ("a.b.c.d.e", "a.b.c"),
    ("e30.e30.", "a.b.c"), ("e30=.e30.c", "a.b.c"),
])
def test_bounded_compact_objects(keys, token, proof):
    with pytest.raises(IdentityError):
        check(verifier(keys), (token, proof))


def test_duplicate_json_and_algorithm_substitution(keys):
    owner = verifier(keys)
    token, proof = pair(keys, owner)
    for raw in (
        b'{"alg":"ES256","alg":"none","typ":"at+jwt","kid":"issuer-1"}',
        b'{"alg":"none","typ":"at+jwt","kid":"issuer-1"}',
        b'{"alg":"HS256","typ":"at+jwt","kid":"issuer-1"}',
    ):
        header = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
        credentials = (header + "." + ".".join(token.split(".")[1:]), proof)
        refuses(owner, credentials, "invalid-credential")


@pytest.mark.parametrize("change", [
    {"d": "private"}, {"kty": "oct"}, {"crv": "P-384"}, {"alg": "HS256"},
    {"use": "enc"}, {"key_ops": ["sign"]}, {"x": "!" * 43},
])
def test_reject_private_wrong_type_and_malformed_public_key(keys, change):
    public = {**keys[0].as_dict(private=False), "kid": "issuer-1", **change}
    with pytest.raises(IdentityError):
        IdentityVerifier(issuer=ISSUER, audience=AUDIENCE, jwks={"keys": [public]},
                         trust_valid_until=NOW + 60, clock=Clock())


@pytest.mark.parametrize("context", [
    {"target_uri": "http://gateway.example/v1/observations"},
    {"target_uri": TARGET + "?q=1"}, {"target_uri": TARGET + "#x"},
    {"target_uri": TARGET + "?"}, {"target_uri": TARGET + "#"},
    {"target_uri": "https://user:password@gateway.example/v1/observations"},
    {"method": "get"},
])
def test_trusted_context_must_be_explicit_https_route(keys, context):
    owner = verifier(keys)
    refuses(owner, pair(keys, owner), "invalid-request-context", **context)


def test_actual_fresh_process_verifies_crypto_and_restart_fence(keys):
    owner = verifier(keys)
    token, proof = pair(keys, owner)
    script = """
import json, sys
from joserfc import jwt
from joserfc.jwk import ECKey
from fleet_manager.runtime_identity import IdentityVerifier, IdentityError
v = json.load(sys.stdin)
owner = IdentityVerifier(issuer=v['issuer'], audience=v['audience'],
    jwks=v['jwks'], trust_valid_until=v['now'] + 3600, clock=lambda: v['now'])
try:
    owner.verify(v['token'], v['proof'], method='GET', target_uri=v['target'])
except IdentityError as exc:
    assert exc.code == 'invalid-proof'
else:
    raise AssertionError('old nonce epoch accepted')
key = ECKey.import_key(v['proof_key'])
proof = jwt.encode({'alg':'ES256', 'typ':'dpop+jwt', 'jwk':key.as_dict(private=False)},
    {**v['claims'], 'nonce':owner.nonce}, key)
identity = owner.verify(v['token'], proof, method='GET', target_uri=v['target'])
assert identity.subject == 'principal-1'
print('fresh-process-verified')
"""
    claims = json.loads(base64.urlsafe_b64decode(
        proof.split(".")[1] + "=" * (-len(proof.split(".")[1]) % 4)))
    payload = {"issuer": ISSUER, "audience": AUDIENCE, "now": NOW,
               "jwks": {"keys": [{**keys[0].as_dict(private=False), "kid": "issuer-1"}]},
               "target": TARGET, "token": token, "proof": proof,
               "proof_key": keys[1].as_dict(private=True), "claims": claims}
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    result = subprocess.run(
        [sys.executable, "-c", script], input=json.dumps(payload), text=True,
        capture_output=True, timeout=15, env=env,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "fresh-process-verified"


def test_stdlib_only_import_and_explicit_missing_capability(keys):
    script = """
import json, sys, importlib.util
sys.path.insert(0, sys.argv[1])
assert importlib.util.find_spec('joserfc') is None
from fleet_manager.runtime_identity import IdentityVerifier, IdentityError
assert 'joserfc' not in sys.modules
try:
    IdentityVerifier(issuer='https://issuer.example', audience='gateway',
        jwks=json.load(sys.stdin), trust_valid_until=1900000100, clock=lambda: 1900000000)
except IdentityError as exc:
    assert exc.code == 'unsupported-profile'
else:
    raise AssertionError('missing capability was accepted')
print('stdlib-only-refusal')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", script,
         str(Path(__file__).parents[1] / "src")],
        input=json.dumps({"keys": [{**keys[0].as_dict(private=False), "kid": "issuer-1"}]}),
        capture_output=True, text=True, timeout=15,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "stdlib-only-refusal"


def test_older_concurrent_request_can_finish_after_newer_request(keys, monkeypatch):
    from fleet_manager import runtime_identity

    owner = verifier(keys)
    older = pair(keys, owner, proof={"jti": "older"})
    newer = pair(keys, owner, now=NOW + 1, proof={"jti": "newer"})
    entered, release = threading.Event(), threading.Event()
    real_verify = runtime_identity._verify

    def delayed_verify(value, key):
        if value == older[0]:
            entered.set()
            assert release.wait(5)
        real_verify(value, key)

    monkeypatch.setattr(runtime_identity, "_verify", delayed_verify)
    with ThreadPoolExecutor(max_workers=1) as pool:
        waiting = pool.submit(check, owner, older)
        try:
            assert entered.wait(5)
            owner._clock.value = NOW + 1
            assert check(owner, newer).verified_at == NOW + 1
        finally:
            release.set()
        assert waiting.result(timeout=5).verified_at == NOW + 1


@pytest.mark.parametrize("advance,expiry,trust,code", [
    (2, NOW + 1, NOW + 1000, "expired-credential"),
    (91, NOW + 1000, NOW + 1000, "invalid-proof"),
    (2, NOW + 1000, NOW + 1, "authority-unavailable"),
])
def test_current_time_rechecked_after_real_signature_verification(
        keys, monkeypatch, advance, expiry, trust, code):
    from fleet_manager import runtime_identity

    owner = verifier(keys, trust_valid_until=trust)
    credentials = pair(keys, owner, access={"exp": expiry})
    real_verify = runtime_identity._verify

    def advancing_verify(value, key):
        real_verify(value, key)
        owner._clock.value = NOW + advance

    monkeypatch.setattr(runtime_identity, "_verify", advancing_verify)
    refuses(owner, credentials, code)
    assert not owner._replays


def test_invalid_trusted_clock_refuses(keys):
    owner = verifier(keys, clock=Clock(True))
    refuses(owner, pair(keys, owner), "invalid-clock")

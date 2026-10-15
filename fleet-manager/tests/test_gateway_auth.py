import copy
import time

import jwt
import pytest
from fleet_manager.gateway_auth import Admission
from fleet_manager.gateway_contract import GatewayConfig, GatewayError, parse
from gateway_fixtures import Fixtures


@pytest.fixture
def fixture(tmp_path):
    return Fixtures(tmp_path)


@pytest.mark.parametrize("field,value", [
    ("fleet", "fleet-b"), ("role", "credential-controller"), ("service", "foreign"),
    ("installation", "foreign"), ("generation", 2), ("sources", ["source-b"]),
    ("corpora", ["corpus-b"]), ("accounts", ["other"]), ("audiences", ["other"]),
    ("scopes", ["write"]), ("cnf", {"jkt": "wrong"}), ("generation", True), ("jobs", ["other"]),
])
def test_token_grants_never_override_current_enrollment(fixture, field, value):
    access = fixture.access("alice", {field: value})
    admission = Admission(fixture.config)
    message = fixture.message("alice", "/v1/session", {}, admission=admission, access=access)
    if field == "cnf":
        with pytest.raises(GatewayError):
            admission.authenticate(message["access"], message["proof"], "https://127.0.0.1:443/v1/session")
    else:
        context = admission.authenticate(message["access"], message["proof"],
                                         "https://127.0.0.1:443/v1/session")
        assert context.peer == fixture.config.enrollments[0]
        assert context.identity.subject == "alice"


@pytest.mark.parametrize("change", ["signature", "issuer", "audience", "expiry", "notbefore",
                                   "algorithm", "unknown-key", "revoked-key", "revoked-principal",
                                   "revoked-jti", "enrollment-expired"])
def test_real_crypto_rejections(fixture, change):
    token = fixture.access("alice")
    claims = jwt.decode(token, options={"verify_signature": False})
    config = copy.deepcopy(fixture.value)
    if change == "signature":
        from cryptography.hazmat.primitives.asymmetric import ec
        token = jwt.encode(claims, ec.generate_private_key(ec.SECP256R1()), algorithm="ES256",
                           headers={"kid": "issuer", "typ": "at+jwt"})
    elif change == "issuer":
        claims["iss"] = "other"
    elif change == "audience":
        claims["aud"] = "other"
    elif change == "expiry":
        claims["iat"], claims["nbf"], claims["exp"] = (int(time.time()) - 100, int(time.time()) - 100,
                                                      int(time.time()) - 1)
    elif change == "notbefore":
        claims["nbf"] = int(time.time()) + 60
    elif change == "algorithm":
        token = jwt.encode(claims, "synthetic-HS256-test-key-long-enough", algorithm="HS256",
                           headers={"kid": "issuer"})
    elif change == "unknown-key":
        token = jwt.encode(claims, fixture.access_issuer, algorithm="ES256",
                           headers={"kid": "not-configured", "typ": "at+jwt"})
    elif change == "revoked-key":
        config["revoked"] = ["issuer"]
    elif change == "revoked-principal":
        config["revoked"] = ["alice"]
    elif change == "revoked-jti":
        config["revoked"] = [claims["jti"]]
    elif change == "enrollment-expired":
        config["enrollments"][0]["expires_at"] = int(time.time()) - 1
    if change in ("issuer", "audience", "expiry", "notbefore"):
        token = jwt.encode(claims, fixture.access_issuer, algorithm="ES256",
                           headers={"kid": "issuer", "typ": "at+jwt"})
    with pytest.raises(GatewayError):
        admission = Admission(GatewayConfig.load(config))
        message = fixture.message("alice", "/v1/session", {}, admission=admission, access=token)
        admission.authenticate(message["access"], message["proof"], "https://127.0.0.1:443/v1/session")


def test_dpop_target_binding_and_replay(fixture):
    admission = Admission(fixture.config)
    message = fixture.message("alice", "/v1/session", {}, admission=admission)
    assert admission.authenticate(message["access"], message["proof"],
                                   "https://127.0.0.1:443/v1/session").peer.principal == "alice"
    with pytest.raises(GatewayError):
        admission.authenticate(message["access"], message["proof"], "https://127.0.0.1:443/v1/session")
    for route in ("/wrong", "/v1/session?query=untrusted"):
        fresh = Admission(fixture.config)
        message = fixture.message("alice", "/v1/session", {}, admission=fresh)
        with pytest.raises(GatewayError):
            fresh.authenticate(message["access"], message["proof"], "https://127.0.0.1:443" + route)


@pytest.mark.parametrize("mutate", [
    lambda v: v.update(role="client"), lambda v: v.update(jwks={"keys": []}),
    lambda v: v["tls"].update(host="0.0.0.0"),
    lambda v: v["jwks"]["keys"][0].update(d="private"),
    lambda v: v["backends"][0].update(endpoint="http://127.0.0.1:80"),
    lambda v: v["backends"][0].update(endpoint="https://example.invalid:443"),
    lambda v: v["backends"][0].update(endpoint="https://127.0.0.1:443/search"),
])
def test_fail_closed_configuration(fixture, mutate):
    value = copy.deepcopy(fixture.value)
    mutate(value)
    with pytest.raises(GatewayError):
        GatewayConfig.load(value)


def test_wire_duplicate_and_size_bounds():
    with pytest.raises(GatewayError):
        parse(b'{"a":1,"a":2}')
    with pytest.raises(GatewayError):
        parse(b"x" * 65537)


def test_gateway_uses_shared_evidence_and_provider_session_lifetime(fixture):
    from fleet_manager.gateway_auth import make_proof
    from fleet_manager.runtime_identity import VerifiedIdentity

    admission = Admission(fixture.config)
    access = fixture.access("alice", {"exp": int(time.time()) + 86400})
    message = fixture.message("alice", "/v1/session", {}, admission=admission, access=access)
    target = "https://127.0.0.1:443/v1/session"
    context = admission.authenticate(access, message["proof"], target)
    assert isinstance(context.identity, VerifiedIdentity)
    assert context.identity.profile == "jwt-pop-v1"
    assert context.identity.expires_at - context.identity.issued_at == 86400
    assert context.expires_at == context.peer.expires_at
    assert set(jwt.decode(message["proof"], options={"verify_signature": False})) == {
        "htm", "htu", "iat", "jti", "ath", "nonce",
    }
    replacement = Admission(fixture.config)
    with pytest.raises(GatewayError, match="invalid-proof"):
        replacement.authenticate(access, message["proof"], target)
    proof = make_proof(fixture.identity_proofs["alice"], access,
                       target_uri=target, nonce=replacement.verifier.nonce)
    assert replacement.authenticate(access, proof, target).peer.principal == "alice"


def test_expired_identity_trust_is_not_receipt_or_legacy_trust(fixture):
    value = copy.deepcopy(fixture.value)
    value["identity"]["trust_valid_until"] = int(time.time()) - 1
    admission = Admission(GatewayConfig.load(value))
    message = fixture.message("alice", "/v1/session", {}, admission=admission)
    with pytest.raises(GatewayError, match="authority-unavailable"):
        admission.authenticate(message["access"], message["proof"], "https://127.0.0.1:443/v1/session")
    value = copy.deepcopy(fixture.value)
    value["identity"]["jwks"] = value["jwks"]
    with pytest.raises(GatewayError):
        Admission(GatewayConfig.load(value))
    del value["identity"]
    with pytest.raises(GatewayError):
        GatewayConfig.load(value)


def test_preverification_budget_and_principal_isolation(fixture, monkeypatch):
    monkeypatch.setattr("fleet_manager.gateway_auth.time.monotonic", lambda: 100)
    admission = Admission(fixture.config)
    for _ in range(256):
        admission.budget()
    monkeypatch.setattr(admission.verifier, "verify", lambda *a, **k: pytest.fail("unbudgeted verification"))
    with pytest.raises(GatewayError, match="admission-unavailable"):
        admission.authenticate("untrusted", "untrusted", "https://127.0.0.1:443/v1/session")
    admission = Admission(fixture.config)
    for _ in range(32):
        admission.budget("alice")
    alice = fixture.message("alice", "/v1/session", {}, admission=admission)
    with pytest.raises(GatewayError, match="admission-unavailable"):
        admission.authenticate(alice["access"], alice["proof"], "https://127.0.0.1:443/v1/session")
    bob = fixture.message("bob", "/v1/session", {}, admission=admission)
    assert admission.authenticate(bob["access"], bob["proof"],
                                  "https://127.0.0.1:443/v1/session").peer.principal == "bob"

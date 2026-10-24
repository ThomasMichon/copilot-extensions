"""Harness boundaries and synthetic TLS regression, not real-provider evidence."""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import json
from pathlib import Path
import ssl
import sys
import time
from unittest.mock import patch

import aiohttp
from cryptography import x509
from joserfc import jwt
from joserfc.jwk import ECKey
import pytest

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "tools/clean-room/authelia-profile-qualification"
SHARED = BASE.parent / "gateway-role-tunnels"


def load() -> dict:
    modules = {}
    with patch.dict(sys.modules), patch.object(sys, "path", list(sys.path)):
        for name, directory in (
            ("source", SHARED), ("architecture", SHARED), ("engine", SHARED),
            ("diagnostics", BASE), ("prepare", BASE), ("probe", BASE), ("authelia_run", BASE),
        ):
            filename = "run" if name == "authelia_run" else name
            spec = importlib.util.spec_from_file_location(name, directory / (filename + ".py"))
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            modules[name] = module
    return modules


MODULES = load()
PREP, PROBE, RUN = (MODULES[name] for name in ("prepare", "probe", "authelia_run"))
CID, NETWORK, IMAGE = "a" * 64, "b" * 64, "sha256:" + "c" * 64


@pytest.fixture
def prepared(tmp_path: Path) -> Path:
    assert PREP.prepare(tmp_path) == {
        "result": "prepared", "credentials": "synthetic-files-only"}
    return tmp_path


def test_secret_files_exclusive_private_separated_without_umask_change(prepared: Path) -> None:
    for path in prepared.rglob("*"):
        assert path.stat().st_mode & 0o077 == 0
    config = json.loads((prepared / "provider/configuration.yml").read_bytes())
    inputs = json.loads((prepared / "client/inputs.json").read_bytes())
    assert config["server"]["disable_healthcheck"] is True
    assert config["ntp"]["disable_startup_check"] is True
    assert config["access_control"] == {
        "default_policy": "deny",
        "rules": [{"domain": "issuer.fixture.test", "policy": "one_factor"}],
    }  # Authelia rejects deny-by-default with no rules before starting.
    assert config["identity_providers"]["oidc"]["clients"][0]["client_secret"] == PREP.client_digest(
        inputs["client_secret"],
        base64.b64decode(config["identity_providers"]["oidc"]["clients"][0]["client_secret"]
                         .split("$")[3].replace(".", "+") + "=="),
    )
    password_hash = json.loads((prepared / "provider/users.yml").read_bytes())[
        "users"]["fixture"]["password"]
    assert password_hash.startswith("$argon2id$")
    assert set(path.name for path in (prepared / "client").iterdir()) == {
        "inputs.json", "ca.pem", "cert.pem", "key.pem"}
    client_public = ECKey.import_key(inputs["proof_key"]).as_dict(private=False)
    assert all(client_public["x"] not in item["key"]
               for item in config["identity_providers"]["oidc"]["jwks"])
    assert x509.load_pem_x509_certificate(
        (prepared / "client/ca.pem").read_bytes()).extensions.get_extension_for_class(
            x509.BasicConstraints).value.ca
    with pytest.raises(FileExistsError):
        PREP.private_write(prepared / "client/inputs.json", b"replace")
    with patch.object(PREP.os, "umask", side_effect=AssertionError("global umask")):
        target = prepared / "another"
        target.mkdir(mode=0o700)
        PREP.prepare(target)


def test_documented_pbkdf2_vector() -> None:
    # Authelia documents this exact digest for its example secret.
    digest = (
        "$pbkdf2-sha512$310000$c8p78n7pUMln0jzvd4aK4Q$"
        "JNRBzwAo0ek5qKn50cFzzvE9RXV88h1wJn5KGiHrD0YKtZaR/nCb2CJPOsKaPK0hjf.9yHxzQGZziziccp6Yng"
    )
    salt = base64.b64decode("c8p78n7pUMln0jzvd4aK4Q==")
    assert PREP.client_digest("insecure_secret", salt) == digest


def test_header_construction_uses_unmangled_actual_value() -> None:
    fake = "fixture-value"
    value = PROBE.headers(fake, "fixture-proof")
    assert value["Authorization"].encode("ascii") == bytes([68, 80, 111, 80, 32]) + fake.encode()
    assert value["DPoP"] == "fixture-proof"
    assert b"*" * 6 not in (BASE / "probe.py").read_bytes()


@pytest.mark.parametrize("profile", ["jwt-pop-v1", "enrolled-pop-v1"])
@pytest.mark.parametrize("kind", ["prepare", "provider", "probe"])
def test_closed_container_profile(tmp_path: Path, kind: str, profile: str) -> None:
    for name in ("qualifier", "fixtures", "fixtures/client", "fixtures/provider"):
        (tmp_path / name).mkdir(mode=0o700)
    engine = RUN.QualifierEngine(tmp_path, {})
    engine.network = NETWORK
    captured = []

    def call(*args, **kwargs):
        captured.extend(args)
        (tmp_path / (kind + ".cid")).write_text(CID)
        kwargs["on_success"](CID.encode())
        return CID

    with patch.object(engine, "call", call), patch.object(
        MODULES["engine"].os, "getuid", return_value=1001,
    ):
        assert engine.create_fixture(IMAGE, kind, profile=profile) == CID
    assert engine.containers == [CID]
    expected_network = "none" if kind == "prepare" else NETWORK
    assert captured[captured.index("--network") + 1] == expected_network
    assert "--read-only" in captured
    for flag, value in {"--memory": "256m", "--cpus": "1", "--pids-limit": "64",
                        "--cap-drop": "ALL", "--security-opt": "no-new-privileges:true"}.items():
        assert captured[captured.index(flag) + 1] == value
    assert not set(captured) & {"--privileged", "--publish", "-p", "--env", "-e", "--device"}
    assert "docker.sock" not in " ".join(captured)
    mounts = [captured[i + 1] for i, item in enumerate(captured) if item == "--mount"]
    assert sum(not value.endswith(",readonly") for value in mounts) == (kind == "prepare")
    if kind == "provider":
        assert len(mounts) == 1 and "/fixtures/provider," in mounts[0]
        assert captured[captured.index("--network-alias") + 1] == "issuer.fixture.test"
        assert captured[-3:] == [IMAGE, "--config", "/config/configuration.yml"]
    if kind == "probe":
        assert all("/fixtures/provider," not in value for value in mounts)
        if profile == "jwt-pop-v1":
            assert captured[-2:] == [IMAGE, "/proof/probe.py"]
        else:
            assert captured[-4:] == [
                IMAGE, "/proof/probe.py", "--profile", "enrolled-pop-v1"]


@pytest.mark.parametrize("kind,image", [("arbitrary", IMAGE), ("provider", "latest")])
def test_unknown_profile_refuses_before_create(tmp_path: Path, kind: str, image: str) -> None:
    engine = RUN.QualifierEngine(tmp_path, {})
    with patch.object(engine, "call") as call, pytest.raises(RUN.ProofError):
        engine.create_fixture(image, kind)
    call.assert_not_called()


def test_no_provider_log_content_is_persisted(tmp_path: Path) -> None:
    engine = RUN.QualifierEngine(tmp_path, {})
    log = engine.logs / "log"
    engine.write_log(log, b"secret-value\nraw-jwt\nprivate-key")
    assert json.loads(log.read_bytes()) == {
        "output_bytes": 32, "raw_output_retained": False,
        "classifications": [], "unclassified_lines": 3,
        "diagnostic_capture": "redacted", "diagnostics": [], "omitted_diagnostic_records": 0,
    }
    assert b"secret-value" not in log.read_bytes()


def test_diagnostics_project_categories_and_never_messages_or_unknown_codes() -> None:
    data = b"\n".join(json.dumps(item).encode() for item in (
        {"result": "fail", "code": "provider-unavailable", "secret": "never-retain"},
        {"result": "fail", "code": "secret-in-an-unknown-code"},
        {"level": "error", "msg": "Configuration: access_control secret never-retain"},
    ))
    projected = RUN.diagnostic_traits(data)
    assert projected["classifications"] == [
        "provider-cause-configuration", "provider-config-access-control", "provider-error",
        "provider-unavailable",
    ]
    assert projected["unclassified_lines"] == 1
    assert "never-retain" not in json.dumps(projected)


def test_provider_nested_error_is_classified_without_copying_it() -> None:
    data = json.dumps({
        "level": "fatal", "msg": "Startup failed", "error": "SQLite database: permission denied",
        "context": "never-retain",
    }).encode()
    assert RUN.diagnostic_traits(data)["classifications"] == [
        "provider-cause-database", "provider-cause-permission-denied",
        "provider-cause-sqlite", "provider-cause-startup", "provider-error",
    ]


def outcome() -> dict:
    return {
        "result": "verified", "resource_tls_verified": True, "provider_tls_verified": True,
        "profile": "jwt-pop-v1", "grant": "client_credentials",
        "human_flow_exercised": False, "enrollment_qualified": False,
        "policy_qualified": False, "production_qualified": False,
        "traits": {**dict.fromkeys((
            "access_alg_es256", "access_type_at_jwt", "audience_exact",
            "proof_binding_present", "proof_binding_matches",
            "traits_are_unverified", "token_type_dpop"), True), "audience_shape": "string"},
    }


def enrolled_outcome() -> dict:
    result = outcome()
    result.update(profile="enrolled-pop-v1", negative_checks={
        "wrong_key_refused": True, "missing_proof_refused": True, "replay_refused": True,
    })
    result["traits"].update(
        audience_shape="array", audience_exact=False, proof_binding_present=False,
        proof_binding_matches=False, token_type_dpop=False, client_id_exact=True,
        subject_exact=True, audience_allowed=True,
    )
    return result


def test_enrolled_receipt_cannot_change_selected_profile_or_claim_issuer_dpop() -> None:
    result = enrolled_outcome()
    assert RUN.admit_outcome(result, profile="enrolled-pop-v1") == result
    assert result["traits"]["proof_binding_present"] is False
    assert result["traits"]["token_type_dpop"] is False
    with pytest.raises(RUN.ProofError):
        RUN.admit_outcome(result)
    with pytest.raises(RUN.ProofError):
        RUN.admit_outcome(outcome(), profile="enrolled-pop-v1")
    assert PROBE.PROFILES == RUN.PROFILES == ("jwt-pop-v1", "enrolled-pop-v1")


@pytest.mark.parametrize("field", [
    "wrong_key_refused", "missing_proof_refused", "replay_refused",
])
@pytest.mark.parametrize("value", [False, "true", "never-persist", None])
def test_enrolled_success_requires_exact_negative_proof_evidence(field: str, value: object) -> None:
    result = enrolled_outcome()
    result["negative_checks"][field] = value
    with pytest.raises(RUN.ProofError):
        RUN.admit_outcome(result, profile="enrolled-pop-v1")


@pytest.mark.parametrize("field", [
    "access_alg_es256", "access_type_at_jwt", "client_id_exact",
    "subject_exact", "audience_allowed",
])
def test_enrolled_success_requires_exact_token_traits(field: str) -> None:
    result = enrolled_outcome()
    result["traits"][field] = False
    with pytest.raises(RUN.ProofError, match="not-established"):
        RUN.admit_outcome(result, profile="enrolled-pop-v1")
    result.update(result="incompatible", identity_error="invalid-key-binding")
    assert RUN.admit_outcome(result, profile="enrolled-pop-v1") == result


@pytest.mark.parametrize("location", ["root", "traits", "negative_checks"])
def test_enrolled_receipt_never_accepts_raw_claims_or_diagnostics(location: str) -> None:
    result = enrolled_outcome()
    target = result if location == "root" else result[location]
    target["subject"] = "never-persist-unverified-subject"
    with pytest.raises(RUN.ProofError):
        RUN.admit_outcome(result, profile="enrolled-pop-v1")


@pytest.mark.parametrize("profile", ["bearer", "", "ENROLLED-POP-V1", "jwt-pop-v2"])
def test_closed_profile_refusal_precedes_files_or_effects(tmp_path: Path, profile: str) -> None:
    with patch.object(RUN, "materialize_probe") as materialize, pytest.raises(RUN.ProofError):
        RUN.run(ROOT, "a" * 40, tmp_path, 900, profile=profile)
    materialize.assert_not_called()
    engine = RUN.QualifierEngine(tmp_path, {})
    with patch.object(engine, "call") as call, pytest.raises(RUN.ProofError):
        engine.create_fixture(IMAGE, "probe", profile=profile)
    call.assert_not_called()
    with pytest.raises(PROBE.ProbeError, match="unsupported-qualification-profile"):
        asyncio.run(PROBE.qualify(profile))
    with patch.object(RUN, "private_root") as private, pytest.raises(SystemExit) as caught:
        RUN.main(["--source-commit", "a" * 40, "--results-root", str(tmp_path),
                  "--profile", profile])
    assert caught.value.code == 2
    private.assert_not_called()
    with pytest.raises(SystemExit) as caught:
        asyncio.run(PROBE.main(["--profile", profile]))
    assert caught.value.code == 2


@pytest.mark.parametrize("profile", ["jwt-pop-v1", "enrolled-pop-v1"])
def test_run_receipts_and_fixed_probe_selection_without_docker(tmp_path: Path, profile: str) -> None:
    calls = []
    expected = outcome() if profile == "jwt-pop-v1" else enrolled_outcome()

    class Engine:
        def __init__(self, root, receipt):
            assert root == tmp_path
            assert receipt["profile"] == profile

        def journal(self):
            pass

        def preflight(self):
            pass

        def build(self, commit, timeout):
            assert commit == "a" * 40 and timeout == 900
            return IMAGE

        def provider_image(self):
            return IMAGE

        def create_network(self):
            pass

        def start(self, cid):
            assert cid == "provider"

        def create_fixture(self, image, kind, *, profile="jwt-pop-v1"):
            assert image == IMAGE
            calls.append((kind, profile))
            return kind

        def attached(self, cid, timeout):
            if cid == "prepare":
                assert timeout == 35
                return {"result": "prepared", "credentials": "synthetic-files-only"}
            assert cid == "probe" and timeout == 110
            return expected

        def cleanup(self):
            return []

    with (patch.object(RUN, "materialize_probe", return_value={"source_commit": "a" * 40}),
          patch.object(RUN, "QualifierEngine", Engine)):
        result = RUN.run(ROOT, "a" * 40, tmp_path, 900, profile=profile)
    assert result["profile"] == profile and result["outcome"] == expected
    assert result["result"] == "verified" and result["cleanup"] == "complete"
    assert calls == [("prepare", "jwt-pop-v1"), ("provider", "jwt-pop-v1"), ("probe", profile)]
    assert json.loads((tmp_path / "receipt.json").read_bytes()) == result


@pytest.mark.parametrize("field,value", [
    ("profile", "other"), ("access_token", "never-persist"),
    ("production_qualified", True), ("human_flow_exercised", True),
    ("identity_error", "provider-secret-error"), ("resource_tls_verified", False),
])
def test_receipt_rejects_claim_expansion_and_unbounded_values(field: str, value: object) -> None:
    result = outcome()
    result[field] = value
    with pytest.raises(RUN.ProofError):
        RUN.admit_outcome(result)


@pytest.mark.parametrize("field", [
    "access_alg_es256", "access_type_at_jwt", "audience_exact",
    "proof_binding_present", "proof_binding_matches", "token_type_dpop",
])
def test_no_success_without_every_profile_trait(field: str) -> None:
    result = outcome()
    result["traits"][field] = False
    with pytest.raises(RUN.ProofError, match="not-established"):
        RUN.admit_outcome(result)
    result.update(result="incompatible", identity_error="invalid-key-binding")
    assert RUN.admit_outcome(result) == result


def test_configured_jwks_selection_ignores_unrelated_rsa_without_rewriting_ec() -> None:
    key = {**ECKey.generate_key("P-256").as_dict(private=False), "kid": PREP.SIGNING_KID}
    supplied = {"keys": [{"kid": "required-rsa", "kty": "RSA"}, key]}
    assert PROBE.trusted_subset(supplied)["keys"][0] is key
    with pytest.raises(PROBE.ProbeError):
        PROBE.trusted_subset({"keys": [key, key]})
    with pytest.raises(PROBE.ProbeError):
        PROBE.trusted_subset({"keys": [{"kid": "token-selected", "kty": "EC"}]})


@pytest.mark.parametrize("binding", [True, False])
def test_real_tls_resource_calls_actual_verifier_with_synthetic_token(
    prepared: Path, binding: bool,
) -> None:
    inputs = json.loads((prepared / "client/inputs.json").read_bytes())
    key = ECKey.import_key(inputs["proof_key"])
    issuer = ECKey.generate_key("P-256")
    now = int(time.time())
    claims = {"iss": PREP.ISSUER, "aud": PREP.AUDIENCE, "sub": "synthetic-subject",
              "iat": now, "exp": now + 120}
    if binding:
        claims["cnf"] = {"jkt": key.thumbprint()}
    token = jwt.encode({"alg": "ES256", "typ": "at+jwt", "kid": PREP.SIGNING_KID}, claims, issuer)
    jwks = {"keys": [{**issuer.as_dict(private=False), "kid": PREP.SIGNING_KID}]}

    async def exercise():
        tls = ssl.create_default_context(cafile=prepared / "client/ca.pem")
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=tls), trust_env=False,
        ) as session:
            return await PROBE.resource_check(session, key, token, jwks)

    with patch.object(PROBE, "ROOT", prepared / "client"):
        result = asyncio.run(exercise())
    assert result["resource_tls_verified"] is True
    assert result["result"] == ("verified" if binding else "incompatible")
    if not binding:
        assert result["identity_error"] == "invalid-key-binding"
    traits = PROBE.token_traits(token, key)
    assert traits["proof_binding_matches"] is binding
    assert traits["traits_are_unverified"] is True
    assert "synthetic-subject" not in json.dumps(traits)


@pytest.mark.parametrize("claims,code", [
    ({}, None),
    ({"aud": [PREP.AUDIENCE]}, None),
    ({"client_id": "unapproved-client"}, "invalid-key-binding"),
    ({"client_id": None}, "invalid-credential"),
    ({"sub": "unapproved-subject"}, "invalid-key-binding"),
    ({"sub": PREP.ENROLLED_PRINCIPAL}, "invalid-key-binding"),
    ({"aud": [PREP.AUDIENCE, "https://unapproved.fixture.test"]}, "invalid-credential"),
    ({"aud": [PREP.AUDIENCE, PREP.AUDIENCE]}, "invalid-credential"),
    ({"iss": "https://other.fixture.test"}, "invalid-credential"),
    ({"exp": 1_000_000_001, "iat": 1_000_000_000}, "expired-credential"),
    ({"cnf": {"jkt": "x" * 43}}, "invalid-key-binding"),
])
def test_enrolled_fixture_uses_fixed_approval_and_actual_tls_crypto(
    prepared: Path, claims: dict, code: str | None,
) -> None:
    key = ECKey.import_key(json.loads((prepared / "client/inputs.json").read_bytes())["proof_key"])
    issuer = ECKey.generate_key("P-256")
    now = int(time.time())
    original_claims = {
        "iss": PREP.ISSUER, "aud": PREP.AUDIENCE, "sub": PREP.ENROLLED_SUBJECT,
        "client_id": PREP.CLIENT, "iat": now, "exp": now + 120, **claims,
    }
    token = jwt.encode({"alg": "ES256", "typ": "at+jwt", "kid": PREP.SIGNING_KID},
                       original_claims, issuer)
    jwks = {"keys": [{**issuer.as_dict(private=False), "kid": PREP.SIGNING_KID}]}

    async def exercise(profile="enrolled-pop-v1"):
        tls = ssl.create_default_context(cafile=prepared / "client/ca.pem")
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=tls), trust_env=False,
        ) as session:
            return await PROBE.resource_check(
                session, key, token, jwks, profile=profile)

    with patch.object(PROBE, "ROOT", prepared / "client"):
        result = asyncio.run(exercise())
    assert result["resource_tls_verified"] is True
    assert result["result"] == ("incompatible" if code else "verified")
    assert result["negative_checks"] == dict.fromkeys((
        "wrong_key_refused", "missing_proof_refused", "replay_refused"), code is None)
    if code:
        assert result["identity_error"] == code
    traits = PROBE.token_traits(token, key, profile="enrolled-pop-v1")
    assert traits["client_id_exact"] is (original_claims["client_id"] == PREP.CLIENT)
    assert traits["subject_exact"] is (original_claims["sub"] == PREP.ENROLLED_SUBJECT)
    assert traits["traits_are_unverified"] is True
    assert set(PROBE.token_traits(token, key)) == set(outcome()["traits"]) - {"token_type_dpop"}
    assert all(value not in json.dumps({**result, "traits": traits})
               for value in (token, PREP.CLIENT, PREP.ENROLLED_PRINCIPAL, key.thumbprint()))
    if original_claims["aud"] == [PREP.AUDIENCE] and code is None:
        with patch.object(PROBE, "ROOT", prepared / "client"):
            baseline = asyncio.run(exercise("jwt-pop-v1"))
        assert baseline == {
            "result": "incompatible", "resource_tls_verified": True,
            "identity_error": "invalid-credential",
        }


@pytest.mark.parametrize("change,code", [
    ("signature", "invalid-signature"), ("type", "invalid-credential"),
    ("method", "invalid-proof"), ("target", "invalid-proof"),
    ("hash", "invalid-token-binding"), ("nonce", "invalid-proof"),
])
def test_enrolled_tls_path_keeps_original_signature_and_request_proof_rules(
    prepared: Path, change: str, code: str,
) -> None:
    key = ECKey.import_key(json.loads((prepared / "client/inputs.json").read_bytes())["proof_key"])
    issuer = ECKey.generate_key("P-256")
    now = int(time.time())
    token = jwt.encode(
        {"alg": "ES256", "typ": "JWT" if change == "type" else "at+jwt",
         "kid": PREP.SIGNING_KID},
        {"iss": PREP.ISSUER, "aud": [PREP.AUDIENCE], "sub": PREP.ENROLLED_SUBJECT,
         "client_id": PREP.CLIENT, "iat": now, "exp": now + 120},
        ECKey.generate_key("P-256") if change == "signature" else issuer)
    jwks = {"keys": [{**issuer.as_dict(private=False), "kid": PREP.SIGNING_KID}]}
    sign = PROBE.proof

    def altered(proof_key, method, uri, *, token=None, nonce=None):
        return sign(
            proof_key, "POST" if change == "method" else method,
            uri + "/other" if change == "target" else uri,
            token="different-original-token" if change == "hash" else token,
            nonce="wrong-nonce" if change == "nonce" else nonce)

    async def exercise():
        tls = ssl.create_default_context(cafile=prepared / "client/ca.pem")
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=tls), trust_env=False,
        ) as session:
            return await PROBE.resource_check(
                session, key, token, jwks, profile="enrolled-pop-v1")

    with (patch.object(PROBE, "ROOT", prepared / "client"),
          patch.object(PROBE, "proof", altered)):
        result = asyncio.run(exercise())
    assert result["result"] == "incompatible" and result["identity_error"] == code
    assert result["resource_tls_verified"] is True
    assert not any(result["negative_checks"].values())


def test_enrolled_success_is_withheld_if_a_negative_resource_check_accepts(prepared: Path) -> None:
    key = ECKey.import_key(json.loads((prepared / "client/inputs.json").read_bytes())["proof_key"])
    issuer = ECKey.generate_key("P-256")
    now = int(time.time())
    token = jwt.encode(
        {"alg": "ES256", "typ": "at+jwt", "kid": PREP.SIGNING_KID},
        {"iss": PREP.ISSUER, "aud": [PREP.AUDIENCE], "sub": PREP.ENROLLED_SUBJECT,
         "client_id": PREP.CLIENT, "iat": now, "exp": now + 120}, issuer)
    jwks = {"keys": [{**issuer.as_dict(private=False), "kid": PREP.SIGNING_KID}]}

    async def exercise():
        tls = ssl.create_default_context(cafile=prepared / "client/ca.pem")
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(ssl=tls), trust_env=False,
        ) as session:
            return await PROBE.resource_check(
                session, key, token, jwks, profile="enrolled-pop-v1")

    with (patch.object(PROBE, "ROOT", prepared / "client"),
          patch.object(PROBE.ECKey, "generate_key", return_value=key),
          pytest.raises(PROBE.ProbeError, match="resource-negative-check-failed")):
        asyncio.run(exercise())


class Response:
    def __init__(self, value: dict, status: int = 200, headers: dict | None = None):
        self.raw = json.dumps(value).encode()
        self.status, self.headers = status, headers or {}
        self.content = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def iter_chunked(self, size: int):
        yield self.raw


@pytest.mark.parametrize("profile", ["jwt-pop-v1", "enrolled-pop-v1"])
@pytest.mark.parametrize("mode", ["bound", "unbound", "nonce", "bad-grant", "bearer"])
def test_provider_request_and_outcome_boundaries(
    prepared: Path, mode: str, profile: str,
) -> None:
    inputs = json.loads((prepared / "client/inputs.json").read_bytes())
    key = ECKey.import_key(inputs["proof_key"])
    issuer = ECKey.generate_key("P-256")
    now = int(time.time())
    claims = {"iss": PREP.ISSUER, "aud": PREP.AUDIENCE, "sub": "synthetic-subject",
              "iat": now, "exp": now + 120}
    if profile == "enrolled-pop-v1":
        claims.update(aud=[PREP.AUDIENCE], sub=PREP.ENROLLED_SUBJECT, client_id=PREP.CLIENT)
    if mode != "unbound":
        claims["cnf"] = {"jkt": key.thumbprint()}
    token = jwt.encode({"alg": "ES256", "typ": "at+jwt", "kid": PREP.SIGNING_KID}, claims, issuer)
    jwks = {"keys": [{**issuer.as_dict(private=False), "kid": PREP.SIGNING_KID}]}
    calls = []
    original_session = aiohttp.ClientSession

    class Session:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False
            self.connector = kwargs["connector"]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            await self.connector.close()
            return False

        def get(self, uri: str, **kwargs):
            assert kwargs["allow_redirects"] is False
            if uri.endswith("/.well-known/openid-configuration"):
                return Response({"issuer": PROBE.ISSUER, "token_endpoint": PROBE.TOKEN_ENDPOINT,
                                 "jwks_uri": PROBE.JWKS_ENDPOINT})
            assert uri == PROBE.JWKS_ENDPOINT
            return Response(jwks)

        def post(self, uri: str, **kwargs):
            assert uri == PROBE.TOKEN_ENDPOINT and kwargs["allow_redirects"] is False
            assert kwargs["data"] == {
                "grant_type": "client_credentials", "client_id": PREP.CLIENT,
                "client_secret": inputs["client_secret"], "scope": "fixture.read",
                "audience": PREP.AUDIENCE,
            }
            if profile == "enrolled-pop-v1":
                assert kwargs["headers"] == {}
                signed = None
            else:
                signed = kwargs["headers"]["DPoP"]
                decoded = jwt.decode(signed, key, algorithms=["ES256"])
                assert decoded.header == {
                    "alg": "ES256", "typ": "dpop+jwt", "jwk": key.as_dict(private=False)}
                assert decoded.claims["htu"] == uri and decoded.claims["htm"] == "POST"
                assert "ath" not in decoded.claims
                if calls:
                    assert decoded.claims["nonce"] == "fixture-nonce"
                else:
                    assert "nonce" not in decoded.claims
            calls.append(signed)
            if mode == "nonce" and len(calls) == 1:
                return Response({"error": "use_dpop_nonce"}, 400, {"DPoP-Nonce": "fixture-nonce"})
            if mode == "bad-grant":
                return Response({"error": "invalid_client", "error_description": "never-echo"}, 400)
            return Response({"access_token": token, "token_type": "Bearer" if mode == "bearer"
                             else "DPoP"})

    async def real_resource(_session, proof_key, access, selected, *, profile="jwt-pop-v1"):
        # The acquisition fake still crosses the real serving verifier over strict TLS.
        assert access == token
        tls = ssl.create_default_context(cafile=prepared / "client/ca.pem")
        async with original_session(
            connector=aiohttp.TCPConnector(ssl=tls),
            timeout=aiohttp.ClientTimeout(total=5), trust_env=False,
        ) as session:
            return await real_check(session, proof_key, access, selected, profile=profile)

    real_check = PROBE.resource_check
    with (patch.object(PROBE, "ROOT", prepared / "client"),
          patch.object(PROBE.aiohttp, "ClientSession", Session),
          patch.object(PROBE, "resource_check", real_resource)):
        if mode == "bad-grant" or (profile == "enrolled-pop-v1" and mode == "nonce"):
            with pytest.raises(PROBE.ProbeError, match="provider-token-request-failed"):
                asyncio.run(PROBE.qualify(profile))
            assert len(calls) == 1
        else:
            result = asyncio.run(PROBE.qualify(profile))
            assert result["result"] == (
                "incompatible" if profile == "jwt-pop-v1" and mode in ("unbound", "bearer")
                else "verified")
            assert RUN.admit_outcome(result, profile=profile) == result
            assert result["human_flow_exercised"] is False
            assert len(calls) == (2 if mode == "nonce" else 1)
            assert "never-echo" not in json.dumps(result)
            assert token not in json.dumps(result)
            if profile == "enrolled-pop-v1":
                assert all(result["negative_checks"].values())
                assert result["traits"]["client_id_exact"] is True
                assert result["traits"]["subject_exact"] is True
                assert result["traits"]["audience_allowed"] is True
                assert PREP.CLIENT not in json.dumps(result)
            if mode == "bearer" and profile == "jwt-pop-v1":
                assert result["identity_error"] == "provider-token-type"


@pytest.mark.parametrize("mode", ["missing", "symlink", "changed", "valid"])
def test_probe_materialization_is_pinned_regular_and_same_commit(
    tmp_path: Path, mode: str,
) -> None:
    sha = "d" * 40
    seen = []

    def git(repository: Path, *args: str):
        seen.append(args)
        if args[0] == "ls-tree":
            path = args[-1]
            if mode == "missing":
                return b""
            permission = "120000" if mode == "symlink" else "100644"
            return f"{permission} blob {'e' * 40}\t{path}\0".encode()
        data = (BASE / Path(seen[-2][-1]).name).read_bytes()
        return data + b" " if mode == "changed" else data

    with (patch.object(RUN, "materialize", return_value={"source_commit": sha}),
          patch.object(RUN, "git", git)):
        if mode == "valid":
            result = RUN.materialize_probe(ROOT, sha, tmp_path)
            assert len(result["qualifier_manifest_sha256"]) == 64
            assert {path.name for path in (tmp_path / "qualifier").iterdir()} == RUN.FILES
            assert all(args[2] == sha for args in seen if args[0] == "ls-tree")
        else:
            with pytest.raises(RUN.ProofError):
                RUN.materialize_probe(ROOT, sha, tmp_path)

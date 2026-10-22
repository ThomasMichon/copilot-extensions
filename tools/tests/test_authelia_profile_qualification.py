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


@pytest.mark.parametrize("kind", ["prepare", "provider", "probe"])
def test_closed_container_profile(tmp_path: Path, kind: str) -> None:
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
        assert engine.create_fixture(IMAGE, kind) == CID
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


@pytest.mark.parametrize("mode", ["bound", "unbound", "nonce", "bad-grant", "bearer"])
def test_provider_request_and_outcome_boundaries(prepared: Path, mode: str) -> None:
    inputs = json.loads((prepared / "client/inputs.json").read_bytes())
    key = ECKey.import_key(inputs["proof_key"])
    issuer = ECKey.generate_key("P-256")
    now = int(time.time())
    claims = {"iss": PREP.ISSUER, "aud": PREP.AUDIENCE, "sub": "synthetic-subject",
              "iat": now, "exp": now + 120}
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

    async def real_resource(_session, proof_key, access, selected):
        # The acquisition fake still crosses the real serving verifier over strict TLS.
        tls = ssl.create_default_context(cafile=prepared / "client/ca.pem")
        async with original_session(
            connector=aiohttp.TCPConnector(ssl=tls),
            timeout=aiohttp.ClientTimeout(total=5), trust_env=False,
        ) as session:
            return await real_check(session, proof_key, access, selected)

    real_check = PROBE.resource_check
    with (patch.object(PROBE, "ROOT", prepared / "client"),
          patch.object(PROBE.aiohttp, "ClientSession", Session),
          patch.object(PROBE, "resource_check", real_resource)):
        if mode == "bad-grant":
            with pytest.raises(PROBE.ProbeError, match="provider-token-request-failed"):
                asyncio.run(PROBE.qualify())
            assert len(calls) == 1
        else:
            result = asyncio.run(PROBE.qualify())
            assert result["result"] == ("incompatible" if mode in ("unbound", "bearer")
                                        else "verified")
            assert RUN.admit_outcome(result) == result
            assert result["human_flow_exercised"] is False
            assert len(calls) == (2 if mode == "nonce" else 1)
            assert "never-echo" not in json.dumps(result)
            if mode == "bearer":
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

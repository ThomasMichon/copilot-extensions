import base64
import builtins
import copy
import hashlib
import json
import os
import subprocess
import sys

import pytest

import fleet_manager
from fleet_manager.artifacts import verify_role_artifacts
from fleet_manager.planning import MAX_BYTES, PlanError, canonical
from fleet_manager.release_auth import authenticate_release
from test_release import manifest


# Generated independently by Node's Ed25519 signer; no private signing key is retained.
PUBLIC_KEY = "4QN4yBBQJ08gS75nf5QengnkYuDKdOLqs3WJDIEhbcA="
SIGNATURE = "tx4f6LOvxUy4BtmR5XsYPG0Srody91bR3hF6vw+LAhP+zcYkASrh0gxptPEyno9aSZJMTciw4Nf3QM179A35CA=="


def policy():
    return {
        "schema": "fleet-manager.release-trust-policy", "version": 1,
        "id": "test-policy", "revision": "r1", "trust_revision": "keys-r1",
        "method": "pinned-key", "authority": "test-authority",
        "installation": "test-installation", "role": "client",
        "valid_from": 50, "valid_until": 250, "minimum_sequence": 10,
        "keys": [{"id": "test-key", "public_key": PUBLIC_KEY,
                  "roles": ["client"], "valid_from": 50, "valid_until": 250,
                  "revoked": False}],
    }


def proof():
    return {
        "schema": "fleet-manager.release-proof", "version": 1, "method": "pinned-key",
        "signature": SIGNATURE,
        "payload": {
            "schema": "fleet-manager.release-signature", "version": 1,
            "key_id": "test-key", "authority": "test-authority",
            "release_digest": "650e081d78d1f5cfe27daf444181ace2ab754971294103be1fa060a8961cc2f8",
            "sequence": 10, "issued_at": 100, "expires_at": 200,
        },
    }


def artifact_fixture(tmp_path):
    release = manifest()
    sources = {"schema": "fleet-manager.artifact-sources", "version": 1, "components": {}}
    for name, component in release["components"].items():
        raw = f"fixture artifact: {name}\n".encode("ascii")
        path = tmp_path / name
        path.write_bytes(raw)
        component["artifact_sha256"] = hashlib.sha256(raw).hexdigest()
        sources["components"][name] = str(path)
    trusted, signed = policy(), proof()
    trusted["keys"][0]["public_key"] = "jKIiugI9pUDTLyfIF0JGz/Pn8D9sox7uybCc/qt/sjA="
    signed["signature"] = "5QC9Xr2rqiqts8nS5imK4y+h8flXePL/SoGUPnxhIumXuqFR7uj86UCzFj78Kf4VnC5fUZShzjNTiZgddFkjAg=="
    signed["payload"]["release_digest"] = "d2700c2c2894b2dce89c98b20b70739e4f8a16fc3a7bdd65cafcdbe894554d6e"
    return {"manifest": release, "proof": signed, "policy": trusted, "sources": sources}


def authenticate(raw=None, signed=None, trusted=None, **kwargs):
    return authenticate_release(
        canonical(manifest()) if raw is None else raw,
        proof() if signed is None else signed, policy() if trusted is None else trusted,
        authority=kwargs.pop("authority", "test-authority"),
        installation=kwargs.pop("installation", "test-installation"),
        role=kwargs.pop("role", "client"), now=kwargs.pop("now", 150), **kwargs)


def test_independent_real_signature_and_attributable_non_authorizing_result():
    trusted, signed = policy(), proof()
    before = copy.deepcopy((trusted, signed))
    result = authenticate(trusted=trusted, signed=signed)
    assert (trusted, signed) == before
    assert result["status"] == "authenticated" and result["authenticity_verified"]
    assert result["release_digest"] == hashlib.sha256(canonical(manifest())).hexdigest()
    assert result["policy_revision"] == "r1" and result["trust_revision"] == "keys-r1"
    assert result["policy_digest"] == hashlib.sha256(canonical(trusted)).hexdigest()
    assert result["signer_key_sha256"] == hashlib.sha256(base64.b64decode(PUBLIC_KEY)).hexdigest()
    assert result["sequence"] == 10 and result["verified_at"] == 150
    assert result["valid_until"] == 200
    for field in ("artifact_integrity_verified", "activation_authorized",
                  "live_readiness_verified", "mutations_performed"):
        assert result[field] is False


def test_actual_manifest_tamper_even_with_same_display_id_is_refused():
    changed = manifest()
    changed["components"]["core"]["artifact_sha256"] = "d" * 64
    with pytest.raises(PlanError, match="signed manifest digest mismatch"):
        authenticate(raw=canonical(changed))


@pytest.mark.parametrize("change", [
    {"signature": base64.b64encode(b"\0" * 64).decode("ascii")},
    {"payload": {**proof()["payload"], "sequence": 11}},
    {"payload": {**proof()["payload"], "issued_at": 101}},
    {"payload": {**proof()["payload"], "expires_at": 199}},
])
def test_real_signature_rejects_changed_signed_bytes(change):
    signed = proof()
    signed.update(change)
    with pytest.raises(PlanError, match="signature verification failed"):
        authenticate(signed=signed)


def test_wrong_configured_public_key_fails_real_crypto():
    trusted = policy()
    trusted["keys"][0]["public_key"] = base64.b64encode(b"\x01" * 32).decode("ascii")
    with pytest.raises(PlanError, match="signature verification failed"):
        authenticate(trusted=trusted)


@pytest.mark.parametrize("field,value,error", [
    ("revoked", True, "revoked or outside role scope"),
    ("roles", ["gateway"], "revoked or outside role scope"),
    ("valid_from", 101, "exceeds signing key validity"),
    ("valid_until", 199, "exceeds signing key validity"),
    ("id", "untrusted-key", "not configured"),
])
def test_trusted_key_restrictions_cannot_be_overridden(field, value, error):
    trusted = policy()
    trusted["keys"][0][field] = value
    with pytest.raises(PlanError, match=error):
        authenticate(trusted=trusted)


@pytest.mark.parametrize("now,error", [
    (49, "policy is not currently valid"), (250, "policy is not currently valid"),
    (99, "signature is not currently valid"), (200, "signature is not currently valid"),
])
def test_signed_and_policy_time_windows_are_half_open(now, error):
    with pytest.raises(PlanError, match=error):
        authenticate(now=now)


def test_trust_policy_expiry_bounds_historical_report():
    trusted = policy()
    trusted["valid_until"] = 175
    assert authenticate(trusted=trusted)["valid_until"] == 175
    with pytest.raises(PlanError, match="policy is not currently valid"):
        authenticate(trusted=trusted, now=175)


@pytest.mark.parametrize("now", [100, 199])
def test_signature_validity_boundary_acceptance(now):
    assert authenticate(now=now)["authenticity_verified"] is True


@pytest.mark.parametrize("now", [True, 1.5, -1, 2**63])
def test_invalid_verification_time_refuses(now):
    with pytest.raises(PlanError, match="verification time must be an integer"):
        authenticate(now=now)


@pytest.mark.parametrize("missing", ["policy", "proof"])
def test_absent_explicit_inputs_do_not_default(missing):
    with pytest.raises(PlanError, match="input must be an object"):
        authenticate_release(canonical(manifest()), None if missing == "proof" else proof(),
                             None if missing == "policy" else policy(),
                             authority="test-authority", installation="test-installation",
                             role="client", now=150)


def test_replay_below_explicit_floor_refuses_valid_signature():
    trusted = policy()
    trusted["minimum_sequence"] = 11
    with pytest.raises(PlanError, match="below configured sequence floor"):
        authenticate(trusted=trusted)


@pytest.mark.parametrize("kwargs", [
    {"authority": "other"}, {"installation": "other"}, {"role": "gateway"},
])
def test_caller_scope_must_match_explicit_policy(kwargs):
    with pytest.raises(PlanError, match="policy scope mismatch"):
        authenticate(**kwargs)


@pytest.mark.parametrize("raw", [
    json.dumps(manifest()).encode(),
    canonical(manifest()) + b"\n",
    canonical(manifest()).replace(b'"version":1', b'"version":1,"version":1', 1),
    b" " * (MAX_BYTES + 1),
], ids=["spaced-json", "trailing-newline", "duplicate-key", "oversized"])
def test_ambiguous_or_oversized_manifest_bytes_refused(raw):
    with pytest.raises(PlanError):
        authenticate(raw=raw)


@pytest.mark.parametrize("field,value", [
    ("signature", SIGNATURE.rstrip("=")),
    ("signature", SIGNATURE[:-3] + "D=="),
    ("signature", ["not-a-string"]),
])
def test_signature_encoding_is_exact_standard_base64(field, value):
    signed = proof()
    signed[field] = value
    with pytest.raises(PlanError, match="signature encoding"):
        authenticate(signed=signed)


@pytest.mark.parametrize("change", [
    {"method": "auto"}, {"method": ["pinned-key"]}, {"keys": []},
    {"minimum_sequence": True}, {"revision": None},
    {"keys": [policy()["keys"][0], policy()["keys"][0]]},
])
def test_missing_unknown_or_ambiguous_trusted_policy_refused(change):
    trusted = policy()
    trusted.update(change)
    with pytest.raises(PlanError):
        authenticate(trusted=trusted)


def test_selected_github_method_never_falls_back_to_valid_pinned_proof():
    trusted = policy()
    trusted["method"] = "github-attestation"
    with pytest.raises(PlanError, match="GitHub attestation verification capability is not implemented"):
        authenticate(trusted=trusted)
    signed = proof()
    signed["method"] = "github-attestation"
    with pytest.raises(PlanError, match="does not match required authentication method"):
        authenticate(signed=signed)


@pytest.mark.parametrize("error_type", [ImportError, ModuleNotFoundError])
def test_incomplete_or_incompatible_crypto_import_refuses_without_private_details(monkeypatch, error_type):
    original = builtins.__import__

    def incompatible(name, *args, **kwargs):
        if name == "cryptography" or name.startswith("cryptography."):
            raise error_type("private-native-component-path")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", incompatible)
    with pytest.raises(PlanError, match="verification capability is unavailable") as error:
        authenticate()
    assert "private-native-component-path" not in str(error.value)


def test_proof_cannot_supply_its_own_key_or_expand_trusted_scope():
    signed = proof()
    signed["public_key"] = PUBLIC_KEY
    with pytest.raises(PlanError, match="missing or unsupported fields"):
        authenticate(signed=signed)
    signed = proof()
    signed["payload"]["authority"] = "other"
    with pytest.raises(PlanError, match="signed release authority mismatch"):
        authenticate(signed=signed)


@pytest.mark.parametrize("role", ["client", "gateway", "node"])
def test_authenticated_manifest_binds_actual_selected_artifact_bytes(tmp_path, role):
    value = artifact_fixture(tmp_path)
    value["policy"]["role"] = role
    value["policy"]["keys"][0]["roles"] = [role]
    result = authenticate(raw=canonical(value["manifest"]), trusted=value["policy"],
                          signed=value["proof"], role=role)
    preflight = verify_role_artifacts(value["manifest"], value["sources"], role=role)
    assert result["release_digest"] == preflight["release_digest"]
    assert {item["component"] for item in preflight["components"]} == set(value["manifest"]["roles"][role])
    assert result["activation_authorized"] is False and preflight["activation_authorized"] is False
    assert result["artifact_integrity_verified"] is False
    selected = value["manifest"]["roles"][role][0]
    (tmp_path / selected).write_bytes(b"tampered")
    with pytest.raises(PlanError, match="artifact digest mismatch"):
        verify_role_artifacts(value["manifest"], value["sources"], role=role)
    assert authenticate(raw=canonical(value["manifest"]), trusted=value["policy"],
                        signed=value["proof"], role=role)["authenticity_verified"]


def test_installed_offline_crypto_and_missing_capability_outside_checkout(tmp_path):
    inputs = tmp_path / "inputs.json"
    inputs.write_bytes(canonical(artifact_fixture(tmp_path)))
    code = """
import importlib.util, json, pathlib, socket, sys
if sys.argv[2] == "missing":
    assert importlib.util.find_spec("cryptography") is None
    package_path = pathlib.Path(sys.argv[3])
    spec = importlib.util.spec_from_file_location("fleet_manager", package_path,
        submodule_search_locations=[str(package_path.parent)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["fleet_manager"] = module
    spec.loader.exec_module(module)
from fleet_manager.planning import PlanError, canonical
from fleet_manager.artifacts import verify_role_artifacts
from fleet_manager.release_auth import authenticate_release
def deny(*args, **kwargs):
    raise AssertionError("network is forbidden")
socket.socket = deny
socket.create_connection = deny
socket.getaddrinfo = deny
value = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
try:
    result = authenticate_release(canonical(value["manifest"]), value["proof"], value["policy"],
        authority="test-authority", installation="test-installation", role="client", now=150)
    integrity = verify_role_artifacts(value["manifest"], value["sources"], role="client")
except PlanError as exc:
    print(str(exc))
    sys.exit(1)
print(json.dumps({"authentication": result, "integrity": integrity}))
"""
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    success = subprocess.run([sys.executable, "-I", "-c", code, str(inputs), "offline"],
                             cwd=tmp_path, capture_output=True, text=True, timeout=20, **flags)
    assert success.returncode == 0, success.stderr + success.stdout
    results = json.loads(success.stdout)
    assert results["authentication"]["authenticity_verified"] is True
    assert results["authentication"]["release_digest"] == results["integrity"]["release_digest"]
    assert results["integrity"]["status"] == "integrity-match"
    missing = subprocess.run([sys.executable, "-I", "-S", "-c", code, str(inputs),
                              "missing", fleet_manager.__file__],
                             cwd=tmp_path, capture_output=True, text=True, timeout=20, **flags)
    assert missing.returncode == 1
    assert "selected pinned-key verification capability is unavailable" in missing.stdout
    assert "authenticated" not in missing.stdout and not missing.stderr

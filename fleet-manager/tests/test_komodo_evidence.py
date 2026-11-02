"""Public identity parsing and exact versus semantic native custody controls."""

from __future__ import annotations

import base64
from copy import deepcopy
import sys
from typing import Any

from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat
import pytest

from fleet_manager.komodo_evidence import (
    FIELDS, legacy_evidence_unproved, native_fingerprint, native_matches, native_set_matches,
    native_subset_matches, same_native_observation,
)
from fleet_manager.komodo_identity import effective_global_key, public_identity
from fleet_manager.komodo_health import identity_digest
from fleet_manager.komodo_owner import KomodoOwner, verify_candidate
from fleet_manager.komodo_selection import KomodoSelection
from fleet_manager.komodo_state import KomodoState
from fleet_manager.planning import PlanError, digest
from komodo_public_fixtures import PUBLIC_DER, PUBLIC_PEM
from test_komodo_native import native_rig as native_rig


@pytest.mark.parametrize("encoding", ["pem", "der", "crlf", "rsa"])
def test_validated_spki_normalizes_only_public_encoding(encoding: str) -> None:
    if encoding == "rsa":
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key()
        pem = key.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo).decode()
        der = key.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        assert public_identity(pem) == public_identity(base64.b64encode(der).decode()) == der
    else:
        value = PUBLIC_PEM["periphery"] if encoding != "der" else PUBLIC_DER["periphery"]
        if encoding == "crlf":
            value = value.replace("\n", "\r\n")
        assert public_identity(value) == base64.b64decode(PUBLIC_DER["periphery"])


@pytest.mark.parametrize("value", [
    None, "", "opaque-key", "-----BEGIN PRIVATE KEY-----\nx\n-----END PRIVATE KEY-----",
    PUBLIC_DER["core"] + "\n" + PUBLIC_DER["core"],
    PUBLIC_PEM["core"] + PUBLIC_PEM["periphery"], PUBLIC_DER["core"] + "=",
    base64.b64encode(base64.b64decode(PUBLIC_DER["core"]) + b"\x00").decode(),
    PUBLIC_PEM["core"].replace("PUBLIC KEY", "CERTIFICATE"),
])
def test_public_identity_refuses_invalid_or_non_spki_input(value: object) -> None:
    with pytest.raises(PlanError, match="encoding"):
        public_identity(value)


def test_public_identity_refuses_valid_private_and_pkcs1_material() -> None:
    private = ed25519.Ed25519PrivateKey.generate().private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode()
    pkcs1 = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key().public_bytes(
        Encoding.DER, PublicFormat.PKCS1)
    for value in (private, base64.b64encode(pkcs1).decode()):
        with pytest.raises(PlanError, match="encoding"):
            public_identity(value)


@pytest.mark.parametrize("mode", ["duplicate-env", "duplicate-mount", "other-file", "wrong-source"])
@pytest.mark.skipif(sys.platform != "linux", reason="Linux selected native key mount")
def test_global_trust_refuses_ambiguous_or_unproved_native_reference(native_rig: Any, mode: str) -> None:
    material = native_rig["material"]
    core = {"Config": {"Env": ["KOMODO_PERIPHERY_PUBLIC_KEY=file:/config/keys/periphery.pub"]},
            "Mounts": [{"Source": str(material.keys), "Destination": "/config/keys", "Type": "bind"}]}
    if mode == "duplicate-env":
        core["Config"]["Env"] *= 2
    elif mode == "duplicate-mount":
        core["Mounts"] *= 2
    elif mode == "other-file":
        core["Config"]["Env"] = ["KOMODO_PERIPHERY_PUBLIC_KEY=file:/other/key.pub"]
    else:
        core["Mounts"][0]["Source"] = str(material.backups)
    with pytest.raises(PlanError, match="ambiguous|unproved"):
        effective_global_key(core, material, PUBLIC_PEM["periphery"])


def container() -> dict[str, Any]:
    return {
        "Id": "a" * 64, "Image": "sha256:" + "b" * 64,
        "Config": {"Env": ["SENSITIVE=never-persist"], "Labels": {"owner": "selected"}},
        "HostConfig": {"Privileged": False},
        "Mounts": [{"Type": "bind", "Source": "/keys", "Destination": "/config/keys",
                    "RW": True, "Propagation": "rprivate"},
                   {"Type": "bind", "Source": "/data", "Destination": "/data/db", "RW": True}],
        "RestartCount": 0, "State": {"Pid": 101, "StartedAt": "start-a", "Running": True},
    }


def test_paired_and_versioned_receipts_ignore_only_mount_order_and_preserve_legacy() -> None:
    before = container()
    after = deepcopy(before)
    after["Mounts"].reverse()
    legacy = digest({name: before[name] for name in FIELDS})
    reference = native_fingerprint(before)
    assert reference.startswith("mounts-v2:")
    assert native_matches(reference, before) and native_matches(reference, after)
    assert native_matches(legacy, before) and not native_matches(legacy, after)
    assert native_set_matches({"core": reference}, {"core": after})
    assert not native_set_matches({"core": reference}, {})
    assert native_subset_matches({"core": reference, "mongo": reference}, {"core": after})
    assert not native_subset_matches({"mongo": reference}, {"core": after})
    assert not legacy_evidence_unproved({"core": reference}, {"core": after})
    assert legacy_evidence_unproved({"core": legacy}, {"core": after})
    assert not legacy_evidence_unproved({"core": legacy}, {"core": before})
    assert same_native_observation({"core": before}, {"core": after})
    assert native_fingerprint(before) == reference and legacy == digest(
        {name: before[name] for name in FIELDS})


@pytest.mark.parametrize("field", ["Source", "Destination", "Type", "RW", "Propagation", "extra"])
def test_native_evidence_preserves_every_mount_field(field: str) -> None:
    before = container()
    after = deepcopy(before)
    after["Mounts"][0][field] = False if field == "RW" else "changed"
    assert not native_matches(native_fingerprint(before), after)
    assert not same_native_observation({"core": before}, {"core": after})


@pytest.mark.parametrize("field", ["Id", "Image", "Config", "HostConfig", "Pid", "StartedAt", "RestartCount"])
def test_paired_observation_retains_resource_and_process_fences(field: str) -> None:
    before = container()
    after = deepcopy(before)
    if field in ("Pid", "StartedAt"):
        after["State"][field] = 102 if field == "Pid" else "start-b"
    else:
        after[field] = {"different": True} if field in ("Config", "HostConfig") else "changed"
    assert not same_native_observation({"core": before}, {"core": after})


@pytest.mark.parametrize("mode", ["duplicate", "missing", "null"])
def test_native_evidence_refuses_duplicate_or_ambiguous_mount_destinations(mode: str) -> None:
    observed = container()
    if mode == "duplicate":
        observed["Mounts"].append(deepcopy(observed["Mounts"][0]))
    elif mode == "missing":
        observed["Mounts"][0].pop("Destination")
    else:
        observed["Mounts"][0]["Destination"] = None
    with pytest.raises(PlanError, match="ambiguous"):
        native_fingerprint(observed)


@pytest.mark.skipif(sys.platform != "linux", reason="Linux incumbent owner evidence")
def test_legacy_adoption_and_original_candidate_refuse_unproved_semantics(native_rig: Any) -> None:
    rig = native_rig
    for name in ("core", "periphery"):
        (rig["material"].keys / (name + ".pub")).write_text(PUBLIC_PEM[name])
    root = rig["root"] / "legacy-owner"
    root.mkdir(mode=0o700)
    before = {name: container() for name in ("core", "mongo", "periphery")}
    for index, item in enumerate(before.values()):
        item["Id"] = str(index + 1) * 64
    references = {name: digest({field: item[field] for field in FIELDS})
                  for name, item in before.items()}
    after = deepcopy(before)
    for item in after.values():
        item["Mounts"].reverse()
    selected = KomodoSelection(
        rig["selected"], "fleet-a", "install-a", 1, "policy-a", 1900001000,
        "e" * 64, root, rig["tooling"], rig["material"],
        adoption=tuple(references.items()), adopted_public_identity=identity_digest(rig["material"]),
        adopted_network="a" * 64)
    attempt = {"public_keys_before": {}, "before": references, "fixed_profile": False}
    with pytest.raises(PlanError, match="legacy native evidence does not prove"):
        verify_candidate(after, selected, {}, selected.profile(), attempt)
    with KomodoState.open(root, daemon="daemon-a", project=selected.declaration.to_dict()["project"]) as state:
        with pytest.raises(PlanError, match="legacy native evidence does not prove"):
            KomodoOwner._baseline(state, selected, after)
    assert not (rig["directory"] / "calls.jsonl").exists()

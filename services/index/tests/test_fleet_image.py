"""Explicit first-party fleet closure, without enrollment or image activation."""

from __future__ import annotations

import hashlib
import json

import pytest
import test_release as wheels

from agent_index_service.__main__ import main
from agent_index_service.container import (
    ContainerError,
    ContainerTarget,
    prepare_container_context,
    verified_context_snapshot,
)
from agent_index_service.release import ReleaseError, build_descriptor, verify_descriptor


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    monkeypatch.setitem(wheels.EXTRAS, "agent-index-service", ["native", "fleet"])
    monkeypatch.setitem(wheels.EXTRAS, "agent-index", ["store", "server", "fleet"])
    monkeypatch.setitem(wheels.EXTRAS, "fleet-manager", ["auth"])
    monkeypatch.setitem(wheels.REQUIREMENTS, "agent-index-service", [
        *wheels.REQUIREMENTS["agent-index-service"],
        'agent-index[store,server,fleet]>=0.10.12.dev1; extra == "fleet"',
    ])
    monkeypatch.setitem(wheels.REQUIREMENTS, "agent-index", [
        *wheels.REQUIREMENTS["agent-index"],
        'fleet-manager[auth]>=0.1; extra == "fleet"',
    ])
    monkeypatch.setitem(wheels.REQUIREMENTS, "fleet-manager", [
        'cryptography>=44; extra == "auth"',
    ])
    bundle, third = tmp_path / "bundle", tmp_path / "third"
    bundle.mkdir()
    third.mkdir()
    for name, version in {**wheels.VERSIONS, "fleet-manager": "0.1"}.items():
        wheels._make_wheel(bundle, name, version)
    lock = tmp_path / "third-party.txt"
    lines = []
    for name, version in (("httpx", "0.28.0"), ("pyyaml", "6.0.3"), ("cryptography", "46.0.0")):
        artifact = wheels._make_wheel(third, name, version)
        lines.append(
            f"{name}=={version} --hash=sha256:{hashlib.sha256(artifact.read_bytes()).hexdigest()}\n"
        )
    lock.write_text("".join(lines))
    descriptor = wheels._save(
        bundle, build_descriptor(bundle, source_commit=wheels.COMMIT, profile="fleet"),
    )
    return {
        "descriptor_path": descriptor, "expected_source_commit": wheels.COMMIT,
        "third_party_lock": lock, "third_party_wheels": third, "output": tmp_path / "image",
        "base_image": "approved.invalid/python@sha256:" + "a" * 64,
        "target": ContainerTarget("3.12.3", "linux/amd64", "2.36"), "profile": "fleet",
    }


def prepare(fleet, **changes):
    values = {**fleet, **changes}
    descriptor = values.pop("descriptor_path")
    return prepare_container_context(descriptor, **values)


def test_explicit_fleet_descriptor_round_trip_and_default_refusal(fleet):
    path = fleet["descriptor_path"]
    result = verify_descriptor(path, expected_source_commit=wheels.COMMIT, profile="fleet")
    assert result["schema_version"] == 2 and result["profile"] == "fleet"
    assert {item["distribution"] for item in result["artifacts"]} == (
        set(wheels.VERSIONS) | {"fleet-manager"}
    )
    for operation in (
        lambda: verify_descriptor(path, expected_source_commit=wheels.COMMIT),
        lambda: build_descriptor(path.parent, source_commit=wheels.COMMIT),
        lambda: prepare(fleet, profile="native"),
    ):
        with pytest.raises((ReleaseError, ContainerError)):
            operation()
    assert not fleet["output"].exists()


def test_fleet_context_exact_extras_snapshot_and_replay(fleet):
    result = prepare(fleet)
    context = fleet["output"]
    contract = json.loads((context / "context.json").read_text())
    assert contract["schema_version"] == 2 and contract["profile"] == "fleet"
    assert contract["extras"]["agent-index-service"] == ["fleet", "native"]
    assert contract["extras"]["agent-index"] == ["fleet", "store", "server"]
    assert contract["extras"]["fleet-manager"] == ["auth"]
    assert set(contract["pins"]) == set(wheels.VERSIONS) | {
        "fleet-manager", "httpx", "pyyaml", "cryptography",
    }
    assert "fleet-manager[auth]==0.1 --hash=sha256:" in (context / "install.txt").read_text()
    assert json.loads((context / "release.json").read_text())["profile"] == "fleet"
    probe = (context / "native-probe.py").read_text()
    for module in ("agent_index.fleet_backend", "agent_index.fleet_http",
                   "agent_index.fleet_tls", "fleet_manager.gateway_auth"):
        assert module in probe
    compile(probe, "native-probe.py", "exec")
    with verified_context_snapshot(
        context, expected_contract_sha256=result["contract_sha256"],
    ) as (verified, snapshot):
        assert snapshot.read(1) and verified == contract
    replay = prepare(fleet)
    assert replay["state"] == "already_prepared"
    assert replay["contract_sha256"] == result["contract_sha256"]


@pytest.mark.parametrize("field,value", [
    ("profile", "native"), ("profile", "unknown"), ("schema_version", 1),
    ("schema_version", True),
])
def test_profile_and_schema_are_not_inferred(fleet, field, value):
    path = fleet["descriptor_path"]
    descriptor = json.loads(path.read_text())
    descriptor[field] = value
    path.write_text(json.dumps(descriptor))
    with pytest.raises(ReleaseError):
        verify_descriptor(path, expected_source_commit=wheels.COMMIT, profile="fleet")


@pytest.mark.parametrize("distribution,old,new", [
    ("agent-index-service", "[store,server,fleet]", "[store,server]"),
    ("agent-index", "fleet-manager[auth]", "fleet-manager"),
    ("fleet-manager", "Provides-Extra: auth", "Provides-Extra: other"),
    ("agent-index", 'extra == "fleet"', 'extra == "other"'),
    ("agent-index", "fleet-manager[auth]>=0.1", "fleet-manager[auth]>=9"),
])
def test_fleet_requires_declared_compatible_extra_chain(fleet, distribution, old, new):
    bundle = fleet["descriptor_path"].parent
    wheels._mutate_metadata(bundle, lambda text: text.replace(old, new), name=distribution)
    with pytest.raises(ReleaseError):
        build_descriptor(bundle, source_commit=wheels.COMMIT, profile="fleet")


def test_missing_first_party_and_wrong_revision_refused(fleet):
    path = fleet["descriptor_path"]
    with pytest.raises(ReleaseError, match="trusted revision"):
        verify_descriptor(path, expected_source_commit="b" * 40, profile="fleet")
    wheels._find(path.parent, "fleet-manager").unlink()
    with pytest.raises(ReleaseError, match="six"):
        build_descriptor(path.parent, source_commit=wheels.COMMIT, profile="fleet")


def test_auth_transitive_closure_must_be_present(fleet):
    wheels._find(fleet["third_party_wheels"], "cryptography").unlink()
    with pytest.raises(ContainerError, match="closure"):
        prepare(fleet)
    assert not fleet["output"].exists()


def test_fleet_manager_cannot_be_disguised_as_third_party(fleet):
    wheel = wheels._make_wheel(fleet["third_party_wheels"], "fleet-manager", "0.1")
    with fleet["third_party_lock"].open("a") as stream:
        stream.write(f"fleet-manager==0.1 --hash=sha256:{hashlib.sha256(wheel.read_bytes()).hexdigest()}")
    with pytest.raises(ContainerError, match="first-party"):
        prepare(fleet)
    assert not fleet["output"].exists()


def test_fleet_cli_selects_profile_at_both_release_boundaries(fleet, capsys):
    path = fleet["descriptor_path"]
    assert main(["release", "describe", "--bundle", str(path.parent),
                 "--source-commit", wheels.COMMIT, "--profile", "fleet"]) == 0
    assert json.loads(capsys.readouterr().out)["profile"] == "fleet"
    assert main(["release", "verify", "--descriptor", str(path),
                 "--expected-source-commit", wheels.COMMIT, "--profile", "fleet"]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True
    assert main(["release", "verify", "--descriptor", str(path),
                 "--expected-source-commit", wheels.COMMIT]) == 2
    assert "agent-index-service:" in capsys.readouterr().err


def test_fleet_cli_prepares_digest_pinned_context(fleet, capsys):
    arguments = ["container", "prepare", "--profile", "fleet"]
    for name in ("descriptor_path", "expected_source_commit", "third_party_lock",
                 "third_party_wheels", "output", "base_image"):
        flag = "descriptor" if name == "descriptor_path" else name.replace("_", "-")
        arguments.extend([f"--{flag}", str(fleet[name])])
    for name in ("python_version", "platform", "glibc_version"):
        arguments.extend([f"--{name.replace('_', '-')}", getattr(fleet["target"], name)])
    assert main(arguments) == 0
    result = json.loads(capsys.readouterr().out)
    with verified_context_snapshot(
        fleet["output"], expected_contract_sha256=result["contract_sha256"],
    ) as (contract, _):
        assert contract["profile"] == "fleet"

"""Actual-file release production without publication or lifecycle effects."""

import copy
import hashlib

import pytest

from fleet_manager import release_assembly as assembly
from fleet_manager.artifacts import verify_role_artifacts
from fleet_manager.planning import PlanError, canonical, decode
from fleet_manager.release import validate_release


def inputs(tmp_path):
    components = {
        "cli": {"kind": "client", "api": {"min": 1, "max": 2}, "data_schemas": [1]},
        "gateway": {"kind": "control", "api": {"min": 1, "max": 1}, "data_schemas": [1]},
        "ssh": {"kind": "executor", "api": {"min": 1, "max": 1}, "data_schemas": [1]},
        "bastion": {"kind": "control", "api": {"min": 1, "max": 1}, "data_schemas": [1]},
    }
    files = {}
    for name in components:
        path = tmp_path / f"{name}.artifact"
        path.write_bytes(f"synthetic {name} artifact\n".encode())
        files[name] = str(path)
    sources = {"schema": "fleet-manager.artifact-sources", "version": 1, "components": files}
    roles = {
        "client": ["cli"], "gateway": ["cli", "gateway", "ssh", "bastion"],
        "node": ["cli", "ssh"],
    }
    return components, sources, roles


def assemble(components, sources, roles):
    return assembly.assemble_release_manifest(
        components, sources, release_id="test-release", roles=roles,
    )


def test_release_assembly_binds_actual_bytes_and_existing_complete_role_contract(tmp_path):
    components, sources, roles = inputs(tmp_path)
    original = copy.deepcopy((components, sources, roles))
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    encoded = assemble(components, sources, roles)
    assert type(encoded) is bytes
    manifest = decode(encoded)
    assert canonical(manifest) == encoded
    assert validate_release(manifest) == manifest
    assert manifest["roles"] == roles
    for name, declaration in manifest["components"].items():
        expected = hashlib.sha256(f"synthetic {name} artifact\n".encode()).hexdigest()
        assert declaration["artifact_sha256"] == expected
        assert {key: value for key, value in declaration.items() if key != "artifact_sha256"} == (
            components[name]
        )
    for role in roles:
        report = verify_role_artifacts(manifest, sources, role=role)
        assert report["status"] == "integrity-match"
        assert not report["authenticity_verified"]
        assert not report["activation_authorized"] and not report["live_readiness_verified"]
        assert not report["mutations_performed"]
    assert (components, sources, roles) == original
    assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")) == before


def test_release_assembly_is_deterministic_without_reordering_explicit_role_closures(tmp_path):
    components, sources, roles = inputs(tmp_path)
    expected = assemble(components, sources, roles)
    components = dict(reversed(list(components.items())))
    sources["components"] = dict(reversed(list(sources["components"].items())))
    assert assemble(components, sources, roles) == expected
    roles["gateway"].reverse()
    assert decode(assemble(components, sources, roles))["roles"]["gateway"] == roles["gateway"]


def test_release_assembly_does_not_require_an_optional_bastion_component(tmp_path):
    components, sources, roles = inputs(tmp_path)
    components.pop("bastion")
    sources["components"].pop("bastion")
    roles["gateway"].remove("bastion")
    manifest = decode(assemble(components, sources, roles))
    assert "bastion" not in manifest["components"]
    assert verify_role_artifacts(manifest, sources, role="gateway")["status"] == "integrity-match"


@pytest.mark.parametrize("mutation", [
    lambda c, s, r: c["cli"].update(kind="control"),
    lambda c, s, r: c["gateway"].update(artifact_sha256="a" * 64),
    lambda c, s, r: c["gateway"].update(api={"min": True, "max": 2}),
    lambda c, s, r: c["gateway"].update(data_schemas=[False]),
    lambda c, s, r: r.update(client=["missing"]),
    lambda c, s, r: r.update(node=[]),
    lambda c, s, r: r.update(other=["cli"]),
    lambda c, s, r: r.update(client=["cli", "cli"]),
    lambda c, s, r: c.update(unreferenced=copy.deepcopy(c["gateway"])),
    lambda c, s, r: s.update(version=True),
    lambda c, s, r: s["components"].pop("ssh"),
    lambda c, s, r: s["components"].update(extra="not-read"),
    lambda c, s, r: s["components"].update(cli=""),
    lambda c, s, r: s["components"].update(cli=True),
    lambda c, s, r: s["components"].update(cli="bad\0path"),
    lambda c, s, r: s["components"].update(cli="a" * 4097),
    lambda c, s, r: s["components"].update(cli="\ud800"),
])
def test_release_assembly_rejects_invalid_inputs_before_artifact_access(tmp_path, monkeypatch, mutation):
    components, sources, roles = inputs(tmp_path)
    mutation(components, sources, roles)

    def unread(*args):
        raise AssertionError("invalid assembly input reached file access")

    monkeypatch.setattr(assembly, "_artifact", unread)
    with pytest.raises(PlanError):
        assemble(components, sources, roles)


@pytest.mark.parametrize("value", [None, [], True, {1: {}}])
def test_release_assembly_requires_bounded_component_objects(value):
    with pytest.raises(PlanError):
        assemble(value, {}, {})


@pytest.mark.parametrize("kind", ["empty", "directory", "missing"])
def test_release_assembly_preserves_existing_ordinary_file_refusals(tmp_path, kind):
    components, sources, roles = inputs(tmp_path)
    target = tmp_path / "invalid"
    if kind == "empty":
        target.write_bytes(b"")
    elif kind == "directory":
        target.mkdir()
    sources["components"]["cli"] = str(target)
    with pytest.raises(PlanError):
        assemble(components, sources, roles)


def test_release_assembly_bounds_the_whole_release_not_each_role_independently(tmp_path, monkeypatch):
    components, sources, roles = inputs(tmp_path)
    monkeypatch.setattr(assembly, "MAX_TOTAL_BYTES", 32)
    with pytest.raises(PlanError, match="byte bounds"):
        assemble(components, sources, roles)


def test_release_assembly_snapshots_input_before_reading_artifacts(tmp_path, monkeypatch):
    components, sources, roles = inputs(tmp_path)
    expected = assemble(components, sources, roles)
    original = assembly._artifact

    def changed(path, remaining):
        components["cli"]["kind"] = "executor"
        sources["components"]["gateway"] = "must-not-be-opened"
        roles["client"] = ["gateway"]
        return original(path, remaining)

    monkeypatch.setattr(assembly, "_artifact", changed)
    assert assemble(components, sources, roles) == expected


def test_release_assembly_does_not_turn_changed_artifacts_into_authenticated_content(tmp_path):
    components, sources, roles = inputs(tmp_path)
    manifest = decode(assemble(components, sources, roles))
    (tmp_path / "gateway.artifact").write_bytes(b"changed after assembly")
    with pytest.raises(PlanError, match="digest mismatch"):
        verify_role_artifacts(manifest, sources, role="gateway")
    assert verify_role_artifacts(manifest, sources, role="client")["status"] == "integrity-match"

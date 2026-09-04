from __future__ import annotations

from pathlib import Path

import pytest

from agent_machines.manifest import (
    ManifestError,
    SCHEMA_VERSION,
    load_package,
    resolve_for_machine,
)

from ._helpers import base_package, write_package


def test_load_and_gate(tmp_path):
    path = write_package(tmp_path, "defaults.yaml", base_package())
    pkg = load_package(path, source_repo="acme")
    assert pkg.name == "acme/copilot-defaults"
    assert pkg.source_repo == "acme"
    assert pkg.applies_to("box-1")
    assert not pkg.applies_to("box-2")


def test_wildcard_gate_applies_everywhere(tmp_path):
    path = write_package(tmp_path, "d.yaml", base_package(gate=["*"]))
    pkg = load_package(path)
    assert pkg.applies_to("anything")


def test_gate_match_is_case_insensitive(tmp_path):
    # platform.node() returns the OS-cased hostname (e.g. "Box-1"/"BOX-1"),
    # but manifests list gates in lowercase ("box-1"). The gate must still match
    # regardless of casing, or a machine is silently excluded from its own package.
    path = write_package(tmp_path, "d.yaml", base_package(gate=["box-1"]))
    pkg = load_package(path)
    assert pkg.applies_to("box-1")
    assert pkg.applies_to("Box-1")
    assert pkg.applies_to("BOX-1")
    assert not pkg.applies_to("box-2")


def test_gate_and_overlay_share_casefold_semantics(tmp_path):
    data = base_package(
        gate=["Straße"],
        **{
            "per-machine": {
                "Straße": {
                    "manage": {
                        "copilot.settings": {"values": {"effortLevel": "low"}}
                    }
                }
            }
        },
    )
    path = write_package(tmp_path, "d.yaml", data)
    package = load_package(path)

    assert package.applies_to("STRASSE")
    resolved = resolve_for_machine(package, "STRASSE")
    assert resolved.manage["copilot.settings"]["values"]["effortLevel"] == "low"


@pytest.mark.parametrize("schema_version", [99, True, 1.0, "2"])
def test_bad_schema_version_rejected(tmp_path, schema_version):
    path = write_package(
        tmp_path,
        "d.yaml",
        base_package(schema_version=schema_version),
    )
    with pytest.raises(ManifestError):
        load_package(path)


def test_legacy_schema_version_remains_readable(tmp_path):
    path = write_package(tmp_path, "d.yaml", base_package(schema_version=1))

    package = load_package(path)

    assert SCHEMA_VERSION == 4
    assert package.schema_version == 1


def test_schema_v2_remains_readable(tmp_path):
    path = write_package(tmp_path, "d.yaml", base_package(schema_version=2))
    assert load_package(path).schema_version == 2


def test_schema_v3_ensure_absent_loads(tmp_path):
    data = base_package(schema_version=3)
    data["manage"] = {
        "copilot.settings.plugin-activation": {
            "disposition": "ensure-absent",
            "keys": {"enabledPlugins": ["optional@example-marketplace"]},
        }
    }
    package = load_package(write_package(tmp_path, "d.yaml", data))
    assert package.manage["copilot.settings.plugin-activation"]["keys"] == {
        "enabledPlugins": ["optional@example-marketplace"]
    }


@pytest.mark.parametrize(
    "spec",
    [
        {
            "disposition": "ensure-absent",
            "keys": {"enabledPlugins": ["unqualified"]},
        },
        {
            "disposition": "ensure-absent",
            "keys": {"enabledPlugins": ["optional@m", "optional@m"]},
        },
        {
            "disposition": "ensure-absent",
            "keys": {"enabledPlugins": []},
        },
        {
            "disposition": "ensure-absent",
            "keys": {"enabledPlugins": ["optional@m"]},
            "values": {},
        },
    ],
)
def test_invalid_ensure_absent_shape_rejected(tmp_path, spec):
    data = base_package(schema_version=3)
    data["manage"] = {"copilot.settings.plugin-activation": spec}
    with pytest.raises(ManifestError):
        load_package(write_package(tmp_path, "d.yaml", data))


def test_ensure_absent_requires_schema_v3(tmp_path):
    data = base_package(schema_version=2)
    data["manage"] = {
        "copilot.settings.plugin-activation": {
            "disposition": "ensure-absent",
            "keys": {"enabledPlugins": ["optional@m"]},
        }
    }
    with pytest.raises(ManifestError, match="schema_version 3"):
        load_package(write_package(tmp_path, "d.yaml", data))


def test_per_machine_ensure_absent_is_validated_after_layering(tmp_path):
    data = base_package(
        schema_version=2,
        **{
            "per-machine": {
                "box-1": {
                    "manage": {
                        "copilot.settings.plugin-activation": {
                            "disposition": "ensure-absent",
                            "keys": {"enabledPlugins": ["optional@m"]},
                        }
                    }
                }
            }
        },
    )
    package = load_package(write_package(tmp_path, "d.yaml", data))
    with pytest.raises(ManifestError, match="schema_version 3"):
        resolve_for_machine(package, "box-1")


def test_bad_disposition_rejected(tmp_path):
    data = base_package()
    data["manage"]["copilot.settings"]["disposition"] = "obliterate"
    path = write_package(tmp_path, "d.yaml", data)
    with pytest.raises(ManifestError):
        load_package(path)


def test_missing_package_key_rejected(tmp_path):
    data = base_package()
    del data["package"]
    path = write_package(tmp_path, "d.yaml", data)
    with pytest.raises(ManifestError):
        load_package(path)


def test_repo_root_resolves_canonical_all_and_machine_paths(tmp_path):
    all_path = write_package(tmp_path, "all.yaml", base_package())
    machine_path = write_package(
        tmp_path,
        "machine.yaml",
        base_package(name="acme/machine"),
        machine="box-1",
    )
    assert load_package(all_path).repo_root() == tmp_path
    assert load_package(machine_path).repo_root() == tmp_path


def test_repo_anchor_can_differ_from_package_execution_root(tmp_path):
    worktree = tmp_path / "worktree"
    anchor = tmp_path / "anchor"
    path = write_package(worktree, "all.yaml", base_package())
    pkg = load_package(path, source_repo="acme", source_anchor=anchor)

    assert pkg.repo_root() == worktree
    assert pkg.repo_anchor() == anchor.resolve()


def test_repo_anchor_normalizes_relative_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    worktree = tmp_path / "worktree"
    path = write_package(worktree, "all.yaml", base_package())
    pkg = load_package(path, source_repo="acme", source_anchor=Path("anchor"))
    expected = (tmp_path / "anchor").resolve()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    assert pkg.repo_anchor() == expected


def test_repo_root_resolves_legacy_path(tmp_path):
    path = write_package(tmp_path, "legacy.yaml", base_package(), legacy=True)
    assert load_package(path).repo_root() == tmp_path


def test_per_machine_layer_overrides_and_unsets(tmp_path):
    data = base_package(
        gate=["box-1", "box-2"],
        **{
            "per-machine": {
                "Box-2": {
                    "manage": {
                        "copilot.settings": {"values": {"model": None, "effortLevel": "low"}}
                    }
                }
            }
        },
    )
    path = write_package(tmp_path, "d.yaml", data)
    pkg = load_package(path)

    base = resolve_for_machine(pkg, "box-1")
    assert base.manage["copilot.settings"]["values"]["model"] == "opus"

    for machine in ("box-2", "BOX-2"):
        layered = resolve_for_machine(pkg, machine)
        values = layered.manage["copilot.settings"]["values"]
        assert "model" not in values  # null unset
        assert values["effortLevel"] == "low"  # overridden


def test_per_machine_case_duplicates_rejected(tmp_path):
    data = base_package(
        **{
            "per-machine": {
                "box-2": {"manage": {}},
                "BOX-2": {"manage": {}},
            }
        },
    )
    path = write_package(tmp_path, "d.yaml", data)

    with pytest.raises(ManifestError, match="same case-insensitive identity"):
        load_package(path)


@pytest.mark.parametrize("machine_key", ["", "   ", " box-2", "box-2 "])
def test_per_machine_invalid_whitespace_rejected(tmp_path, machine_key):
    data = base_package(
        **{"per-machine": {machine_key: {"manage": {}}}},
    )
    path = write_package(tmp_path, "d.yaml", data)

    with pytest.raises(ManifestError, match="without surrounding whitespace"):
        load_package(path)


def test_per_machine_dual_spellings_rejected(tmp_path):
    data = base_package(
        **{
            "per-machine": {},
            "per_machine": {"box-2": {"manage": {}}},
        },
    )
    path = write_package(tmp_path, "d.yaml", data)

    with pytest.raises(ManifestError, match="declare only one"):
        load_package(path)


def test_per_machine_explicit_null_is_empty(tmp_path):
    data = base_package(**{"per-machine": None})
    path = write_package(tmp_path, "d.yaml", data)

    package = load_package(path)

    assert package.per_machine == {}


@pytest.mark.parametrize("spelling", ["per-machine", "per_machine"])
def test_per_machine_non_mapping_container_rejected(tmp_path, spelling):
    data = base_package(**{spelling: "invalid"})
    path = write_package(tmp_path, "d.yaml", data)

    with pytest.raises(
        ManifestError,
        match="'per-machine'/'per_machine' must be a mapping",
    ):
        load_package(path)


@pytest.mark.parametrize("overlay", ["invalid", [], 1, False])
def test_per_machine_non_mapping_overlay_rejected(tmp_path, overlay):
    data = base_package(
        **{"per-machine": {"box-2": overlay}},
    )
    path = write_package(tmp_path, "d.yaml", data)

    with pytest.raises(ManifestError, match="must be a mapping"):
        load_package(path)

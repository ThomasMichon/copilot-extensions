from __future__ import annotations

import pytest

from agent_machines.manifest import (
    ManifestError,
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


def test_bad_schema_version_rejected(tmp_path):
    path = write_package(tmp_path, "d.yaml", base_package(schema_version=99))
    with pytest.raises(ManifestError):
        load_package(path)


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


def test_per_machine_layer_overrides_and_unsets(tmp_path):
    data = base_package(
        gate=["box-1", "box-2"],
        **{
            "per-machine": {
                "box-2": {
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

    layered = resolve_for_machine(pkg, "box-2")
    values = layered.manage["copilot.settings"]["values"]
    assert "model" not in values  # null unset
    assert values["effortLevel"] == "low"  # overridden

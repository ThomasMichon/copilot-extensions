from __future__ import annotations

import pytest
import yaml

from agent_machines import modules
from agent_machines.identity import resolve_machine
from agent_machines.manifest import ManifestError, load_package, resolve_for_machine

from ._helpers import base_package, write_package


def _topology(repo, machines):
    (repo / "machines.yaml").parent.mkdir(parents=True, exist_ok=True)
    (repo / "machines.yaml").write_text(
        yaml.safe_dump({"machines": machines}),
        encoding="utf-8",
    )


def test_hostname_resolves_canonical_key_and_aliases(tmp_path):
    repo = tmp_path / "repo"
    _topology(
        repo,
        {
            "owner-workstation": {
                "hostname": "generated-host",
                "alias": "workstation",
                "display_name": "Workstation",
            }
        },
    )

    identity = resolve_machine("GENERATED-HOST", topology_repos=[repo])

    assert identity.canonical == "owner-workstation"
    assert identity.raw == "GENERATED-HOST"
    assert identity.accepted == (
        "owner-workstation",
        "generated-host",
        "workstation",
    )


@pytest.mark.parametrize(
    "value",
    ["owner-workstation", "generated-host", "workstation", "Workstation"],
)
def test_every_topology_identity_resolves_same_machine(tmp_path, value):
    repo = tmp_path / "repo"
    _topology(
        repo,
        {
            "owner-workstation": {
                "hostname": "generated-host",
                "alias": "workstation",
                "display_name": "Workstation",
            }
        },
    )

    assert resolve_machine(value, topology_repos=[repo]).canonical == "owner-workstation"


def test_unknown_machine_preserves_raw_fallback(tmp_path):
    identity = resolve_machine("unknown-host", topology_repos=[tmp_path])

    assert identity.canonical == "unknown-host"
    assert identity.accepted == ("unknown-host",)
    assert identity.topology_path is None


def test_malformed_topology_is_advisory_and_preserves_fallback(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "machines.yaml").write_text("machines: [", encoding="utf-8")

    identity = resolve_machine("raw-host", topology_repos=[repo])

    assert identity.canonical == "raw-host"
    assert identity.accepted == ("raw-host",)
    assert len(identity.warnings) == 1
    assert "cannot read machine topology" in identity.warnings[0]


def test_ambiguous_alias_fails_closed(tmp_path):
    repo = tmp_path / "repo"
    _topology(
        repo,
        {
            "machine-a": {"alias": "shared"},
            "machine-b": {"hostname": "shared"},
        },
    )

    with pytest.raises(ManifestError, match="ambiguous.*machine-a, machine-b"):
        resolve_machine("shared", topology_repos=[repo])


def test_ambiguous_alias_fails_even_when_resolving_canonical_key(tmp_path):
    repo = tmp_path / "repo"
    _topology(
        repo,
        {
            "machine-a": {"alias": "shared"},
            "machine-b": {"alias": "shared"},
        },
    )

    with pytest.raises(ManifestError, match="ambiguous.*machine-a, machine-b"):
        resolve_machine("machine-a", topology_repos=[repo])


def test_per_machine_overlay_and_nested_gates_accept_alias(tmp_path):
    repo = tmp_path / "repo"
    package = base_package(gate=["generated-host"])
    package["per-machine"] = {
        "workstation": {
            "manage": {
                "copilot.settings": {
                    "values": {"effortLevel": "medium"}
                }
            }
        }
    }
    package["modules"] = [
        {
            "name": "host",
            "gate": ["generated-host"],
            "windows": {"command": ["host"]},
        }
    ]
    package["resources"] = [
        {
            "type": "package",
            "id": "Example.Tool",
            "manager": "winget",
            "state": "present",
            "gate": ["Workstation"],
        }
    ]
    path = write_package(repo, "package.yaml", package)
    loaded = load_package(path, source_repo="repo")
    accepted = (
        "owner-workstation",
        "generated-host",
        "workstation",
    )

    assert loaded.applies_to("owner-workstation", accepted)
    resolved = resolve_for_machine(loaded, "owner-workstation", accepted)

    assert (
        resolved.manage["copilot.settings"]["values"]["effortLevel"]
        == "medium"
    )
    assert resolved.modules[0]["gate"] == ["owner-workstation"]
    assert resolved.resources[0]["gate"] == ["owner-workstation"]


def test_alias_package_gate_is_canonicalized_for_inherited_module_gate(tmp_path):
    repo = tmp_path / "repo"
    package = base_package(gate=["generated-host"])
    package["modules"] = [
        {
            "name": "host",
            "windows": {"command": ["host"]},
        }
    ]
    loaded = load_package(write_package(repo, "package.yaml", package))
    accepted = ("owner-workstation", "generated-host")

    resolved = resolve_for_machine(loaded, "owner-workstation", accepted)

    assert resolved.gate == ["owner-workstation"]
    assert modules.module_applies(
        resolved.modules[0],
        resolved,
        "owner-workstation",
    )


def test_multiple_alias_overlays_fail_closed(tmp_path):
    repo = tmp_path / "repo"
    package = base_package(gate=["*"])
    package["per-machine"] = {
        "generated-host": {"manage": {}},
        "workstation": {"manage": {}},
    }
    loaded = load_package(write_package(repo, "package.yaml", package))

    with pytest.raises(ManifestError, match="multiple per-machine overlays"):
        resolve_for_machine(
            loaded,
            "owner-workstation",
            ("owner-workstation", "generated-host", "workstation"),
        )


def test_wildcard_nested_gates_remain_machine_independent(tmp_path):
    repo = tmp_path / "repo"
    package = base_package(gate=["*"])
    package["modules"] = [
        {
            "name": "shared",
            "gate": ["*"],
            "windows": {"command": ["shared"]},
        }
    ]
    loaded = load_package(write_package(repo, "package.yaml", package))

    resolved = resolve_for_machine(
        loaded,
        "owner-workstation",
        ("owner-workstation", "generated-host"),
    )

    assert resolved.modules[0]["gate"] == ["*"]

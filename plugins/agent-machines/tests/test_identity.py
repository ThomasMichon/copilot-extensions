from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
import yaml

from agent_machines import modules
from agent_machines.identity import resolve_machine
from agent_machines.manifest import ManifestError, load_package, resolve_for_machine

from ._helpers import base_package, enable_plugin, write_package


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


def test_legacy_windows_wsl_identity_and_gate_aliases_are_independent(tmp_path):
    repo = tmp_path / "repo"
    _topology(repo, {"desktop": {
        "hostname": "generated-host", "alias": "native-compat",
        "ssh": {"environments": [
            {"name": "windows", "alias": "native-profile"},
            {"name": "wsl", "alias": "guest-profile"},
        ]},
    }})
    native = resolve_machine("desktop", topology_repos=[repo])
    guest = resolve_machine("guest-profile", topology_repos=[repo])
    assert (native.canonical, guest.canonical) == ("desktop", "desktop-wsl")
    assert native.physical_host == guest.physical_host == "desktop"
    assert (native.execution_platform, guest.execution_platform) == ("windows", "wsl")
    assert "native-compat" in native.accepted
    assert guest.accepted == ("desktop-wsl", "guest-profile")
    assert resolve_machine(
        "generated-host", topology_repos=[repo], execution_platform="wsl",
    ).canonical == "desktop-wsl"
    with pytest.raises(ManifestError, match="does not select an execution space"):
        resolve_machine("generated-host", topology_repos=[repo])


def test_automatic_local_identity_uses_owner_platform_detection(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _topology(repo, {"desktop": {
        "hostname": "generated-host",
        "ssh": {"environments": [{"name": "wsl", "alias": "guest-profile"}]},
    }})
    monkeypatch.setattr("agent_machines.identity.platform.node", lambda: "generated-host")
    monkeypatch.setattr("agent_machines.identity.local_execution_platform", lambda: "wsl")
    assert resolve_machine(topology_repos=[repo]).canonical == "desktop-wsl"
    monkeypatch.setattr("agent_machines.identity.local_execution_platform", lambda: "windows")
    assert resolve_machine(topology_repos=[repo]).canonical == "desktop"


def test_explicit_logical_identity_preserves_physical_relationship(tmp_path):
    repo = tmp_path / "repo"
    _topology(repo, {
        "native": {"execution_platform": "windows", "physical_host": "desktop"},
        "guest": {"execution_platform": "wsl", "physical_host": "desktop"},
    })
    guest = resolve_machine("guest", topology_repos=[repo])
    assert guest.canonical == "guest"
    assert guest.physical_host == "desktop"
    assert guest.accepted == ("guest",)


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("platform_name", ["windows", "wsl"])
def test_mixed_topologies_ignore_transport_details_in_identity_and_native_cli(
    tmp_path, reverse, platform_name,
):
    legacy, explicit = tmp_path / "legacy", tmp_path / "explicit"
    _topology(legacy, {"desktop": {"ssh": {"environments": [
        {"name": "windows", "alias": "native-profile"},
        {"name": "wsl", "alias": "guest-profile", "user": "guest"},
    ]}}})
    _topology(explicit, {
        key: {
            "execution_platform": execution_platform,
            "physical_host": "desktop",
            "ssh": {"environments": [{
                "name": execution_platform, "alias": alias,
                "port": port, "user": "operator", "shell": shell,
            }]},
        }
        for key, execution_platform, alias, port, shell in (
            ("desktop", "windows", "native-profile", 2222, "pwsh"),
            ("desktop-wsl", "wsl", "guest-profile", 2200, "bash"),
        )
    })
    repos = [explicit, legacy] if reverse else [legacy, explicit]
    requested = "native-profile" if platform_name == "windows" else "guest-profile"

    identity = resolve_machine(requested, topology_repos=repos)

    assert identity.canonical == ("desktop" if platform_name == "windows" else "desktop-wsl")
    assert identity.execution_platform == platform_name
    assert identity.physical_host == "desktop"
    assert identity.accepted == (identity.canonical, requested)
    assert identity.warnings == ()

    for repo in repos:
        write_package(repo, "defaults.yaml", base_package(
            name=f"{repo.name}/defaults", gate=[requested],
        ))
        enable_plugin(repo)
    home = tmp_path / "home"
    registry = home / ".agent-worktrees"
    registry.mkdir(parents=True)
    (registry / "repos.yaml").write_text(yaml.safe_dump({
        "schema_version": 1,
        "repos": {
            repo.name: {"class": "worktree", **{
                platform: str(repo) for platform in ("windows", "linux", "wsl")
            }}
            for repo in repos
        },
    }, sort_keys=False), encoding="utf-8")
    (registry / "projects.yaml").write_text(yaml.safe_dump({
        "schema_version": 2,
        "projects": {repo.name: {"config_dir": str(home / repo.name)} for repo in repos},
    }, sort_keys=False), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "agent_machines", "discover", "--machine", requested, "--json"],
        cwd=tmp_path, env={**os.environ, "HOME": str(home), "USERPROFILE": str(home)},
        capture_output=True, text=True, timeout=20,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["machine"] == identity.canonical
    assert payload["machine_aliases"] == list(identity.accepted)
    assert payload["machine_identity_warnings"] == []
    assert [repo["repo"] for repo in payload["repos"]] == [repo.name for repo in repos]
    assert all(repo["enabled"] for repo in payload["repos"])


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("conflict", [
    {"execution_platform": "linux"},
    {"physical_host": "other-host"},
])
def test_duplicate_logical_machine_conflicts_still_fail_closed(tmp_path, reverse, conflict):
    first, second = tmp_path / "first", tmp_path / "second"
    entry = {"execution_platform": "windows", "physical_host": "desktop"}
    _topology(first, {"native": entry})
    _topology(second, {"native": {**entry, **conflict}})
    repos = [second, first] if reverse else [first, second]

    with pytest.raises(ManifestError, match="conflicting topology declarations"):
        resolve_machine("native", topology_repos=repos)


@pytest.mark.parametrize("reverse", [False, True])
def test_merged_profile_aliases_still_reject_cross_machine_ambiguity(tmp_path, reverse):
    first, second = tmp_path / "first", tmp_path / "second"
    entry = {"execution_platform": "windows", "physical_host": "desktop"}
    _topology(first, {
        "native": {**entry, "alias": "original-profile"},
        "other": {"execution_platform": "linux", "alias": "shared-profile"},
    })
    _topology(second, {"native": {
        **entry, "ssh": {"environments": [
            {"name": "windows", "alias": "shared-profile", "port": 2222},
        ]},
    }})
    repos = [second, first] if reverse else [first, second]

    with pytest.raises(ManifestError, match="ambiguous.*native, other"):
        resolve_machine("native", topology_repos=repos)


def test_known_platform_does_not_choose_among_same_platform_entries(tmp_path):
    repo = tmp_path / "repo"
    _topology(repo, {
        key: {"execution_platform": "wsl", "physical_host": "desktop"}
        for key in ("guest-one", "guest-two")
    })
    with pytest.raises(ManifestError, match="ambiguous wsl entries"):
        resolve_machine("desktop", topology_repos=[repo], execution_platform="wsl")


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

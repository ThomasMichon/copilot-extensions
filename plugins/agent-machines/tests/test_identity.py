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


@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
def test_default_identity_distinguishes_guest(tmp_path, monkeypatch, environment):
    from agent_machines import identity as identity_module

    _topology(tmp_path, {
        "example-host": {"hostname": "generated-host", "alias": "host-label"},
        "example-host-wsl": {"hostname": "generated-host", "alias": "guest-label"},
    })
    monkeypatch.setattr(identity_module.platform, "node", lambda: "generated-host")
    monkeypatch.setattr(identity_module, "detect_platform", lambda: environment, raising=False)
    identity = resolve_machine(topology_repos=[tmp_path])
    assert identity.canonical == (
        "example-host-wsl" if environment == "wsl" else "example-host"
    )
    if environment == "wsl":
        assert "example-host" not in identity.accepted
        assert "generated-host" not in identity.accepted
        assert "host-label" not in identity.accepted


@pytest.mark.parametrize("hostname", ["example-host", "example-host-wsl"])
def test_standalone_default_guest_is_qualified_once(monkeypatch, hostname):
    from agent_machines import identity as identity_module

    monkeypatch.setattr(identity_module.platform, "node", lambda: hostname)
    monkeypatch.setattr(identity_module, "detect_platform", lambda: "wsl", raising=False)
    assert resolve_machine().canonical == "example-host-wsl"


@pytest.mark.parametrize("environment", ["windows", "linux", "wsl"])
def test_default_hostname_case_is_portable(monkeypatch, environment):
    from agent_machines import identity as identity_module

    monkeypatch.setattr(identity_module.platform, "node", lambda: "EXAMPLE-HOST")
    monkeypatch.setattr(identity_module, "detect_platform", lambda: environment)
    identity = resolve_machine()
    assert identity.canonical == (
        "example-host-wsl" if environment == "wsl" else "example-host"
    )
    assert identity.raw == "EXAMPLE-HOST"


@pytest.mark.parametrize("system,release,expected", [
    ("Windows", "10", "windows"),
    ("Linux", "6.6-microsoft-standard-WSL2", "wsl"),
    ("Linux", "6.6-generic", "linux"),
])
def test_execution_platform_ignores_inherited_environment(monkeypatch, system, release, expected):
    import io
    from machine_transport import detect_platform

    monkeypatch.setattr("platform.system", lambda: system)
    monkeypatch.setattr("platform.release", lambda: release)
    monkeypatch.setattr("builtins.open", lambda *_args, **_kwargs: io.StringIO("Linux generic"))
    monkeypatch.setenv("WSL_DISTRO_NAME", "inherited-transport-environment")
    assert detect_platform() == expected


def test_explicit_selector_is_not_reinterpreted_as_local_guest(tmp_path, monkeypatch):
    from agent_machines import identity as identity_module

    _topology(tmp_path, {"example-host": {"hostname": "generated-host"}})
    monkeypatch.setattr(identity_module, "detect_platform", lambda: "wsl", raising=False)
    assert resolve_machine("generated-host", topology_repos=[tmp_path]).canonical == "example-host"


def test_guest_without_entry_cannot_accept_host_package_gates(tmp_path, monkeypatch):
    from agent_machines import identity as identity_module

    _topology(tmp_path, {
        "example-host": {"hostname": "generated-host", "alias": "host-label"},
    })
    monkeypatch.setattr(identity_module.platform, "node", lambda: "generated-host")
    monkeypatch.setattr(identity_module, "detect_platform", lambda: "wsl")
    identity = resolve_machine(topology_repos=[tmp_path])
    assert identity.canonical == "example-host-wsl"
    assert identity.accepted == ("example-host-wsl",)
    assert "no guest entry" in identity.warnings[0]
    host_package = load_package(write_package(
        tmp_path, "host.yaml", base_package(gate=["host-label"]),
    ))
    assert not host_package.applies_to(identity.canonical, identity.accepted)


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


@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
def test_shared_display_names_are_metadata(tmp_path, monkeypatch, environment):
    from agent_machines import identity as identity_module

    _topology(tmp_path, {
        "example-host": {
            "hostname": "generated-host", "display_name": "Example workstation",
        },
        "example-host-wsl": {
            "hostname": "generated-host", "display_name": "Example workstation",
        },
    })
    monkeypatch.setattr(identity_module.platform, "node", lambda: "generated-host")
    monkeypatch.setattr(identity_module, "detect_platform", lambda: environment)
    identity = resolve_machine(topology_repos=[tmp_path])
    assert identity.canonical == (
        "example-host-wsl" if environment == "wsl" else "example-host"
    )
    assert "Example workstation" not in identity.accepted
    for key in ("example-host", "example-host-wsl"):
        assert resolve_machine(key, topology_repos=[tmp_path]).canonical == key
    with pytest.raises(ManifestError, match="display label.*multiple machines"):
        resolve_machine("Example workstation", topology_repos=[tmp_path])


def test_display_metadata_collision_does_not_override_machine_key(tmp_path):
    _topology(tmp_path, {
        "example-host": {"hostname": "generated-host"},
        "other-host": {"display_name": "example-host"},
    })
    assert resolve_machine("example-host", topology_repos=[tmp_path]).canonical == "example-host"
    other = resolve_machine("other-host", topology_repos=[tmp_path])
    assert "example-host" not in other.accepted


@pytest.mark.parametrize("owner", ["example-host", "other-host"])
def test_guest_fallback_collision(tmp_path, monkeypatch, owner):
    from agent_machines import identity as identity_module

    machines = {"example-host": {"hostname": "generated-host"}}
    machines.setdefault(owner, {})["alias"] = "example-host-wsl"
    _topology(tmp_path, machines)
    monkeypatch.setattr(identity_module.platform, "node", lambda: "generated-host")
    monkeypatch.setattr(identity_module, "detect_platform", lambda: "wsl")
    with pytest.raises(ManifestError, match="guest identity.*collides.*native machine"):
        resolve_machine(topology_repos=[tmp_path])


def test_guest_fallback_collision_with_native_hostname(tmp_path, monkeypatch):
    from agent_machines import identity as identity_module

    _topology(tmp_path, {"native-host": {"hostname": "generated-host-wsl"}})
    monkeypatch.setattr(identity_module.platform, "node", lambda: "generated-host")
    monkeypatch.setattr(identity_module, "detect_platform", lambda: "wsl")
    with pytest.raises(ManifestError, match="guest identity.*collides.*native machine"):
        resolve_machine(topology_repos=[tmp_path])


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

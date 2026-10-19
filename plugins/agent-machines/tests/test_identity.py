from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from types import SimpleNamespace

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


@pytest.mark.parametrize("other", [
    {"key": "other", "display_name": "Legacy label"},
    {"key": "other", "alias": "Legacy label"},
    {"key": "Legacy label"},
])
def test_automatic_legacy_display_gate_never_shadows_another_identity(tmp_path, monkeypatch, other):
    key = other["key"]
    _topology(tmp_path, {
        "example-host": {"hostname": "generated-host", "display_name": "Legacy label"},
        key: {name: value for name, value in other.items() if name != "key"},
    })
    monkeypatch.setattr("agent_machines.identity.platform.node", lambda: "generated-host")
    monkeypatch.setattr("agent_machines.identity.detect_platform", lambda: "linux")
    assert "Legacy label" not in resolve_machine(topology_repos=[tmp_path]).accepted


def test_explicit_space_display_aliases_require_selected_context(tmp_path):
    _topology(tmp_path, {"guest": {
        "execution_platform": "wsl", "physical_host": "example-host", "display_name": "Legacy label",
    }})
    accepted = resolve_machine("guest", topology_repos=[tmp_path]).accepted
    assert accepted[0] == "guest"
    assert "Legacy label (WSL)" in accepted
    from machine_transport import find_machine_entry, parse_machines_yaml_file
    with pytest.raises(ValueError, match="does not select an execution space"):
        find_machine_entry(parse_machines_yaml_file(tmp_path / "machines.yaml"), "Legacy label")


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
    assert guest.accepted[:2] == ("desktop-wsl", "guest-profile")
    assert "desktop (WSL)" in guest.accepted
    assert "native-compat" not in guest.accepted
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
    assert guest.accepted[0] == "guest"
    assert "desktop (WSL)" in guest.accepted
    assert "desktop" not in guest.accepted


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
    assert identity.accepted[:2] == (identity.canonical, requested)
    assert (
        "desktop (WSL)" if platform_name == "wsl" else "desktop (Windows)"
    ) in identity.accepted
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


@pytest.mark.parametrize("hostname", ["example-host", "EXAMPLE-HOST"])
def test_automatic_hostname_key_selects_explicit_guest_but_manual_native_stays_strict(
    tmp_path, monkeypatch, hostname,
):
    from agent_machines import identity as identity_module

    _topology(tmp_path, {
        "example-host": {
            "execution_platform": "windows", "physical_host": "example-host",
            "ssh": {"environments": [{"name": "windows", "alias": "native-profile"}]},
        },
        "example-host-wsl": {
            "execution_platform": "wsl", "physical_host": "example-host",
            "ssh": {"environments": [{"name": "wsl", "alias": "guest-profile"}]},
        },
    })
    monkeypatch.setattr(identity_module.platform, "node", lambda: hostname)
    monkeypatch.setattr(identity_module, "detect_platform", lambda: "wsl")
    assert resolve_machine(topology_repos=[tmp_path]).canonical == "example-host-wsl"
    assert resolve_machine(
        "guest-profile", topology_repos=[tmp_path], execution_platform="wsl",
    ).canonical == "example-host-wsl"
    for manual in (hostname, "native-profile"):
        with pytest.raises(ManifestError, match="conflicts"):
            resolve_machine(manual, topology_repos=[tmp_path], execution_platform="wsl")


@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
def test_default_identity_distinguishes_guest(tmp_path, monkeypatch, environment):
    from agent_machines import identity as identity_module

    _topology(tmp_path, {
        "example-host": {"hostname": "generated-host", "alias": "host-label"},
        "example-host-wsl": {"hostname": "generated-host", "alias": "guest-label"},
    })
    monkeypatch.setattr(identity_module.platform, "node", lambda: "generated-host")
    monkeypatch.setattr(identity_module, "local_execution_platform", lambda: environment)
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
    monkeypatch.setattr(identity_module, "local_execution_platform", lambda: "wsl")
    assert resolve_machine().canonical == "example-host-wsl"


@pytest.mark.parametrize("environment", ["windows", "linux", "wsl"])
def test_default_hostname_case_is_portable(monkeypatch, environment):
    from agent_machines import identity as identity_module

    monkeypatch.setattr(identity_module.platform, "node", lambda: "EXAMPLE-HOST")
    monkeypatch.setattr(identity_module, "local_execution_platform", lambda: environment)
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

    monkeypatch.setattr("sys.platform", "win32" if system == "Windows" else "linux")
    monkeypatch.setattr("os.uname", lambda: SimpleNamespace(release=release), raising=False)
    monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: "0::/user.slice")
    real_is_file = Path.is_file
    monkeypatch.setattr(
        Path, "is_file",
        lambda path: False if path in (Path("/.dockerenv"), Path("/run/.containerenv"))
        else real_is_file(path),
    )
    monkeypatch.setattr("builtins.open", lambda *_args, **_kwargs: io.StringIO("Linux generic"))
    monkeypatch.setenv("WSL_DISTRO_NAME", "inherited-transport-environment")
    assert detect_platform() == expected


@pytest.mark.parametrize("marker", ["/.dockerenv", "/run/.containerenv", None])
@pytest.mark.parametrize("inherited_wsl_tags", [False, True])
def test_container_marker_distinguishes_shared_wsl_kernel(monkeypatch, marker, inherited_wsl_tags):
    import io
    from machine_transport import detect_platform

    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("os.uname", lambda: SimpleNamespace(release="6.6-microsoft-standard-WSL2"), raising=False)
    monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: "0::/user.slice")
    real_is_file = Path.is_file
    monkeypatch.setattr(
        Path, "is_file",
        lambda path: (path == Path(marker) if marker else False)
        if path in (Path("/.dockerenv"), Path("/run/.containerenv")) else real_is_file(path),
    )
    monkeypatch.setattr(
        "builtins.open", lambda *_args, **_kwargs: io.StringIO("Linux microsoft-standard-WSL2"),
    )
    for name in ("WSL_DISTRO_NAME", "WSL_INTEROP"):
        if inherited_wsl_tags:
            monkeypatch.setenv(name, "inherited-example-value")
        else:
            monkeypatch.delenv(name, raising=False)
    assert detect_platform() == ("linux" if marker else "wsl")


def test_actual_container_is_linux_execution():
    import platform
    from machine_transport import detect_platform

    if platform.system() != "Linux" or not any(
        Path(marker).is_file() for marker in ("/.dockerenv", "/run/.containerenv")
    ):
        pytest.skip("requires an actual container root")
    assert detect_platform() == "linux"


def test_headless_wsl_proc_fallback_without_container_marker(monkeypatch):
    import io
    from machine_transport import detect_platform

    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("os.uname", lambda: SimpleNamespace(release="generic-kernel"), raising=False)
    monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: "0::/user.slice")
    real_is_file = Path.is_file
    monkeypatch.setattr(
        Path, "is_file",
        lambda path: False if path in (Path("/.dockerenv"), Path("/run/.containerenv"))
        else real_is_file(path),
    )
    monkeypatch.setattr(
        "builtins.open", lambda *_args, **_kwargs: io.StringIO("Linux microsoft-standard"),
    )
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    monkeypatch.delenv("WSL_INTEROP", raising=False)
    assert detect_platform() == "wsl"


def test_explicit_selector_is_not_reinterpreted_as_local_guest(tmp_path, monkeypatch):
    from agent_machines import identity as identity_module

    _topology(tmp_path, {"example-host": {"hostname": "generated-host"}})
    monkeypatch.setattr(identity_module, "local_execution_platform", lambda: "wsl")
    assert resolve_machine("generated-host", topology_repos=[tmp_path]).canonical == "example-host"


def test_guest_without_entry_cannot_accept_host_package_gates(tmp_path, monkeypatch):
    from agent_machines import identity as identity_module

    _topology(tmp_path, {
        "example-host": {"hostname": "generated-host", "alias": "host-label"},
    })
    monkeypatch.setattr(identity_module.platform, "node", lambda: "generated-host")
    monkeypatch.setattr(identity_module, "local_execution_platform", lambda: "wsl")
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

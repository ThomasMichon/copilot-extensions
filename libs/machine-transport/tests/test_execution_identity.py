from __future__ import annotations

import io
import ast
from pathlib import Path

from types import SimpleNamespace

import pytest

from machine_transport import (
    IdentityError,
    MachineEntry,
    TopologyEntry,
    detect_platform,
    machine_entries_to_topology,
    resolve_identity,
    resolve_machine_identity,
)


def test_resolve_identity_qualifies_guest_once():
    assert resolve_identity("example-host", guest=True).canonical == "example-host-wsl"
    assert resolve_identity("example-host-wsl", guest=True).canonical == "example-host-wsl"


def test_resolve_identity_prefers_guest_entry_and_warns_when_missing():
    entries = [
        TopologyEntry("example-host", hostname="generated-host", alias="host-label"),
        TopologyEntry("example-host-wsl", hostname="generated-host", alias="guest-label"),
    ]
    resolved = resolve_identity("generated-host", entries, guest=True)
    assert resolved.canonical == "example-host-wsl"
    assert "generated-host" not in resolved.accepted
    assert "host-label" not in resolved.accepted

    fallback = resolve_identity("generated-host", entries[:1], guest=True)
    assert fallback.canonical == "example-host-wsl"
    assert fallback.accepted == ("example-host-wsl",)
    assert "no guest entry" in fallback.warnings[0]


def test_resolve_identity_rejects_ambiguous_labels_and_warns_on_missing_guest():
    with pytest.raises(IdentityError, match="ambiguous.*machine-a, machine-b"):
        resolve_identity(
            "shared",
            (
                TopologyEntry("machine-a", alias="shared"),
                TopologyEntry("machine-b", hostname="shared"),
            ),
        )

    fallback = resolve_identity(
        "example-host",
        (TopologyEntry("example-host", hostname="generated-host"),),
        guest=True,
    )
    assert fallback.canonical == "example-host-wsl"
    assert "no guest entry" in fallback.warnings[0]


def test_shared_display_names_are_metadata_until_unique_lookup():
    entries = (
        TopologyEntry("example-host", hostname="generated-host", display_name="Example"),
        TopologyEntry("example-host-wsl", hostname="generated-host", display_name="Example"),
    )
    assert resolve_identity("example-host", entries).canonical == "example-host"
    with pytest.raises(IdentityError, match="display label.*multiple machines"):
        resolve_identity("Example", entries)


def test_machine_entry_projection_and_resolution():
    entries = {
        "example-host": MachineEntry(
            key="example-host",
            display_name="Example host",
            hostname="generated-host",
            alias="host-label",
        ),
        "example-host-wsl": MachineEntry(
            key="example-host-wsl",
            display_name="Example guest",
            hostname="generated-host",
            alias="guest-label",
        ),
    }
    projected = machine_entries_to_topology(entries)
    assert [entry.key for entry in projected] == ["example-host", "example-host-wsl"]
    assert resolve_machine_identity(entries, "generated-host", guest=True).canonical == (
        "example-host-wsl"
    )


@pytest.mark.parametrize(
    ("system", "release", "expected"),
    [("Windows", "10", "windows"), ("Linux", "6.6-microsoft-standard-WSL2", "wsl"), ("Linux", "6.6-generic", "linux")],
)
def test_detect_platform_ignores_inherited_environment(
    monkeypatch, system, release, expected,
):
    monkeypatch.setattr("sys.platform", "win32" if system == "Windows" else "linux")
    monkeypatch.setattr("os.uname", lambda: SimpleNamespace(release=release), raising=False)
    monkeypatch.setattr("pathlib.Path.is_file", lambda _path: False)
    monkeypatch.setattr("pathlib.Path.read_text", lambda *_args, **_kwargs: "0::/user.slice")
    monkeypatch.setattr("builtins.open", lambda *_args, **_kwargs: io.StringIO("Linux generic"))
    monkeypatch.setenv("WSL_DISTRO_NAME", "inherited-transport-environment")
    assert detect_platform() == expected


@pytest.mark.parametrize("cgroup", [
    "0::/docker/example", "0::/kubepods/example", "0::/containerd/example",
    "0::/lxc/example", "0::/libpod/example",
])
def test_markerless_containers_do_not_inherit_wsl_identity(monkeypatch, cgroup):
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr("os.uname", lambda: SimpleNamespace(release="6.6-microsoft-standard-WSL2"), raising=False)
    monkeypatch.setattr("pathlib.Path.is_file", lambda _path: False)
    monkeypatch.setattr("pathlib.Path.read_text", lambda *_args, **_kwargs: cgroup)
    assert detect_platform() == "linux"


def test_platform_fixture_uname_patches_support_native_windows_absence():
    root = Path(__file__).resolve().parents[3]
    fixtures = [
        Path(__file__).relative_to(root),
        Path("libs/machine-transport/tests/test_transport.py"),
        Path("plugins/agent-machines/tests/test_identity.py"),
        Path("plugins/agent-worktrees/tests/test_execution_identity.py"),
        Path("plugins/agent-worktrees/tests/test_config.py"),
    ]
    count = 0
    for fixture in fixtures:
        tree = ast.parse((root / fixture).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "setattr" and node.args
                and isinstance(node.args[0], ast.Constant) and node.args[0].value == "os.uname"
            ):
                assert any(
                    keyword.arg == "raising" and isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is False for keyword in node.keywords
                ), fixture
                count += 1
    assert count == 8

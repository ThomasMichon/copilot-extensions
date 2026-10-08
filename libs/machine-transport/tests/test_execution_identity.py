from __future__ import annotations

import io

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
    monkeypatch.setattr("platform.system", lambda: system)
    monkeypatch.setattr("platform.release", lambda: release)
    monkeypatch.setattr("builtins.open", lambda *_args, **_kwargs: io.StringIO("Linux generic"))
    monkeypatch.setenv("WSL_DISTRO_NAME", "inherited-transport-environment")
    assert detect_platform() == expected

"""Logical-key decomposition preserves the execution environment."""

from __future__ import annotations

import pytest

from machine_transport.machine_environment import split_machine_environment


@pytest.mark.parametrize(("machine", "environment", "physical", "platform", "logical"), [
    ("example-host-wsl", None, "example-host", "wsl", "example-host-wsl"),
    ("Example-Host-WSL", " WSL ", "Example-Host", "wsl", "Example-Host-wsl"),
    ("example-host", "WSL", "example-host", "wsl", "example-host-wsl"),
    ("example-host", "Win", "example-host", "windows", "example-host"),
    ("example-host", "Windows", "example-host", "windows", "example-host"),
    ("example-host", "Linux", "example-host", "linux", "example-host"),
    (" example-host ", None, "example-host", "", "example-host"),
    ("example-wsl-host", None, "example-wsl-host", "", "example-wsl-host"),
])
def test_logical_and_tuple_forms(
    machine: str, environment: str | None, physical: str, platform: str, logical: str,
) -> None:
    selected = split_machine_environment(machine, environment)
    assert (selected.machine, selected.environment) == (physical, platform)
    assert selected.logical_machine == logical


@pytest.mark.parametrize(("machine", "environment"), [
    ("", None), (" ", "WSL"), ("-wsl", None),
    ("example-host-wsl", "Win"), ("example-host-wsl", "Linux"),
    ("example-host", "unknown"),
])
def test_invalid_or_conflicting_identity_is_explicit(
    machine: str, environment: str | None,
) -> None:
    with pytest.raises(ValueError):
        split_machine_environment(machine, environment)


def test_decomposition_does_not_make_native_and_guest_the_same_target() -> None:
    native = split_machine_environment("example-host", "Win")
    guest = split_machine_environment("example-host-wsl")
    assert native.machine == guest.machine
    assert native.environment != guest.environment
    assert native.logical_machine != guest.logical_machine

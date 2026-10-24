from __future__ import annotations

import pytest

from machine_transport import (
    IdentityError,
    MachineEntry,
    SSHEnvironment,
    resolve_local_execution_context,
    resolve_machine_selection,
)


@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("manifest", ["absent", "empty", "unrelated", "matching"])
def test_local_context_does_not_require_enrollment(environment: str, manifest: str) -> None:
    entries: dict[str, MachineEntry] = {}
    if manifest == "unrelated":
        entries["other"] = MachineEntry("other", "", execution_platform="windows")
    if manifest == "matching":
        entries["custom-execution"] = MachineEntry(
            "custom-execution", "A display label", hostname="OBSERVED-HOST",
            execution_platform=environment, physical_host="friendly-physical",
        )
    before = dict(entries)
    context = resolve_local_execution_context(
        entries, hostname="OBSERVED-HOST", environment=environment,
    )
    assert context.hostname == "OBSERVED-HOST"
    assert context.environment == environment
    assert context.machine == ("friendly-physical" if manifest == "matching" else "observed-host")
    expected = "observed-host-wsl" if environment == "wsl" else "observed-host"
    assert context.execution_machine == ("custom-execution" if manifest == "matching" else expected)
    assert context.customization_key == ("custom-execution" if manifest == "matching" else None)
    assert entries == before


@pytest.mark.parametrize("hostname", ["literal-wsl", "literal-win", "literal-windows"])
@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
def test_observed_hostname_is_not_legacy_address_syntax(hostname: str, environment: str) -> None:
    context = resolve_local_execution_context({}, hostname=hostname, environment=environment)
    assert context.machine == hostname
    assert context.execution_machine == (f"{hostname}-wsl" if environment == "wsl" else hostname)


@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
def test_unscoped_literal_host_key_does_not_reclassify_observed_hostname(environment: str) -> None:
    entry = MachineEntry("literal-wsl", "")
    context = resolve_local_execution_context(
        {entry.key: entry}, hostname=entry.key, environment=environment,
    )
    assert context.machine == entry.key
    assert context.execution_machine == (f"{entry.key}-wsl" if environment == "wsl" else entry.key)


@pytest.mark.parametrize("environment", ["windows", "wsl"])
def test_projected_legacy_entries_keep_their_original_keys(environment: str) -> None:
    entry = MachineEntry(
        "friendly", "", hostname="raw-host", ssh_environments=[
            SSHEnvironment("windows", "native-ssh"), SSHEnvironment("wsl", "guest-ssh"),
        ],
    )
    context = resolve_local_execution_context(
        {entry.key: entry}, hostname="raw-host", environment=environment,
    )
    assert context.machine == "friendly"
    assert context.execution_machine == ("friendly-wsl" if environment == "wsl" else "friendly")
    assert context.customization_key == context.execution_machine


def test_unscoped_guest_declaration_keeps_legacy_execution_key() -> None:
    entry = MachineEntry("friendly-wsl", "", hostname="raw-host")
    context = resolve_local_execution_context({entry.key: entry}, hostname="raw-host", environment="wsl")
    assert (context.machine, context.execution_machine) == ("friendly", "friendly-wsl")


def test_configured_legacy_presentation_resolves_to_one_execution_term() -> None:
    context = resolve_local_execution_context(
        {}, hostname="raw-host", environment="wsl", configured_machine="friendly (WSL)",
    )
    assert (context.machine, context.execution_machine) == ("friendly", "friendly-wsl")


def test_current_guest_can_use_physical_customization_without_native_authority() -> None:
    entries = {
        "native": MachineEntry(
            "native", "", hostname="raw-host", physical_host="friendly",
            execution_platform="windows",
        ),
    }
    context = resolve_local_execution_context(entries, hostname="raw-host", environment="wsl")
    assert (context.machine, context.execution_machine, context.customization_key) == (
        "friendly", "friendly-wsl", None,
    )
    with pytest.raises(ValueError, match="no wsl logical entry"):
        resolve_machine_selection(entries, "raw-host", "wsl")
    with pytest.raises(ValueError, match="conflicts"):
        resolve_local_execution_context(
            entries, hostname="raw-host", environment="wsl", configured_machine="native",
        )


@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
def test_existing_configured_binding_remains_distinct_from_observation(environment: str) -> None:
    configured = "custom-wsl" if environment == "wsl" else "custom"
    context = resolve_local_execution_context(
        {}, hostname="raw-host", environment=environment, configured_machine=configured,
    )
    assert (context.hostname, context.machine, context.execution_machine) == (
        "raw-host", "custom", configured,
    )


def test_same_platform_customizations_require_unambiguous_current_selection() -> None:
    entries = {
        key: MachineEntry(key, "", hostname="raw-host", execution_platform="linux", physical_host="host")
        for key in ("first", "second")
    }
    with pytest.raises(IdentityError, match="ambiguous"):
        resolve_local_execution_context(entries, hostname="raw-host", environment="linux")
    context = resolve_local_execution_context(
        entries, hostname="raw-host", environment="linux", configured_machine="second",
    )
    assert context.execution_machine == "second"


def test_missing_sibling_physical_spelling_is_deterministic() -> None:
    entries = {
        platform: MachineEntry(
            platform, "", hostname="raw-host", physical_host=physical, execution_platform=platform,
        )
        for platform, physical in (("windows", "Friendly"), ("linux", "friendly"))
    }
    first = resolve_local_execution_context(entries, hostname="raw-host", environment="wsl")
    second = resolve_local_execution_context(
        dict(reversed(list(entries.items()))), hostname="raw-host", environment="wsl",
    )
    assert first == second
    assert first.machine == "Friendly"


@pytest.mark.parametrize("key", ["raw-host-wsl", "RAW-HOST-WSL"])
def test_generated_guest_key_cannot_shadow_native_entry(key: str) -> None:
    entries = {key: MachineEntry(key, "", execution_platform="windows")}
    with pytest.raises(IdentityError, match="conflicts"):
        resolve_local_execution_context(entries, hostname="raw-host", environment="wsl")


@pytest.mark.parametrize("hostname", ["", "two terms", "../host", "host/project/id"])
def test_invalid_current_names_refuse(hostname: str) -> None:
    with pytest.raises(IdentityError):
        resolve_local_execution_context({}, hostname=hostname, environment="linux")


def test_invalid_customizations_are_not_absence() -> None:
    entries = {
        "first": MachineEntry("first", "", alias="shared"),
        "second": MachineEntry("second", "", alias="shared"),
    }
    with pytest.raises(ValueError, match="ambiguous"):
        resolve_local_execution_context(entries, hostname="unrelated", environment="linux")

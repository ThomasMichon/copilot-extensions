from __future__ import annotations

import pytest

from machine_transport import (
    MachineEntry,
    resolve_local_execution_context,
    resolve_machine_selection,
)

pytestmark = pytest.mark.contract("machine-transport.local-context.binding")


@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
def test_unmapped_literal_hostname_can_remain_the_local_binding(environment: str) -> None:
    context = resolve_local_execution_context(
        {}, hostname="literal-wsl", environment=environment, configured_machine="literal-wsl",
    )
    assert context.machine == "literal-wsl"
    assert context.execution_machine == ("literal-wsl-wsl" if environment == "wsl" else "literal-wsl")


@pytest.mark.parametrize("hostname", [
    "raw-host", "literal-win", "literal-wsl", "literal(WSL)", "literal(Windows)",
])
@pytest.mark.parametrize(("environment", "address"), [
    ("windows", "{host}"), ("windows", "{host}-win"), ("windows", "{host} (Windows)"),
    ("wsl", "{host}-wsl"), ("wsl", "{host} (WSL)"),
])
def test_configured_hostname_forms_retain_matching_friendly_customizations(
    hostname: str, environment: str, address: str,
) -> None:
    entries = {
        key: MachineEntry(key, "", hostname=hostname)
        for key in ("friendly", "friendly-wsl")
    }
    context = resolve_local_execution_context(
        entries, hostname=hostname, environment=environment,
        configured_machine=address.format(host=hostname),
    )
    assert (context.machine, context.execution_machine) == (
        "friendly", "friendly-wsl" if environment == "wsl" else "friendly",
    )


@pytest.mark.parametrize("hostname", ["literal-wsl", "literal(WSL)"])
@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
def test_unique_display_binding_preserves_the_literal_observed_host(
    hostname: str, environment: str,
) -> None:
    entry = MachineEntry(hostname, "Friendly", hostname=hostname)
    entries = {entry.key: entry}
    display = resolve_local_execution_context(
        entries, hostname=hostname, environment=environment, configured_machine="Friendly",
    )
    keyed = resolve_local_execution_context(
        entries, hostname=hostname, environment=environment, configured_machine=entry.key,
    )
    assert display == keyed


@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("count", [1, 2])
def test_unrelated_display_metadata_cannot_replace_an_established_local_binding(
    environment: str, count: int,
) -> None:
    binding = "local-binding-wsl" if environment == "wsl" else "local-binding"
    baseline = resolve_local_execution_context(
        {}, hostname="raw-host", environment=environment, configured_machine=binding,
    )
    entries = {
        f"other-{index}": MachineEntry(
            f"other-{index}", binding, hostname=f"other-host-{index}", execution_platform=environment,
        )
        for index in range(count)
    }
    assert resolve_local_execution_context(
        entries, hostname="raw-host", environment=environment, configured_machine=binding,
    ) == baseline


@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
def test_locally_associated_display_binding_retains_matching_customization(environment: str) -> None:
    entry = MachineEntry(
        "custom-execution", "Friendly", hostname="raw-host",
        physical_host="physical", execution_platform=environment,
    )
    context = resolve_local_execution_context(
        {entry.key: entry}, hostname="raw-host", environment=environment, configured_machine="Friendly",
    )
    assert (context.machine, context.execution_machine, context.customization_key) == (
        "physical", entry.key, entry.key,
    )


@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
def test_explicit_legacy_display_selector_can_resolve_without_hostname_metadata(environment: str) -> None:
    entry = MachineEntry(
        "custom-execution", "Friendly computer", physical_host="physical", execution_platform=environment,
    )
    context = resolve_local_execution_context(
        {entry.key: entry}, hostname="raw-host", environment=environment,
        configured_machine=entry.display_name,
    )
    assert (context.machine, context.execution_machine, context.customization_key) == (
        "physical", entry.key, entry.key,
    )


@pytest.mark.parametrize("shadow", ["key", "alias"])
def test_literal_display_name_cannot_shadow_an_explicit_identity(shadow: str) -> None:
    local = MachineEntry("literal-wsl", "Friendly", hostname="literal-wsl")
    foreign = MachineEntry(
        "Friendly" if shadow == "key" else "foreign", "", execution_platform="windows",
        alias="Friendly" if shadow == "alias" else "",
    )
    with pytest.raises(ValueError, match="conflicts"):
        resolve_local_execution_context(
            {local.key: local, foreign.key: foreign}, hostname="literal-wsl", environment="wsl",
            configured_machine="Friendly",
        )


def test_literal_display_name_binding_must_be_unique() -> None:
    local = MachineEntry("literal-wsl", "Friendly", hostname="literal-wsl")
    other = MachineEntry("other", "Friendly", hostname="other")
    with pytest.raises(ValueError, match="multiple machines|ambiguous"):
        resolve_local_execution_context(
            {local.key: local, other.key: other}, hostname="literal-wsl", environment="wsl",
            configured_machine="Friendly",
        )


def test_configured_legacy_presentation_resolves_to_one_execution_term() -> None:
    context = resolve_local_execution_context(
        {}, hostname="raw-host", environment="wsl", configured_machine="friendly (WSL)",
    )
    assert (context.machine, context.execution_machine) == ("friendly", "friendly-wsl")


@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
def test_unrelated_legacy_display_like_key_does_not_require_local_enrollment(environment: str) -> None:
    entry = MachineEntry("Other Legacy Computer", "", hostname="other-host")
    context = resolve_local_execution_context(
        {entry.key: entry}, hostname="local-host", environment=environment,
    )
    assert context.machine == "local-host"
    assert context.execution_machine == ("local-host-wsl" if environment == "wsl" else "local-host")


@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
def test_existing_configured_binding_remains_distinct_from_observation(environment: str) -> None:
    configured = "custom-wsl" if environment == "wsl" else "custom"
    context = resolve_local_execution_context(
        {}, hostname="raw-host", environment=environment, configured_machine=configured,
    )
    assert (context.hostname, context.machine, context.execution_machine) == (
        "raw-host", "custom", configured,
    )


@pytest.mark.parametrize("unrelated", [False, True])
def test_configured_guest_does_not_require_optional_guest_entry(unrelated: bool) -> None:
    entries = {
        "native": MachineEntry(
            "native", "", execution_platform="windows",
            physical_host="elsewhere" if unrelated else "friendly",
        ),
    }
    context = resolve_local_execution_context(
        entries, hostname="raw-host", environment="wsl", configured_machine="friendly-wsl",
    )
    assert (context.machine, context.execution_machine, context.customization_key) == (
        "friendly", "friendly-wsl", None,
    )
    if not unrelated:
        with pytest.raises(ValueError, match="no wsl logical entry"):
            resolve_machine_selection(entries, "friendly-wsl", "wsl")


@pytest.mark.parametrize("guest_key", ["friendly-wsl", "friendly (WSL)"])
@pytest.mark.parametrize("scoped_neighbor", [False, True])
def test_configured_legacy_native_binding_selects_its_actual_guest(
    guest_key: str, scoped_neighbor: bool,
) -> None:
    entries = {
        key: MachineEntry(key, "", hostname="raw-host")
        for key in ("friendly", guest_key)
    }
    if scoped_neighbor:
        entries["other"] = MachineEntry("other", "", execution_platform="linux")
    configured = resolve_local_execution_context(
        entries, hostname="raw-host", environment="wsl", configured_machine="friendly",
    )
    observed = resolve_local_execution_context(entries, hostname="raw-host", environment="wsl")
    assert configured == observed
    assert configured.customization_key == guest_key
    assert resolve_machine_selection(entries, "friendly", "wsl").entry.key == guest_key


@pytest.mark.parametrize("binding", [
    "native-label-wsl", "native-label (WSL)", "native-space-wsl", "{physical}-wsl",
    "Human label-wsl", "Human label (WSL)",
])
@pytest.mark.parametrize("physical", ["Friendly", "physical-wsl"])
def test_missing_guest_alias_fallback_preserves_its_resolved_physical_family(
    binding: str, physical: str,
) -> None:
    binding = binding.format(physical=physical)
    native = MachineEntry(
        "native-space", "Human label", alias="native-label", hostname="raw-host",
        physical_host=physical, execution_platform="windows",
    )
    entries = {native.key: native}
    configured = resolve_local_execution_context(
        entries, hostname="raw-host", environment="wsl", configured_machine=binding,
    )
    observed = resolve_local_execution_context(entries, hostname="raw-host", environment="wsl")
    assert configured == observed
    assert (configured.machine, configured.execution_machine) == (physical, f"{physical}-wsl")
    with pytest.raises(ValueError, match="no wsl logical entry"):
        resolve_machine_selection(entries, binding, "wsl")


def test_missing_guest_metadata_does_not_hide_ambiguous_physical_families() -> None:
    entries = {
        key: MachineEntry(
            key, "Shared", hostname="Shared", execution_platform="windows", physical_host=physical,
        )
        for key, physical in (("first", "physical-a"), ("second", "physical-b"))
    }
    with pytest.raises(ValueError, match="ambiguous"):
        resolve_local_execution_context(
            entries, hostname="raw-host", environment="wsl", configured_machine="Shared-wsl",
        )

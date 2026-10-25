from __future__ import annotations

import pytest

from machine_transport import (
    IdentityError,
    MachineEntry,
    SSHEnvironment,
    normalize_machine_entries,
    resolve_local_execution_context,
    resolve_machine_selection,
)

pytestmark = pytest.mark.contract("machine-transport.local-context.observation")


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
@pytest.mark.parametrize("redundant_hostname", [False, True])
@pytest.mark.parametrize("configured", [False, True])
def test_unscoped_literal_host_key_does_not_reclassify_observed_hostname(
    environment: str, redundant_hostname: bool, configured: bool,
) -> None:
    entry = MachineEntry("literal-wsl", "", hostname="literal-wsl" if redundant_hostname else "")
    context = resolve_local_execution_context(
        {entry.key: entry}, hostname=entry.key, environment=environment,
        configured_machine=entry.key if configured else None,
    )
    assert context.machine == entry.key
    assert context.execution_machine == (f"{entry.key}-wsl" if environment == "wsl" else entry.key)


@pytest.mark.parametrize("hostname", ["literal-wsl", "literal-win", "literal(WSL)", "literal(Windows)"])
@pytest.mark.parametrize("environment", ["windows", "wsl"])
@pytest.mark.parametrize("configured", [False, True])
def test_literal_native_and_guest_siblings_keep_the_observed_physical_name(
    hostname: str, environment: str, configured: bool,
) -> None:
    execution = f"{hostname}-wsl" if environment == "wsl" else hostname
    entries = {
        key: MachineEntry(key, "", hostname=hostname)
        for key in (hostname, f"{hostname}-wsl")
    }
    before = dict(entries)
    context = resolve_local_execution_context(
        entries, hostname=hostname, environment=environment,
        configured_machine=execution if configured else None,
    )
    assert (context.machine, context.execution_machine, context.customization_key) == (
        hostname, execution, execution,
    )
    assert entries == before


@pytest.mark.parametrize("hostname", ["literal(WSL)", "literal(Windows)"])
@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("matching", [False, True])
@pytest.mark.parametrize("configured", [False, True])
def test_observed_parentheses_are_not_legacy_presentation_syntax(
    hostname: str, environment: str, matching: bool, configured: bool,
) -> None:
    entries = {hostname: MachineEntry(hostname, "", hostname=hostname)} if matching else {}
    context = resolve_local_execution_context(
        entries, hostname=hostname, environment=environment,
        configured_machine=hostname if configured else None,
    )
    assert context.machine == (hostname if matching else hostname.casefold())
    physical = hostname if matching else hostname.casefold()
    assert context.execution_machine == (f"{physical}-wsl" if environment == "wsl" else physical)


@pytest.mark.parametrize("environment", ["windows", "wsl"])
@pytest.mark.parametrize("configured", [False, True])
@pytest.mark.parametrize("spaced", [False, True])
def test_legacy_presentation_registry_keys_produce_one_term_execution_names(
    environment: str, configured: bool, spaced: bool,
) -> None:
    label = "WSL" if environment == "wsl" else "Windows"
    key = f"friendly{' ' if spaced else ''}({label})"
    entry = MachineEntry(key, "", hostname="raw-host")
    context = resolve_local_execution_context(
        {key: entry}, hostname="raw-host", environment=environment,
        configured_machine=key if configured else None,
    )
    assert context.machine == "friendly"
    assert context.execution_machine == ("friendly-wsl" if environment == "wsl" else "friendly")
    assert context.customization_key == key


def test_literal_hostname_projection_still_rejects_genuine_alias_collision() -> None:
    entries = {
        "literal-win": MachineEntry("literal-win", "", hostname="literal-win", alias="shared"),
        "literal-win-wsl": MachineEntry("literal-win-wsl", "", hostname="literal-win", alias="shared"),
    }
    with pytest.raises(ValueError, match="ambiguous"):
        resolve_local_execution_context(entries, hostname="literal-win", environment="wsl")


@pytest.mark.parametrize("hostname", ["literal-win", "literal-wsl"])
def test_literal_observation_is_an_opt_in_normalization_context(hostname: str) -> None:
    entries = {
        key: MachineEntry(key, "", hostname=hostname)
        for key in (hostname, f"{hostname}-wsl")
    }
    with pytest.raises(ValueError):
        normalize_machine_entries(entries)
    normalized = normalize_machine_entries(entries, observed_hostname=hostname)
    assert normalized == entries
    assert all(entry.hostname == hostname for entry in normalized.values())


@pytest.mark.parametrize("hostname", ["", " "])
def test_normalization_rejects_invalid_observation_context(hostname: str) -> None:
    with pytest.raises(ValueError, match="observed hostname"):
        normalize_machine_entries({}, observed_hostname=hostname)


def test_canonicalized_presentation_key_cannot_shadow_another_entry() -> None:
    entry = MachineEntry("friendly (WSL)", "", hostname="raw-host")
    other = MachineEntry("friendly-wsl", "", execution_platform="windows")
    with pytest.raises(ValueError, match="conflicts"):
        resolve_local_execution_context(
            {entry.key: entry, other.key: other}, hostname="raw-host", environment="wsl",
        )


def test_explicit_canonical_key_is_not_presentation_syntax() -> None:
    entry = MachineEntry("friendly(WSL)", "", execution_platform="linux", hostname="raw-host")
    context = resolve_local_execution_context(
        {entry.key: entry}, hostname="raw-host", environment="linux",
    )
    assert context.execution_machine == entry.key


@pytest.mark.parametrize(("environment", "slot"), [
    (environment, slot)
    for environment in ("windows", "wsl", "linux")
    for slot in ("alias", "compatibility_aliases", "ssh_profile")
    if (environment, slot) != ("wsl", "ssh_profile")
])
def test_registered_aliases_preserve_the_literal_observed_host(environment: str, slot: str) -> None:
    entry = MachineEntry(
        "literal-wsl", "", hostname="literal-wsl",
        alias="friendly" if slot == "alias" else "",
        compatibility_aliases=("friendly",) if slot == "compatibility_aliases" else (),
        ssh_environments=[SSHEnvironment(environment, "friendly")] if slot == "ssh_profile" else [],
    )
    context = resolve_local_execution_context(
        {entry.key: entry}, hostname="literal-wsl", environment=environment,
        configured_machine="friendly",
    )
    assert context.machine == "literal-wsl"
    assert context.execution_machine == ("literal-wsl-wsl" if environment == "wsl" else "literal-wsl")


def test_literal_host_alias_does_not_override_explicit_native_space() -> None:
    entry = MachineEntry(
        "literal-wsl", "", hostname="literal-wsl", alias="friendly", execution_platform="windows",
    )
    with pytest.raises(ValueError, match="conflicts"):
        resolve_local_execution_context(
            {entry.key: entry}, hostname="literal-wsl", environment="wsl", configured_machine="friendly",
        )


def test_derived_guest_does_not_claim_native_customization() -> None:
    entry = MachineEntry("friendly", "Friendly", hostname="raw-host")
    entries = {entry.key: entry}
    observed = resolve_local_execution_context(entries, hostname="raw-host", environment="wsl")
    configured = resolve_local_execution_context(
        entries, hostname="raw-host", environment="wsl", configured_machine=entry.key,
    )
    assert observed == configured
    assert (observed.machine, observed.execution_machine, observed.customization_key) == (
        "friendly", "friendly-wsl", None,
    )


def test_unscoped_guest_declaration_keeps_legacy_execution_key() -> None:
    entry = MachineEntry("friendly-wsl", "", hostname="raw-host")
    context = resolve_local_execution_context({entry.key: entry}, hostname="raw-host", environment="wsl")
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
@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("metadata", ["none", "hostname", "physical", "both"])
@pytest.mark.parametrize("configured", [False, True])
def test_guest_only_entry_does_not_require_optional_host_metadata(
    environment: str, explicit: bool, metadata: str, configured: bool,
) -> None:
    entry = MachineEntry(
        "desk-wsl", "", execution_platform="wsl" if explicit else "",
        hostname="desk" if metadata in ("hostname", "both") else "",
        physical_host="desk" if metadata in ("physical", "both") else "",
    )
    execution = "desk-wsl" if environment == "wsl" else "desk"
    context = resolve_local_execution_context(
        {entry.key: entry}, hostname="desk", environment=environment,
        configured_machine=execution if configured else None,
    )
    assert (context.machine, context.execution_machine) == ("desk", execution)
    assert context.customization_key == ("desk-wsl" if environment == "wsl" else None)


@pytest.mark.parametrize("configured", [False, True])
def test_execution_whitespace_is_normalized_before_collision_validation(configured: bool) -> None:
    entry = MachineEntry(" desk-wsl ", "", hostname="raw-host", alias="guest-binding")
    foreign = MachineEntry("desk-wsl", "", execution_platform="windows")
    with pytest.raises(ValueError, match="conflicts"):
        resolve_local_execution_context(
            {entry.key: entry, foreign.key: foreign}, hostname="raw-host", environment="wsl",
            configured_machine="guest-binding" if configured else None,
        )


@pytest.mark.parametrize("foreign_alias", ["desk-wsl", " desk-wsl "])
def test_normalized_execution_checks_every_colliding_entry(foreign_alias: str) -> None:
    entry = MachineEntry(" desk-wsl ", "", hostname="raw-host", alias="desk-wsl")
    foreign = MachineEntry("foreign", "", alias=foreign_alias, execution_platform="windows")
    with pytest.raises(ValueError, match="ambiguous|conflicts"):
        resolve_local_execution_context(
            {entry.key: entry, foreign.key: foreign}, hostname="raw-host", environment="wsl",
        )


@pytest.mark.parametrize("key", [" desk-wsl ", " desk (WSL) "])
def test_execution_normalization_keeps_original_customization_key(key: str) -> None:
    entry = MachineEntry(key, "", hostname="raw-host", alias="desk-wsl")
    context = resolve_local_execution_context({key: entry}, hostname="raw-host", environment="wsl")
    assert (context.machine, context.execution_machine, context.customization_key) == ("desk", "desk-wsl", key)


@pytest.mark.parametrize("hostname", ["literal-wsl", "literal(WSL)"])
@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("configured", [False, True])
def test_registry_whitespace_cannot_change_a_literal_observed_identity(
    hostname: str, environment: str, configured: bool,
) -> None:
    key = f" {hostname} "
    entry = MachineEntry(key, "", hostname=hostname)
    context = resolve_local_execution_context(
        {key: entry}, hostname=hostname, environment=environment,
        configured_machine=hostname if configured else None,
    )
    assert context.machine == hostname
    assert context.execution_machine == (f"{hostname}-wsl" if environment == "wsl" else hostname)
    assert context.customization_key == (None if environment == "wsl" else key)


@pytest.mark.parametrize(("environment", "first_key", "second_key"), [
    ("windows", "desk (Windows)", "desk(Win)"),
    ("wsl", "desk (WSL)", "desk(WSL)"),
])
@pytest.mark.parametrize("host", ["host-a", "host-b"])
def test_different_physical_entries_cannot_share_a_canonicalized_execution_key(
    environment: str, first_key: str, second_key: str, host: str,
) -> None:
    entries = {
        first_key: MachineEntry(first_key, "", hostname="host-a", physical_host="physical-a"),
        second_key: MachineEntry(second_key, "", hostname="host-b", physical_host="physical-b"),
    }
    with pytest.raises(ValueError, match="conflicts"):
        resolve_local_execution_context(entries, hostname=host, environment=environment)


@pytest.mark.parametrize("physical", ["Physical computer", "Rack/Computer", "Rack#1"])
def test_structured_physical_names_are_not_execution_reference_tokens(physical: str) -> None:
    entry = MachineEntry(
        "opaque-device", "", hostname="raw-host", physical_host=physical, execution_platform="linux",
    )
    context = resolve_local_execution_context(
        {entry.key: entry}, hostname="raw-host", environment="linux", configured_machine=entry.key,
    )
    assert (context.machine, context.execution_machine) == (physical, "opaque-device")


@pytest.mark.parametrize("physical", ["Physical computer", "Rack/Computer", "Rack#1", r"Rack\Computer"])
@pytest.mark.parametrize(("declared", "environment", "binding"), [
    ("windows", "wsl", "native-label-wsl"),
    ("wsl", "windows", "native-label-win"),
    ("wsl", "linux", "{physical}"),
    ("", "wsl", "native"),
])
def test_missing_customization_keeps_structured_physical_label_and_clean_local_key(
    physical: str, declared: str, environment: str, binding: str,
) -> None:
    entry = MachineEntry(
        "native", "Friendly computer", hostname="RAW-HOST", alias="native-label",
        physical_host=physical, execution_platform=declared,
    )
    entries = {entry.key: entry}
    observed = resolve_local_execution_context(entries, hostname="RAW-HOST", environment=environment)
    configured = resolve_local_execution_context(
        entries, hostname="RAW-HOST", environment=environment,
        configured_machine=binding.format(physical=physical),
    )
    assert observed == configured
    assert (observed.machine, observed.execution_machine, observed.customization_key) == (
        physical, "raw-host-wsl" if environment == "wsl" else "raw-host", None,
    )


@pytest.mark.parametrize("configured", [False, True])
@pytest.mark.parametrize("slot", ["key", "alias", "compatibility_aliases"])
def test_structured_physical_fallback_still_checks_execution_collisions(
    configured: bool, slot: str,
) -> None:
    native = MachineEntry(
        "native", "", hostname="raw-host", physical_host="Rack/Computer",
        execution_platform="windows", alias="native-label",
    )
    other = MachineEntry(
        "RAW-HOST-WSL" if slot == "key" else "foreign", "", execution_platform="linux",
        alias="RAW-HOST-WSL" if slot == "alias" else "",
        compatibility_aliases=("RAW-HOST-WSL",) if slot == "compatibility_aliases" else (),
    )
    with pytest.raises(ValueError, match="conflicts"):
        resolve_local_execution_context(
            {native.key: native, other.key: other}, hostname="raw-host", environment="wsl",
            configured_machine="native-label-wsl" if configured else None,
        )


@pytest.mark.parametrize("name", ["bad\0host", "bad\nhost", "bad\rhost", "bad\u2003host"])
def test_execution_reference_tokens_reject_unclean_or_multiterm_names(name: str) -> None:
    with pytest.raises(IdentityError):
        resolve_local_execution_context({}, hostname="raw-host", environment="linux", configured_machine=name)


@pytest.mark.parametrize("key", ["opaque\x1b-id", "opaque\x7f-id"])
def test_existing_clean_registered_tokens_are_not_reinterpreted(key: str) -> None:
    entry = MachineEntry(key, "", hostname="raw-host", physical_host="physical", execution_platform="linux")
    context = resolve_local_execution_context(
        {key: entry}, hostname="raw-host", environment="linux", configured_machine=key,
    )
    assert context.execution_machine == key


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
@pytest.mark.parametrize("configured", [False, True])
def test_generated_guest_key_cannot_shadow_native_entry(key: str, configured: bool) -> None:
    entries = {key: MachineEntry(key, "", execution_platform="windows")}
    with pytest.raises(ValueError, match="conflicts"):
        resolve_local_execution_context(
            entries, hostname="raw-host", environment="wsl",
            configured_machine="raw-host" if configured else None,
        )


@pytest.mark.parametrize("slot", ["alias", "compatibility_aliases", "ssh_profile"])
@pytest.mark.parametrize("configured", [False, True])
def test_generated_guest_key_cannot_shadow_identity_labels(slot: str, configured: bool) -> None:
    entry = MachineEntry(
        "foreign-native", "", execution_platform="windows",
        alias="RAW-HOST-WSL" if slot == "alias" else "",
        compatibility_aliases=("RAW-HOST-WSL",) if slot == "compatibility_aliases" else (),
        ssh_environments=[SSHEnvironment("windows", "RAW-HOST-WSL")] if slot == "ssh_profile" else [],
    )
    with pytest.raises(ValueError, match="conflicts"):
        resolve_local_execution_context(
            {entry.key: entry}, hostname="raw-host", environment="wsl",
            configured_machine="raw-host" if configured else None,
        )


@pytest.mark.parametrize("hostname", ["raw-host", "host"])
def test_unscoped_guest_sibling_precedes_unscoped_native_fallback(hostname: str) -> None:
    entries = {
        key: MachineEntry(key, "", hostname="raw-host")
        for key in ("host", "host-wsl")
    }
    context = resolve_local_execution_context(entries, hostname=hostname, environment="wsl")
    assert (context.machine, context.execution_machine, context.customization_key) == (
        "host", "host-wsl", "host-wsl",
    )


@pytest.mark.parametrize(("key", "hostname", "actual", "observed"), [
    ("foreign", "raw-host-wsl", "wsl", "raw-host"),
    ("foreign-wsl", "raw-host", "windows", "raw-host-wsl"),
])
def test_generated_execution_cannot_shadow_legacy_hostname_addresses(
    key: str, hostname: str, actual: str, observed: str,
) -> None:
    entry = MachineEntry(key, "", hostname=hostname)
    with pytest.raises(ValueError, match="conflicts"):
        resolve_local_execution_context({key: entry}, hostname=observed, environment=actual)


@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
def test_selected_unscoped_keys_are_not_regenerated_from_metadata(environment: str) -> None:
    key = "custom-wsl" if environment == "wsl" else "custom"
    entry = MachineEntry(key, "", hostname="raw-host", alias="friendly", physical_host="physical")
    entries = {key: entry}
    automatic = resolve_local_execution_context(entries, hostname="raw-host", environment=environment)
    configured = resolve_local_execution_context(
        entries, hostname="raw-host", environment=environment, configured_machine=key,
    )
    assert automatic == configured
    assert automatic.execution_machine == key


@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
def test_observed_compatibility_name_keeps_the_matching_customization(environment: str) -> None:
    entry = MachineEntry(
        "custom-execution", "", physical_host="physical",
        execution_platform=environment, compatibility_aliases=("old-host",),
    )
    context = resolve_local_execution_context(
        {entry.key: entry}, hostname="OLD-HOST", environment=environment,
    )
    assert (context.machine, context.execution_machine) == ("physical", entry.key)


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

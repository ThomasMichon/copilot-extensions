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


@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
def test_unmapped_literal_hostname_can_remain_the_local_binding(environment: str) -> None:
    context = resolve_local_execution_context(
        {}, hostname="literal-wsl", environment=environment, configured_machine="literal-wsl",
    )
    assert context.machine == "literal-wsl"
    assert context.execution_machine == ("literal-wsl-wsl" if environment == "wsl" else "literal-wsl")


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


@pytest.mark.parametrize(("environment", "profile"), [
    ("windows", "linux"), ("wsl", "windows"), ("linux", "windows"),
])
def test_literal_host_profile_alias_cannot_override_its_environment(
    environment: str, profile: str,
) -> None:
    entry = MachineEntry(
        "literal-wsl", "", hostname="literal-wsl",
        ssh_environments=[SSHEnvironment(profile, "profile-only")],
    )
    with pytest.raises(ValueError, match="conflicts"):
        resolve_local_execution_context(
            {entry.key: entry}, hostname="literal-wsl", environment=environment,
            configured_machine="profile-only",
        )


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


@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
def test_unnamed_profile_does_not_assert_a_conflicting_environment(environment: str) -> None:
    key = "desk-wsl" if environment == "wsl" else "desk"
    entry = MachineEntry(key, "", hostname="raw-host", ssh_environments=[SSHEnvironment("", "generic")])
    context = resolve_local_execution_context(
        {key: entry}, hostname="raw-host", environment=environment, configured_machine="generic",
    )
    assert (context.machine, context.execution_machine, context.customization_key) == ("desk", key, key)


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

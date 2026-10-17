"""Exact identities and legacy label formats share one contextual mapping grid."""

from __future__ import annotations
from dataclasses import replace

import pytest

from machine_transport import (
    MachineEntry, SSHEnvironment, legacy_alias_forms, normalize_machine_entries,
    parse_machines_yaml, resolve_machine_selection, split_machine_environment,
    is_local_machine,
    is_observed_local_name,
    machine_identity_names,
)


def test_explicit_identity_names_exclude_legacy_physical_metadata() -> None:
    entry = MachineEntry(
        key="Example-Host", display_name="Example workstation", hostname="Generated-Host",
        alias="native-alias", compatibility_aliases=("legacy-alias",),
        ssh_environments=[SSHEnvironment("windows", "native-profile")],
    )
    assert machine_identity_names(entry, include_metadata=False) == (
        "Example-Host", "native-alias", "legacy-alias", "native-profile",
    )
    assert "Generated-Host" in machine_identity_names(entry)
    assert "Example workstation" in machine_identity_names(entry, entries={entry.key: entry})
    assert "Example workstation" not in machine_identity_names(
        entry, entries={entry.key: entry}, include_metadata=False,
    )


@pytest.mark.parametrize("actual", ["windows", "wsl"])
@pytest.mark.parametrize("standalone", [False, True])
def test_observed_hostname_without_metadata_is_a_contextual_local_fallback(actual, standalone):
    registry = {} if standalone else {
        key: replace(entry, hostname="") for key, entry in entries(True).items()
    }
    configured = "Example-Host-wsl" if actual == "wsl" else "Example-Host"
    for alias in legacy_alias_forms("Observed-Host", actual):
        assert is_observed_local_name(
            alias.swapcase(), config_machine=configured, entries=registry,
            environment=actual, real_hostname="Observed-Host",
        )
    assert not is_observed_local_name(
        "Observed-Host-wsl" if actual == "windows" else "Observed-Host-win",
        config_machine=configured, entries=registry, environment=actual,
        real_hostname="Observed-Host",
    )
    if not standalone:
        for field in ("key", "profile"):
            foreign = MachineEntry(
                key="Observed-Host" if field == "key" else "foreign-space",
                display_name="Foreign", execution_platform=actual, physical_host="foreign-host",
                ssh_environments=[SSHEnvironment(actual, "Observed-Host" if field == "profile" else "foreign")],
            )
            assert not is_observed_local_name(
                "Observed-Host", config_machine=configured,
                entries={**registry, foreign.key: foreign}, environment=actual,
                real_hostname="Observed-Host",
            )


def entries(explicit: bool):
    registry = normalize_machine_entries(parse_machines_yaml({"machines": {
        "Example-Host": {
            "hostname": "Generated-Host", "alias": "Legacy-Alias",
            "display_name": "Example Workstation",
            "ssh": {"environments": [
                {"name": "windows", "alias": "native-profile"},
                {"name": "wsl", "alias": "guest-profile"},
            ]},
        },
    }}))
    return {
        key: replace(entry, legacy_key="" if explicit else entry.legacy_key)
        for key, entry in registry.items()
    }


@pytest.mark.parametrize("actual", ["windows", "wsl"])
@pytest.mark.parametrize("name", ["example-host", "EXAMPLE-HOST"])
@pytest.mark.parametrize("automatic", [False, True])
def test_overlapping_legacy_key_and_profile_keep_historical_host_selection(actual, name, automatic):
    registry = normalize_machine_entries(parse_machines_yaml({"machines": {
        "example-host": {"ssh": {"environments": [
            {"name": "windows", "alias": name},
            {"name": "wsl", "alias": "guest-login"},
        ]}},
    }}))
    selected = resolve_machine_selection(registry, name, actual, allow_physical_context=automatic)
    assert selected.entry.key == ("example-host-wsl" if actual == "wsl" else "example-host")


@pytest.mark.parametrize("automatic", [False, True])
def test_overlapping_scoped_key_retains_manual_authority_and_automatic_physical_context(automatic):
    native = MachineEntry(
        key="example-host", display_name="Native", execution_platform="windows",
        physical_host="example-host", ssh_environments=[SSHEnvironment("windows", "example-host")],
    )
    guest = MachineEntry(
        key="example-host-wsl", display_name="Guest", execution_platform="wsl",
        physical_host="example-host", ssh_environments=[SSHEnvironment("wsl", "guest-login")],
    )
    registry = {native.key: native, guest.key: guest}
    if automatic:
        assert resolve_machine_selection(
            registry, native.key, "wsl", allow_physical_context=True,
        ).entry is guest
    else:
        with pytest.raises(ValueError, match="conflicts"):
            resolve_machine_selection(registry, native.key, "wsl")
    foreign = replace(native, physical_host="foreign-host")
    with pytest.raises(ValueError, match="conflicts"):
        resolve_machine_selection(
            {foreign.key: foreign, guest.key: guest}, native.key, "wsl",
            allow_physical_context=automatic,
        )


@pytest.mark.parametrize("automatic", [False, True])
def test_profile_only_match_cannot_borrow_the_overlapping_key_exception(automatic):
    registry = normalize_machine_entries(parse_machines_yaml({"machines": {
        "example-host": {"ssh": {"environments": [
            {"name": "windows", "alias": "native-login"},
            {"name": "wsl", "alias": "guest-login"},
        ]}},
    }}))
    with pytest.raises(ValueError, match="conflicts"):
        resolve_machine_selection(
            registry, "native-login", "wsl", allow_physical_context=automatic,
        )


CELLS = [
    (platform, base, form, variant)
    for platform in ("windows", "wsl")
    for base in ("Example-Host", "Generated-Host", "Legacy-Alias", "Example Workstation")
    for form in legacy_alias_forms(base, platform)
    for variant in (form, form.swapcase())
]


@pytest.mark.parametrize("explicit", [False, True], ids=["projected", "explicit"])
@pytest.mark.parametrize("platform,base,form,alias", CELLS)
def test_contextual_alias_mapping_grid(explicit, platform, base, form, alias):
    registry = entries(explicit)
    selected = resolve_machine_selection(registry, alias, platform)
    expected = "Example-Host-wsl" if platform == "wsl" else "Example-Host"
    assert selected.entry.key == expected
    assert selected.target.environment == platform
    assert selected.target.machine == "Example-Host"
    assert registry[expected].key == expected


@pytest.mark.parametrize("platform", ["windows", "wsl"])
def test_unqualified_physical_metadata_requires_context(platform):
    registry = entries(True)
    with pytest.raises(ValueError, match="does not select an execution space"):
        resolve_machine_selection(registry, "Generated-Host")
    assert resolve_machine_selection(registry, "Generated-Host", platform).entry.execution_platform == platform


@pytest.mark.parametrize("form,environment", [
    ("Example-Host-win", "WSL"), ("Example-Host (Windows)", "wsl"),
    ("Example-Host-wsl", "Win"), ("Example-Host (WSL)", "Windows"),
])
def test_inline_and_supplied_context_conflicts_reject(form, environment):
    with pytest.raises(ValueError, match="conflicts"):
        split_machine_environment(form, environment)
    with pytest.raises(ValueError, match="conflicts"):
        resolve_machine_selection({}, form, environment)


def test_exact_registered_key_and_profile_precede_format_inference():
    opaque = MachineEntry(
        key="Example-Host-wsl", display_name="Opaque native", execution_platform="windows",
        ssh_environments=[SSHEnvironment("windows", "Profile (WSL)")],
    )
    registry = {opaque.key: opaque}
    for alias in ("EXAMPLE-HOST-WSL", "profile (wsl)"):
        selected = resolve_machine_selection(registry, alias)
        assert selected.entry is opaque
        assert selected.target.environment == "windows"
        assert selected.target.machine == opaque.key
        with pytest.raises(ValueError, match="conflicts"):
            resolve_machine_selection(registry, alias, "WSL")


def test_native_profile_is_not_a_global_guest_alias():
    registry = entries(True)
    assert resolve_machine_selection(registry, "native-profile").entry.execution_platform == "windows"
    with pytest.raises(ValueError, match="conflicts"):
        resolve_machine_selection(registry, "native-profile", "WSL", allow_physical_context=True)
    assert resolve_machine_selection(
        registry, "Example-Host", "WSL", allow_physical_context=True,
    ).entry.execution_platform == "wsl"


@pytest.mark.parametrize("explicit", [False, True])
def test_guest_key_cannot_borrow_native_context_even_with_legacy_projection(explicit):
    registry = entries(explicit)
    with pytest.raises(ValueError, match="conflicts"):
        resolve_machine_selection(registry, "Example-Host-wsl", "Windows", allow_physical_context=True)


@pytest.mark.parametrize("label,platform", [
    (" Example-Host ( wSl ) ", "wsl"), ("Example-Host( WiNdOwS )", "windows"),
])
def test_parenthesized_label_whitespace_is_owned_by_the_format_parser(label, platform):
    selected = resolve_machine_selection(entries(True), label)
    assert selected.target.environment == platform


@pytest.mark.parametrize("platform", ["windows", "wsl"])
def test_historical_registry_keys_are_selected_not_renamed(platform):
    for key in legacy_alias_forms("Example-Host", platform):
        entry = MachineEntry(key=key, display_name=key, hostname="Generated-Host")
        for alias in legacy_alias_forms("Example-Host", platform):
            selected = resolve_machine_selection({key: entry}, alias, platform)
            assert selected.entry is entry
            assert selected.entry.key == key


@pytest.mark.parametrize("platform", ["windows", "wsl"])
def test_standalone_form_aliases_still_require_the_same_execution_context(monkeypatch, platform):
    monkeypatch.setattr("machine_transport.identity.local_execution_platform", lambda: platform)
    configured = "Example-Host-wsl" if platform == "wsl" else "Example-Host"
    for alias in legacy_alias_forms("Example-Host", platform):
        assert is_local_machine(
            alias.swapcase(), config_machine=configured, load_entries=lambda: {}, guest=platform == "wsl",
        )
    other = "Example-Host (Windows)" if platform == "wsl" else "Example-Host (WSL)"
    assert not is_local_machine(
        other, config_machine=configured, load_entries=lambda: {}, guest=platform == "wsl",
    )

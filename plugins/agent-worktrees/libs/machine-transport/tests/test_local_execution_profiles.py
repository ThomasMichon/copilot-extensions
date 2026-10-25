from __future__ import annotations

import pytest

from machine_transport import (
    MachineEntry,
    SSHEnvironment,
    normalize_machine_entries,
    parse_machines_yaml,
    resolve_local_execution_context,
    resolve_machine_selection,
    resolve_ssh_target,
)

pytestmark = pytest.mark.contract("machine-transport.local-context.profile")


@pytest.mark.parametrize("guest_label", ["wsl", " WSL ", "WsL"])
@pytest.mark.parametrize("configured", [False, True])
@pytest.mark.parametrize("physical_key", ["desk", "literal-wsl", "literal(WSL)"])
def test_normalized_guest_profile_keeps_declared_guest_customization(
    guest_label: str, configured: bool, physical_key: str,
) -> None:
    entries = parse_machines_yaml({"machines": {physical_key: {
        "hostname": "raw-host",
        "ssh": {"environments": [{"name": guest_label, "alias": "guest-profile"}]},
    }}}, preserve_environment_values=True, keep_unnamed_environments=True)
    context = resolve_local_execution_context(
        entries, hostname="raw-host", environment="wsl",
        configured_machine="guest-profile" if configured else None,
    )
    assert (context.machine, context.execution_machine, context.customization_key) == (
        physical_key, f"{physical_key}-wsl", f"{physical_key}-wsl",
    )
    assert entries[physical_key].ssh_environments[0].name == guest_label


def test_logical_projection_canonicalizes_roles_without_mutating_raw_profile_values() -> None:
    entries = parse_machines_yaml({"machines": {"desk": {
        "ssh": {"environments": [
            {"name": "WIN", "alias": "native-profile"},
            {"name": " WSL ", "alias": "guest-profile"},
        ]},
    }}}, preserve_environment_values=True)
    normalized = normalize_machine_entries(entries)
    assert list(normalized) == ["desk", "desk-wsl"]
    assert normalized == normalize_machine_entries(normalized)
    assert [env.name for env in entries["desk"].ssh_environments] == ["WIN", " WSL "]
    assert resolve_ssh_target(normalized["desk"]) == ("native-profile", "pwsh")
    assert resolve_ssh_target(normalized["desk-wsl"]) == ("guest-profile", "bash")


@pytest.mark.parametrize("policy", ["preserved", "required-alias"])
def test_explicit_space_accepts_equivalent_profile_labels_without_relabeling_keys(policy: str) -> None:
    entries = parse_machines_yaml({"machines": {"opaque-guest": {
        "execution_platform": "wsl", "physical_host": "desk",
        "ssh": {"environments": [{"name": " WSL ", "alias": "guest-profile"}]},
    }}}, preserve_environment_values=policy == "preserved", require_alias=policy == "required-alias")
    normalized = normalize_machine_entries(entries)
    assert list(normalized) == ["opaque-guest"]
    assert entries["opaque-guest"].ssh_environments[0].name == " WSL "
    assert normalized["opaque-guest"].ssh_environments[0].name == "wsl"
    assert resolve_ssh_target(normalized["opaque-guest"]) == ("guest-profile", "bash")


@pytest.mark.parametrize("policy", ["preserved", "required-alias"])
@pytest.mark.parametrize("profile_label", ["WIN", "LiNuX", None])
def test_profile_label_normalization_does_not_accept_a_foreign_or_unknown_space(
    policy: str, profile_label: str | None,
) -> None:
    with pytest.raises(ValueError, match="another space"):
        parse_machines_yaml({"machines": {"opaque-guest": {
            "execution_platform": "wsl",
            "ssh": {"environments": [{"name": profile_label, "alias": "profile"}]},
        }}}, preserve_environment_values=policy == "preserved",
            require_alias=policy == "required-alias", keep_unnamed_environments=True)


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


@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
def test_unnamed_profile_does_not_assert_a_conflicting_environment(environment: str) -> None:
    key = "desk-wsl" if environment == "wsl" else "desk"
    entry = MachineEntry(key, "", hostname="raw-host", ssh_environments=[SSHEnvironment("", "generic")])
    context = resolve_local_execution_context(
        {key: entry}, hostname="raw-host", environment=environment, configured_machine="generic",
    )
    assert (context.machine, context.execution_machine, context.customization_key) == ("desk", key, key)


@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("nullable", ["name", "alias", "both"])
@pytest.mark.parametrize("policy", ["preserved", "required-alias"])
def test_legacy_nullable_profile_metadata_remains_non_authoritative(
    environment: str, nullable: str, policy: str,
) -> None:
    name = None if nullable in ("name", "both") else environment
    alias = None if nullable in ("alias", "both") else "profile"
    entries = parse_machines_yaml({"machines": {"desk": {
        "alias": "friendly", "hostname": "raw-host",
        "ssh": {"environments": [{"name": name, "alias": alias}]},
    }}}, preserve_environment_values=policy == "preserved",
        require_alias=policy == "required-alias", keep_unnamed_environments=True)
    query = alias or "friendly"
    selected = resolve_machine_selection(entries, query, environment)
    assert (selected.target.machine, selected.target.environment) == ("desk", environment)
    context = resolve_local_execution_context(
        entries, hostname="raw-host", environment=environment, configured_machine=query,
    )
    assert (context.machine, context.environment, context.execution_machine) == (
        "desk", environment, "desk-wsl" if environment == "wsl" else "desk",
    )
    profile = entries["desk"].ssh_environments[0]
    assert (profile.name, profile.alias) == (name, alias)


@pytest.mark.parametrize("policy", ["preserved", "required-alias"])
def test_nullable_profile_alias_does_not_hide_an_explicit_platform_conflict(policy: str) -> None:
    entries = parse_machines_yaml({"machines": {"native": {
        "alias": "friendly", "execution_platform": "windows",
        "ssh": {"environments": [{"name": "windows", "alias": None}]},
    }}}, preserve_environment_values=policy == "preserved", require_alias=policy == "required-alias")
    with pytest.raises(ValueError, match="conflicts"):
        resolve_machine_selection(entries, "friendly", "wsl")


@pytest.mark.parametrize("policy", ["preserved", "required-alias"])
def test_non_string_profile_environment_is_an_explicit_validation_error(policy: str) -> None:
    entries = parse_machines_yaml({"machines": {"desk": {
        "ssh": {"environments": [{"name": 123, "alias": "profile"}]},
    }}}, preserve_environment_values=policy == "preserved", require_alias=policy == "required-alias")
    with pytest.raises(ValueError, match="string or null"):
        resolve_machine_selection(entries, "profile", "linux")


@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("label", ["legacy-label", "Developer shell"])
def test_unknown_legacy_profile_labels_do_not_assert_a_foreign_platform(
    environment: str, label: str,
) -> None:
    entries = parse_machines_yaml({"machines": {"desk": {
        "hostname": "raw-host", "ssh": {"environments": [{"name": label, "alias": "profile"}]},
    }}}, preserve_environment_values=True)
    context = resolve_local_execution_context(
        entries, hostname="raw-host", environment=environment, configured_machine="profile",
    )
    assert context.execution_machine == ("desk-wsl" if environment == "wsl" else "desk")
    assert entries["desk"].ssh_environments[0].name == label


@pytest.mark.parametrize("scoped_neighbor", [False, True])
@pytest.mark.parametrize("profile_name", [
    "native-profile", "native-profile-wsl", "native-profile (WSL)",
])
def test_legacy_guest_selection_never_retargets_a_native_profile(
    scoped_neighbor: bool, profile_name: str,
) -> None:
    native = MachineEntry(
        "friendly", "", hostname="raw-host",
        ssh_environments=[SSHEnvironment("windows", "native-profile")],
    )
    guest = MachineEntry("friendly-wsl", "", hostname="raw-host")
    entries = {native.key: native, guest.key: guest}
    if scoped_neighbor:
        entries["other"] = MachineEntry("other", "", execution_platform="linux")
    with pytest.raises(ValueError, match="conflicts"):
        resolve_machine_selection(entries, profile_name, "wsl")


@pytest.mark.parametrize(("environment", "profile"), [
    ("windows", "WIN"), ("wsl", " WSL "), ("linux", "LiNuX"),
])
def test_named_profile_environment_uses_shared_normalization(environment: str, profile: str) -> None:
    key = "desk"
    entry = MachineEntry(key, "", hostname="raw-host", ssh_environments=[SSHEnvironment(profile, "profile")])
    context = resolve_local_execution_context(
        {key: entry}, hostname="raw-host", environment=environment, configured_machine="profile",
    )
    assert context.execution_machine == ("desk-wsl" if environment == "wsl" else key)

"""Opt-in legacy parser policies must not alter existing consumers."""

import pytest

from machine_transport import MachineEntry, find_machine_entry, parse_machines_yaml


def test_raw_parser_defaults_and_bridge_policy_are_independent():
    raw = {"machines": {"box": {"ssh": {"environments": [
        {}, {"name": "linux", "port": 2200, "user": "dev"},
        {"name": "wsl", "alias": "", "shell": ""},
    ]}}}}
    entry = parse_machines_yaml(raw)["box"]
    assert [(env.name, env.alias, env.shell) for env in entry.ssh_environments] == [
        ("linux", "", ""), ("wsl", "", ""),
    ]
    assert (entry.ssh_environments[0].port, entry.ssh_environments[0].user) == (2200, "dev")
    assert parse_machines_yaml(raw, require_alias=True)["box"].ssh_environments[0].name == "wsl"
    legacy = parse_machines_yaml(
        raw, default_ssh_alias_to_key=True, default_ssh_shell="bash",
        keep_unnamed_environments=True,
        preserve_environment_values=True,
    )["box"]
    assert [(env.name, env.alias, env.shell) for env in legacy.ssh_environments] == [
        ("", "box", "bash"), ("linux", "box", "bash"), ("wsl", "", ""),
    ]
    with pytest.raises(ValueError, match="mapping"):
        parse_machines_yaml({"machines": []})


def test_strict_matching_is_opt_in_and_exact_keys_keep_precedence():
    first = MachineEntry("first", "First", alias="same")
    second = MachineEntry("second", "Second", alias="SAME")
    entries = {"first": first, "second": second}
    assert find_machine_entry(entries, "same") is first
    with pytest.raises(ValueError, match="ambiguous"):
        find_machine_entry(entries, "same", reject_ambiguous=True)
    assert find_machine_entry(entries, "first", reject_ambiguous=True) is first
    assert find_machine_entry(entries, "", reject_ambiguous=True) is None


@pytest.mark.parametrize("policy", [
    {"default_ssh_shell": "bash"},
    {"default_ssh_alias_to_key": True},
    {"keep_unnamed_environments": True},
])
def test_missing_value_policies_preserve_unrelated_normalization(policy):
    raw = {"machines": {"box": {"ssh": {"environments": [
        {"name": " linux ", "alias": " host ", "shell": " zsh "},
        {"name": 123, "alias": 456, "shell": 789},
    ]}}}}
    environments = parse_machines_yaml(raw, **policy)["box"].ssh_environments
    assert [(env.name, env.alias, env.shell) for env in environments] == [
        ("linux", "host", "zsh"), ("123", "456", "789"),
    ]

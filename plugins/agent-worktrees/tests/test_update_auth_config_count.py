"""Inherited GIT_CONFIG_COUNT handling for command-scope credential routing."""

from __future__ import annotations

import pytest

from agent_worktrees import update_auth as auth


def test_empty_inherited_command_config_count_means_no_entries() -> None:
    # Git accepts an empty GIT_CONFIG_COUNT as zero entries; source recovery
    # inherited one and failed its payload auth report with int('').
    scope = auth.command_scope({"GIT_CONFIG_COUNT": ""}, [("credential.helper", "")])
    assert scope == {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": "",
    }


@pytest.mark.parametrize(("value", "message"), [(" ", "non-negative integer"), ("-1", "negative")])
def test_malformed_inherited_command_config_count_is_refused(value: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        auth.command_scope({"GIT_CONFIG_COUNT": value}, [])
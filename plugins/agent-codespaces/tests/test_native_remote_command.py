"""Prepared remote command attribution is reused, never replaced with PATH."""

import json
import shlex

import pytest

from ssh_manager.remote_command import render_command, validate_descriptor


def descriptor():
    return {
        "schema": "copilot-extensions.remote-command", "version": 1,
        "argv": ["/payload with spaces/bin/agent-bridge"],
        "receipt": {"path": "/installation/current.json", "sha256": "a" * 64},
    }


@pytest.mark.parametrize("action", ["capabilities", "start", "activate", "status", "message", "result", "stop"])
def test_every_native_remote_operation_retains_prepared_receipt_and_argv(action):
    value = descriptor()
    command = shlex.split(render_command(value, ["native-host", action]))
    assert command[:2] == ["python3", "-c"]
    assert json.loads(command[3]) == value
    assert command[4:] == ["native-host", action]
    assert "os.execv(argv[0],argv)" in command[2]


@pytest.mark.parametrize("mutate", [
    lambda value: value["argv"].__setitem__(0, "agent-bridge"),
    lambda value: value["receipt"].__setitem__("path", "relative.json"),
    lambda value: value["receipt"].__setitem__("sha256", "invalid"),
])
def test_invalid_remote_owner_does_not_use_a_legacy_fallback(mutate):
    value = descriptor()
    mutate(value)
    with pytest.raises(ValueError):
        validate_descriptor(value)

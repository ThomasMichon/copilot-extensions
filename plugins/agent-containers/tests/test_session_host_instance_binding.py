"""Mutable names cannot redirect preparation or credential projection."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent_containers import __main__ as cli
from agent_containers.config import ContainersConfig, FleetConfig
from agent_containers.session_host_context import resolve_context


@pytest.mark.parametrize("replacement_phase", ["discovery", "inspection"])
def test_replaced_instance_refuses_before_any_projection(monkeypatch, replacement_phase):
    config = ContainersConfig()
    config.fleets["example"] = FleetConfig(security_profile="trusted")
    expected, replacement = "a" * 64, "b" * 64
    info = SimpleNamespace(
        fleet="example", state="running",
        container_id=replacement if replacement_phase == "discovery" else expected,
    )
    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr("agent_containers.lifecycle.get_container", lambda *args: info)
    inspection = MagicMock(return_value={
        "Id": replacement,
        "Config": {"Labels": {"agent-containers.security-profile": "trusted"}},
    })
    monkeypatch.setattr("agent_containers.lifecycle.inspect_container", inspection)
    effects = []
    for name in (
        "prepare_ssh_config", "cleanup_remote_envs", "container_environment",
        "host_gh_token", "write_remote_env",
    ):
        mock = MagicMock(side_effect=lambda *args: effects.append(args))
        monkeypatch.setattr(cli, name, mock)
    with pytest.raises(RuntimeError, match="selected container instance"):
        cli._cmd_session_host_prepare(SimpleNamespace(
            name="example-1", expected_instance=expected, host_relay_port=None,
        ))
    assert effects == []
    if replacement_phase == "inspection":
        inspection.assert_called_once_with(expected)
    else:
        inspection.assert_not_called()


def test_preparation_pins_every_remote_operation_to_selected_id(monkeypatch, capsys):
    selected = "a" * 64
    config = ContainersConfig()
    config.forward_gh_token = False
    config.fleets["example"] = FleetConfig(
        security_profile="trusted", acp_command="{{target_preference_launcher}} copilot --acp",
    )
    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr("agent_containers.lifecycle.get_container", lambda *args: SimpleNamespace(
        fleet="example", state="running", container_id=selected,
    ))
    monkeypatch.setattr("agent_containers.lifecycle.inspect_container", lambda target: {
        "Id": selected, "Config": {"Labels": {"agent-containers.security-profile": "trusted"}},
    })
    monkeypatch.setattr(config, "credentials_for", lambda fleet: (False, False))
    # dataclasses.asdict requires the actual transport record.
    from agent_containers.ssh_transport import SSHConfig

    ssh = SSHConfig(host_alias="example-pinned", user="node")
    effects = []

    def operation(kind, result):
        def call(target, *args):
            assert target == selected
            effects.append(kind)
            return result
        return call

    monkeypatch.setattr(cli, "prepare_ssh_config", operation("ssh", ssh))
    monkeypatch.setattr(cli, "cleanup_remote_envs", operation("cleanup", None))
    monkeypatch.setattr(cli, "container_environment", operation("environment", {}))
    monkeypatch.setattr(cli, "write_remote_env", operation("write", None))
    assert cli._cmd_session_host_prepare(SimpleNamespace(
        name="example-1", expected_instance=selected, host_relay_port=None,
    )) == 0
    result = json.loads(capsys.readouterr().out)
    assert effects == ["ssh", "cleanup", "environment", "write"]
    assert result["execution_instance"] == selected
    assert result["state_command"][-2:] == ["--expected-instance", selected]


def test_context_inspects_immutable_id_not_name(monkeypatch):
    config = ContainersConfig()
    config.fleets["example"] = FleetConfig(security_profile="trusted")
    selected = "a" * 64
    monkeypatch.setattr("agent_containers.lifecycle.get_container", lambda *args: SimpleNamespace(
        fleet="example", state="running", container_id=selected,
    ))
    inspection = MagicMock(return_value={
        "Id": selected, "Config": {"Labels": {"agent-containers.security-profile": "trusted"}},
    })
    monkeypatch.setattr("agent_containers.lifecycle.inspect_container", inspection)
    assert resolve_context("example-1", config, selected).instance_id == selected
    inspection.assert_called_once_with(selected)


@pytest.mark.parametrize("removed", [True, False])
def test_secret_cleanup_survives_name_replacement_and_uses_bound_user(monkeypatch, removed):
    selected = "a" * 64
    monkeypatch.setattr("agent_containers.lifecycle.get_container", lambda *args: pytest.fail(
        "cleanup must not rediscover a mutable name",
    ))
    inspection = MagicMock(return_value={
        "Id": selected, "Config": {"Labels": {"agent-containers.security-profile": "trusted"}},
    })
    monkeypatch.setattr("agent_containers.lifecycle.inspect_container", inspection)
    cleanup = MagicMock(return_value=removed)
    monkeypatch.setattr(cli, "cleanup_remote_env", cleanup)
    path = "/home/your_user/.agent-containers/launch/" + "0" * 32 + ".env"
    args = SimpleNamespace(
        name="name-now-points-elsewhere", expected_instance=selected,
        expected_user="runner", remote_env=path,
    )
    if removed:
        assert cli._cmd_session_host_cleanup(args) == 0
    else:
        with pytest.raises(RuntimeError, match="could not be confirmed"):
            cli._cmd_session_host_cleanup(args)
    inspection.assert_called_once_with(selected)
    cleanup.assert_called_once_with(selected, "runner", path)

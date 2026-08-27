"""Tests for the container `namespace-*` CLI seam (dotfiles #892 Increment 3b)."""

from __future__ import annotations

import json
import types
from unittest.mock import patch

import pytest
from ssh_manager import SSHConfig

from agent_containers.__main__ import main


def test_namespace_list_json(capsys):
    async def _list_specs(self):
        return [{"name": "example-web-1", "display_name": "example-web-1 (example-web)",
                 "description": "Local dev container", "icon": "container",
                 "state": "running"}]

    with patch("agent_containers.resolver.ContainerResolver.list_specs", _list_specs):
        rc = main(["namespace-list"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)[0]["name"] == "example-web-1"


def test_namespace_resolve_json(capsys):
    async def _spec(self, name):
        return {
            "type": "command",
            "spawn_command": ["docker", "exec", name],
            "user": "node",
            "venue": {
                "schema_version": 1,
                "provider": "agent-containers",
                "target_id": f"container:{name}",
            },
        }

    with patch("agent_containers.resolver.ContainerResolver.resolve_spec", _spec):
        rc = main(["namespace-resolve", "example-web-1"])
    assert rc == 0
    d = json.loads(capsys.readouterr().out)
    assert d["spawn_command"] == ["docker", "exec", "example-web-1"] and d["user"] == "node"
    assert d["venue"]["target_id"] == "container:example-web-1"


def test_namespace_resolve_not_found_exit3(capsys):
    async def _spec(self, name):
        raise KeyError(name)

    with patch("agent_containers.resolver.ContainerResolver.resolve_spec", _spec):
        assert main(["namespace-resolve", "nope"]) == 3


def test_namespace_target_repo_is_empty(capsys):
    # Containers do not drive related-repo plugin injection -> always empty.
    assert main(["namespace-target-repo", "example-web-1"]) == 0
    assert capsys.readouterr().out.strip() == ""


def test_namespace_ensure_ready_ok_and_fail(capsys):
    async def _ok(self, name):
        return None

    with patch("agent_containers.resolver.ContainerResolver.ensure_ready", _ok):
        assert main(["namespace-ensure-ready", "example-web-1"]) == 0

    async def _fail(self, name):
        raise RuntimeError("not found")

    with patch("agent_containers.resolver.ContainerResolver.ensure_ready", _fail):
        assert main(["namespace-ensure-ready", "example-web-1"]) == 1


def test_session_host_prepare_returns_only_env_backed_launch_data(capsys):
    config = types.SimpleNamespace(
        relay_port=9857,
        credentials_for=lambda fleet: (True, True),
        acp_command_for=lambda fleet: "copilot --acp --stdio",
    )
    fleet = types.SimpleNamespace(security_profile="trusted")
    ssh = SSHConfig(
        host_alias="agent-container-example",
        user="vscode",
        identity_file="/keys/id_ed25519",
        config_file="/ssh/example.config",
    )
    with (
        patch(
            "agent_containers.__main__._trusted_session_host_context",
            return_value=(config, fleet, "vscode", "/workspaces/example"),
        ),
        patch("agent_containers.__main__.prepare_ssh_config", return_value=ssh),
        patch("agent_containers.__main__.cleanup_remote_envs"),
        patch(
            "agent_containers.__main__.container_environment",
            return_value={"PATH": "/usr/bin"},
        ),
        patch("agent_containers.__main__.host_gh_token", return_value="github-secret"),
        patch("agent_containers.__main__._relay_healthy", return_value=True),
        patch(
            "agent_containers.__main__.write_remote_env",
            return_value="/tmp/agent-containers/env-123",
        ) as write_env,
        patch(
            "agent_containers.__main__.build_remote_command",
            return_value="source /tmp/agent-containers/env-123 && exec copilot",
        ),
        patch("agent_containers.container_shims.deploy") as deploy,
        patch("agent_containers.relay_provider.token_for", return_value="relay-secret"),
    ):
        rc = main([
            "session-host-prepare",
            "example-web-1",
            "--host-relay-port",
            "61234",
        ])

    assert rc == 0
    result = json.loads(capsys.readouterr().out)
    assert result["reverse_forwards"] == ["9857:127.0.0.1:61234"]
    assert result["remote_command"].startswith("source /tmp/")
    assert result["acp_command"] == "copilot --acp --stdio"
    assert "github-secret" not in json.dumps(result)
    assert "relay-secret" not in json.dumps(result)
    staged = write_env.call_args.args[2]
    assert staged["GH_TOKEN"] == "github-secret"
    assert staged["LC_GIT_CREDENTIAL_RELAY_TOKEN"] == "relay-secret"
    assert staged["GIT_CONFIG_COUNT"] == "2"
    assert staged["GIT_CONFIG_VALUE_1"] == "/usr/local/bin/ado-auth-helper"
    assert staged["GIT_TERMINAL_PROMPT"] == "0"
    deploy.assert_called_once_with("example-web-1", ado=True)


def test_session_host_state_is_non_waking(capsys):
    with (
        patch(
            "agent_containers.lifecycle.inspect_container",
            return_value={
                "Id": "abc123",
                "State": {"Status": "running", "StartedAt": "now"},
            },
        ),
    ):
        assert main(["session-host-state", "example-web-1"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "name": "example-web-1",
        "state": "running",
        "running": True,
        "container_id": "abc123",
        "started_at": "now",
    }


@pytest.mark.parametrize("live_profile", [None, "unexpected", "restricted"])
def test_session_host_context_requires_exact_trusted_live_profile(
    monkeypatch, live_profile
):
    from agent_containers import __main__ as cli
    from agent_containers.config import ContainersConfig, FleetConfig
    import agent_containers.lifecycle as lifecycle

    config = ContainersConfig()
    config.fleets["repo"] = FleetConfig(security_profile="trusted")
    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(
        lifecycle,
        "get_container",
        lambda cfg, name: types.SimpleNamespace(
            fleet="repo",
            state="running",
        ),
    )
    labels = {}
    if live_profile is not None:
        labels["agent-containers.security-profile"] = live_profile
    monkeypatch.setattr(
        lifecycle,
        "inspect_container",
        lambda name: {"Config": {"Labels": labels}},
    )

    with pytest.raises(RuntimeError, match="exact trusted/trusted posture"):
        cli._trusted_session_host_context("repo-1")

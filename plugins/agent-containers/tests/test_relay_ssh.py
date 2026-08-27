"""Trusted-container credential relay over SSH reverse forwarding."""

from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace

import pytest
import ssh_manager
from ssh_manager import LockHolder, TargetBusyError

from agent_containers import __main__ as cli
from agent_containers.config import ContainersConfig, FleetConfig


def _args() -> Namespace:
    return Namespace(name="repo-1", stdio=False, force=False)


def _config() -> tuple[ContainersConfig, FleetConfig]:
    config = ContainersConfig(
        forward_gh_token=False,
        relay_enabled=True,
        relay_port=9857,
    )
    fleet = FleetConfig(
        repo="example/repo",
        workspace_folder="/workspaces/repo",
        exec_user="vscode",
    )
    return config, fleet


def _patch_cmd_context(monkeypatch, *, profile: str = "trusted"):
    import agent_containers.lifecycle as lifecycle

    config, fleet = _config()
    fleet.security_profile = profile
    if profile == "restricted":
        fleet.acp_command = "minimal-agent --stdio"
    config.fleets["repo"] = fleet
    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(
        lifecycle,
        "get_container",
        lambda cfg, name: SimpleNamespace(fleet="repo"),
    )
    monkeypatch.setattr(
        lifecycle,
        "inspect_container",
        lambda name: {
            "Config": {
                "Labels": {"agent-containers.security-profile": profile},
            },
        },
    )
    monkeypatch.setattr(lifecycle, "restricted_policy_errors", lambda *a, **k: [])
    monkeypatch.setattr(cli, "_launch_container_agent", lambda *args: 0)


def test_trusted_exec_holds_namespaced_target_lock(monkeypatch):
    _patch_cmd_context(monkeypatch)
    seen = {}

    class FakeLock:
        def __init__(self, target, *, op):
            seen.update(target=target, op=op)

        def acquire(self, *, force=False):
            seen["force"] = force

        def release(self):
            seen["released"] = True

    monkeypatch.setattr(ssh_manager, "TargetLock", FakeLock)
    args = _args()
    args.stdio = True
    args.force = True

    assert cli._cmd_exec(args) == 0
    assert seen == {
        "target": "container:repo-1",
        "op": "stdio",
        "force": True,
        "released": True,
    }


def test_trusted_exec_returns_busy_exit(monkeypatch, capsys):
    _patch_cmd_context(monkeypatch)

    class BusyLock:
        def __init__(self, target, *, op):
            self.target = target

        def acquire(self, *, force=False):
            raise TargetBusyError(
                self.target,
                LockHolder(
                    pid=123,
                    op="stdio",
                    target=self.target,
                    started_at=0,
                ),
            )

        def release(self):
            pytest.fail("unacquired lock must not release")

    monkeypatch.setattr(ssh_manager, "TargetLock", BusyLock)
    assert cli._cmd_exec(_args()) == 75
    assert "[BUSY]" in capsys.readouterr().err


def test_restricted_exec_skips_ssh_lock(monkeypatch):
    _patch_cmd_context(monkeypatch, profile="restricted")
    monkeypatch.setattr(
        ssh_manager,
        "TargetLock",
        lambda *args, **kwargs: pytest.fail("restricted path must not lock SSH"),
    )
    assert cli._cmd_exec(_args()) == 0


@pytest.mark.parametrize("live_profile", [None, "unexpected"])
def test_exec_rejects_unknown_live_profile_before_launch(monkeypatch, live_profile):
    import agent_containers.lifecycle as lifecycle

    config, fleet = _config()
    config.fleets["repo"] = fleet
    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(
        lifecycle,
        "get_container",
        lambda cfg, name: SimpleNamespace(fleet="repo"),
    )
    labels = {}
    if live_profile is not None:
        labels["agent-containers.security-profile"] = live_profile
    monkeypatch.setattr(
        lifecycle,
        "inspect_container",
        lambda name: {"Config": {"Labels": labels}},
    )
    monkeypatch.setattr(
        cli,
        "_launch_container_agent",
        lambda *args: pytest.fail("unknown profile must not launch"),
    )

    with pytest.raises(RuntimeError, match="unsupported live security profile"):
        cli._cmd_exec(_args())


def test_exec_rejects_configured_observed_profile_mismatch(monkeypatch):
    import agent_containers.lifecycle as lifecycle

    config, fleet = _config()
    config.fleets["repo"] = fleet
    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(
        lifecycle,
        "get_container",
        lambda cfg, name: SimpleNamespace(fleet="repo"),
    )
    monkeypatch.setattr(
        lifecycle,
        "inspect_container",
        lambda name: {
            "Config": {
                "Labels": {"agent-containers.security-profile": "restricted"},
            },
        },
    )
    monkeypatch.setattr(
        cli,
        "_launch_container_agent",
        lambda *args: pytest.fail("profile mismatch must not launch"),
    )

    with pytest.raises(RuntimeError, match="does not match its fleet"):
        cli._cmd_exec(_args())


def test_restricted_launch_never_enters_credential_or_relay_paths(monkeypatch):
    config, fleet = _config()
    fleet.security_profile = "restricted"
    fleet.acp_command = "minimal-agent --stdio"
    seen = {}

    monkeypatch.setattr(
        config,
        "credentials_for",
        lambda fleet: pytest.fail("restricted launch must not resolve credentials"),
    )
    monkeypatch.setattr(
        cli,
        "host_gh_token",
        lambda: pytest.fail("restricted launch must not fetch a host token"),
    )
    monkeypatch.setattr(
        cli,
        "_require_live_relay_port",
        lambda: pytest.fail("restricted launch must not resolve a relay"),
    )
    def build_restricted(*args):
        seen["spawn"] = list(args)
        return ["docker"]

    def run(command, **kwargs):
        seen["run"] = (command, kwargs["env"])
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(cli, "build_restricted_spawn_command", build_restricted)
    monkeypatch.setattr(cli.subprocess, "run", run)

    assert cli._launch_container_agent(
        _args(),
        config,
        fleet,
        "restricted",
        "agent",
        "minimal-agent --stdio",
    ) == 0
    assert seen["spawn"] == ["repo-1", "agent", "minimal-agent --stdio"]


def test_trusted_launch_uses_loopback_reverse_forward(monkeypatch):
    config, fleet = _config()
    seen = {}

    monkeypatch.setattr(cli, "_require_live_relay_port", lambda: 61234)
    monkeypatch.setattr(cli, "_relay_healthy", lambda port: port == 61234)
    monkeypatch.setattr(
        "agent_containers.container_shims.deploy",
        lambda name, ado=False: seen.update(deployed=(name, ado)),
    )
    monkeypatch.setattr(
        "agent_containers.relay_provider.token_for",
        lambda name: "relay-secret",
    )
    monkeypatch.setattr(cli, "container_environment", lambda *args: {"PATH": "/bin"})

    def write_env(container, user, values):
        seen["launch_env"] = values
        return "/home/vscode/.agent-containers/launch/relay.env"

    monkeypatch.setattr(cli, "write_remote_env", write_env)
    monkeypatch.setattr(cli, "prepare_ssh_config", lambda *args: SimpleNamespace())
    monkeypatch.setattr(cli, "build_remote_command", lambda *args: "remote-command")

    def build_ssh(config, command, *, reverse_forwards=None):
        seen["reverse_forwards"] = reverse_forwards
        return ["ssh", "target", command]

    monkeypatch.setattr(cli, "build_ssh_command", build_ssh)
    monkeypatch.setattr(cli, "cleanup_remote_env", lambda *args: None)
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )

    assert cli._launch_container_agent(
        _args(),
        config,
        fleet,
        "trusted",
        "vscode",
        "copilot --acp --stdio",
    ) == 0

    assert seen["deployed"] == ("repo-1", True)
    assert seen["reverse_forwards"] == [
        "127.0.0.1:9857:127.0.0.1:61234"
    ]
    assert seen["launch_env"]["LC_GIT_CREDENTIAL_RELAY_HOST"] == "127.0.0.1"
    assert seen["launch_env"]["LC_GIT_CREDENTIAL_RELAY"] == "9857"
    assert (
        seen["launch_env"]["LC_GIT_CREDENTIAL_RELAY_TOKEN"] == "relay-secret"  # noqa: S105
    )
    assert seen["launch_env"]["GIT_CONFIG_COUNT"] == "2"
    assert (
        seen["launch_env"]["GIT_CONFIG_VALUE_1"]
        == "/usr/local/bin/ado-auth-helper"
    )
    assert seen["launch_env"]["GIT_TERMINAL_PROMPT"] == "0"


def test_trusted_launch_refuses_missing_host_relay(monkeypatch):
    config, fleet = _config()
    monkeypatch.setattr(cli, "_require_live_relay_port", lambda: 61234)
    monkeypatch.setattr(cli, "_relay_healthy", lambda port: False)
    monkeypatch.setattr(
        "agent_containers.container_shims.deploy",
        lambda *args, **kwargs: pytest.fail("must fail before deployment"),
    )

    with pytest.raises(RuntimeError, match="restart agent-bridge"):
        cli._launch_container_agent(
            _args(),
            config,
            fleet,
            "trusted",
            "vscode",
            "copilot --acp --stdio",
        )

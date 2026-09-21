"""Tests for `agent-containers copilot <name>` (agent-bridge-cli-mode-sessions
Phase 4: the venue counterpart to `agent-worktrees copilot`, PR #3126).

Drives ``copilot_venue.cmd_copilot`` directly with its injected dependencies
faked -- the target resolver, trusted-container SSH transport, `venue_copilot`
orchestration, and the target lock -- so the wiring between them is exercised
end-to-end without a real container.
"""
from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace

import pytest
import ssh_manager

from agent_containers import copilot_venue
from agent_containers.config import ContainersConfig, FleetConfig, RESTRICTED_PROFILE


def _args(**kw) -> Namespace:
    defaults = dict(
        name="repo-1",
        worktree_id="wt-A",
        driver="cli-mode",
        seed=None,
        ttl_seconds=300.0,
        ensure_mux=True,
        no_relay=False,
        force=False,
    )
    defaults.update(kw)
    return Namespace(**defaults)


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


def _target(profile: str = "trusted"):
    config, fleet = _config()
    return SimpleNamespace(
        name="repo-1",
        container_id="instance-123",
        config=config,
        fleet=fleet,
        info=None,
        actual_profile=profile,
        user="vscode",
        workspace_folder="/workspaces/repo",
        acp_command="copilot --acp --stdio",
    )


class _FakeLock:
    instances: list["_FakeLock"] = []

    def __init__(self, target, *, op):
        self.target = target
        self.op = op
        self.released = False
        _FakeLock.instances.append(self)

    def acquire(self, *, force=False):
        self.force = force

    def release(self):
        self.released = True


def _call(
    args,
    *,
    target,
    monkeypatch,
    venue_copilot_run=None,
    daemon_port=9280,
    build_ssh=None,
    subprocess_run=None,
):
    seen: dict = {}

    def default_venue_copilot_run(worktree_id, *, connect, **kwargs):
        return connect("agent-worktrees copilot --worktree-id wt-A")

    import venue_copilot

    monkeypatch.setattr(
        venue_copilot, "run_venue_copilot",
        venue_copilot_run or default_venue_copilot_run,
    )
    monkeypatch.setattr(venue_copilot, "resolve_daemon_port", lambda: daemon_port)

    def default_build_ssh(config, command, *, reverse_forwards=None, pty=False):
        seen["reverse_forwards"] = reverse_forwards
        seen["pty"] = pty
        return ["ssh", "target", command]

    monkeypatch.setattr(ssh_manager, "TargetLock", _FakeLock)
    _FakeLock.instances.clear()

    import agent_containers.resolver as resolver_mod
    import agent_containers.config as config_mod
    import agent_containers.ssh_transport as ssh_transport_mod

    monkeypatch.setattr(
        resolver_mod, "resolve_live_exec_target",
        lambda name, config=None: target,
    )
    monkeypatch.setattr(config_mod, "load_config", lambda: target.config)
    monkeypatch.setattr(
        ssh_transport_mod, "prepare_ssh_config", lambda *a: SimpleNamespace(),
    )
    monkeypatch.setattr(
        ssh_transport_mod, "build_ssh_command", build_ssh or default_build_ssh,
    )
    monkeypatch.setattr(
        copilot_venue.subprocess, "run",
        subprocess_run or (lambda spawn_cmd, **kw: SimpleNamespace(returncode=0)),
    )

    rc = copilot_venue.cmd_copilot(
        args,
        require_live_relay_port=lambda: 61234,
        relay_healthy=lambda port: port == 61234,
        busy_exit=75,
        creation_flags=lambda: 0,
    )
    return rc, seen


class TestCmdCopilotRestrictedRefusal:
    def test_refuses_restricted_container(self, monkeypatch, capsys) -> None:
        rc, _ = _call(
            _args(), target=_target(profile=RESTRICTED_PROFILE), monkeypatch=monkeypatch,
        )
        assert rc == 1
        assert "restricted" in capsys.readouterr().err.lower()


class TestCmdCopilotTrusted:
    def test_reserves_connects_with_pty_and_releases_lock(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "agent_containers.relay_provider.token_for", lambda name: "relay-secret",
        )
        connect_calls: list[str] = []

        def venue_copilot_run(worktree_id, *, connect, **kwargs):
            assert worktree_id == "wt-A"
            connect_calls.append("called")
            return connect("agent-worktrees copilot --worktree-id wt-A")

        rc, seen = _call(
            _args(), target=_target(), venue_copilot_run=venue_copilot_run,
            monkeypatch=monkeypatch,
        )

        assert rc == 0
        assert connect_calls == ["called"]
        assert seen["pty"] is True
        assert seen["reverse_forwards"] == [
            "127.0.0.1:9857:127.0.0.1:61234",
            "9280:127.0.0.1:9280",
        ]
        assert _FakeLock.instances[0].released is True

    def test_no_relay_skips_relay_forward_but_keeps_daemon_forward(
        self, monkeypatch,
    ) -> None:
        rc, seen = _call(_args(no_relay=True), target=_target(), monkeypatch=monkeypatch)
        assert rc == 0
        assert seen["reverse_forwards"] == ["9280:127.0.0.1:9280"]

    def test_lock_released_even_when_connect_raises(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "agent_containers.relay_provider.token_for", lambda name: "relay-secret",
        )

        def raising_run(spawn_cmd, **kwargs):
            raise RuntimeError("ssh dropped")

        with pytest.raises(RuntimeError, match="ssh dropped"):
            _call(
                _args(), target=_target(), monkeypatch=monkeypatch,
                subprocess_run=raising_run,
            )

        assert _FakeLock.instances[0].released is True

    def test_venue_copilot_error_is_reported_and_lock_released(
        self, monkeypatch, capsys,
    ) -> None:
        from venue_copilot import VenueCopilotError

        monkeypatch.setattr(
            "agent_containers.relay_provider.token_for", lambda name: "relay-secret",
        )

        def raising_venue_copilot_run(worktree_id, *, connect, **kwargs):
            raise VenueCopilotError("already has an active reservation")

        rc, _ = _call(
            _args(), target=_target(), venue_copilot_run=raising_venue_copilot_run,
            monkeypatch=monkeypatch,
        )

        assert rc == 1
        assert "already has an active reservation" in capsys.readouterr().err
        assert _FakeLock.instances[0].released is True

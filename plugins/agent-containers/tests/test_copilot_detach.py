"""Tests for ``agent-containers copilot <name> --detach/--stop``."""

from __future__ import annotations

import argparse
import json
import types

import pytest
import venue_copilot
from ssh_manager import forward_keeper as shared_forward_keeper

from agent_containers import copilot_detach as detach
from agent_containers import forward_keeper
from agent_containers.config import ContainersConfig, FleetConfig, RESTRICTED_PROFILE


def _args(**kw):
    base = dict(
        name="repo-1",
        worktree_id=None,
        driver="orchestrator",
        seed="do the task",
        seed_file=None,
        copilot_args=["--no-ask-user"],
        register_timeout=0.0,
        ensure_mux=True,
        no_relay=False,
        force=False,
        detach=True,
        stop=False,
        dry_run=False,
    )
    base.update(kw)
    return argparse.Namespace(**base)


def _target(profile: str = "trusted"):
    config = ContainersConfig(forward_gh_token=False, relay_enabled=True, relay_port=9857)
    fleet = FleetConfig(repo="example/repo", workspace_folder="/workspaces/repo", exec_user="vscode")
    return types.SimpleNamespace(
        name="repo-1",
        config=config,
        fleet=fleet,
        actual_profile=profile,
        user="vscode",
        workspace_folder="/workspaces/repo",
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


@pytest.fixture
def seams(monkeypatch):
    calls = types.SimpleNamespace(
        run=[],
        reserve=[],
        release=[],
        keeper=[],
        stop_keeper=[],
        cleaned=[],
        deregister=[],
        remote_env=[],
    )
    target = _target()
    import agent_containers.config as config_mod
    import agent_containers.resolver as resolver_mod
    import agent_containers.ssh_transport as ssh_transport
    import ssh_manager

    monkeypatch.setattr(config_mod, "load_config", lambda: target.config)
    monkeypatch.setattr(resolver_mod, "resolve_live_exec_target", lambda name, config=None: target)
    monkeypatch.setattr(ssh_manager, "TargetLock", _FakeLock)
    _FakeLock.instances.clear()
    monkeypatch.setattr(venue_copilot, "resolve_daemon_port", lambda: 41234)
    monkeypatch.setattr(venue_copilot, "resolve_local_auth_token", lambda: "tok")
    monkeypatch.setattr(
        venue_copilot,
        "reserve_cli_mode",
        lambda scope, ttl_seconds, venue: calls.reserve.append((scope, venue)) or {"reservation_id": "r1"},
    )
    monkeypatch.setattr(
        venue_copilot,
        "await_claim",
        lambda scope, rid, timeout: "sid-42",
    )
    monkeypatch.setattr(
        venue_copilot,
        "release_cli_mode",
        lambda scope, reservation_id=None: calls.release.append((scope, reservation_id)) or 1,
    )
    monkeypatch.setattr(venue_copilot, "live_session_for", lambda handle: {
        "session_id": "sid-42",
        "venue": {"target": "repo-1"},
    })
    monkeypatch.setattr(
        venue_copilot,
        "deregister_live_session",
        lambda sid: calls.deregister.append(sid) or True,
    )
    monkeypatch.setattr(ssh_transport, "prepare_ssh_config", lambda name, user: types.SimpleNamespace())
    monkeypatch.setattr(ssh_transport, "build_ssh_command", lambda cfg, cmd, **kw: ["ssh", cmd])
    monkeypatch.setattr(ssh_transport, "container_environment", lambda name, user: {"PATH": "/bin"})
    monkeypatch.setattr(
        ssh_transport,
        "write_remote_env",
        lambda name, user, values: calls.remote_env.append(values) or "/tmp/env",
    )
    monkeypatch.setattr(
        ssh_transport,
        "cleanup_remote_env",
        lambda name, user, path: calls.cleaned.append(path),
    )
    monkeypatch.setattr("agent_containers.container_shims.deploy", lambda *a, **k: None)
    monkeypatch.setattr("agent_containers.container_shims.git_credential_environment", lambda: {
        "GIT_TERMINAL_PROMPT": "0",
    })
    monkeypatch.setattr("agent_containers.relay_provider.token_for", lambda name: "relay-token")
    monkeypatch.setattr(
        forward_keeper,
        "ensure_running",
        lambda *a, **k: calls.keeper.append((a, k)) or {"started": True, "state": {"pid": 123}},
    )
    monkeypatch.setattr(
        forward_keeper,
        "stop_keeper",
        lambda name: calls.stop_keeper.append(name) or True,
    )
    monkeypatch.setattr(forward_keeper, "read_state", lambda name: None)
    created = json.dumps({
        "ok": True,
        "created": True,
        "resumed": False,
        "seed_submitted": True,
        "session": "wt-anchor-repo",
    })

    def fake_run(argv, **kwargs):
        command = argv[-1]
        calls.run.append(command)
        if "curl -fsS" in command:
            return types.SimpleNamespace(returncode=0, stdout="", stderr="")
        if "agent-worktrees embody" in command:
            return types.SimpleNamespace(returncode=0, stdout=created, stderr="")
        if "tmux kill-session" in command:
            return types.SimpleNamespace(returncode=0, stdout="STOPPED\n", stderr="")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(detach.subprocess, "run", fake_run)
    return calls


def test_detach_success_provisions_credentials_starts_keeper_and_reports_handle(seams, capsys):
    rc = detach.cmd_detach(
        _args(),
        require_live_relay_port=lambda: 61234,
        relay_healthy=lambda port: port == 61234,
    )

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["session_id"] == "sid-42"
    assert out["scope_id"] == "anchor-repo@repo-1"
    assert out["venue"] == {
        "kind": "container",
        "target": "repo-1",
        "mux_session_name": "wt-anchor-repo",
    }
    assert out["commands"]["attach"] == "agent-containers copilot repo-1"
    assert out["commands"]["stop"] == "agent-containers copilot repo-1 --stop"
    assert seams.reserve == [("anchor-repo@repo-1", out["venue"])]
    assert seams.keeper[0][1] == {
        "venue_port": 41234,
        "mux": "wt-anchor-repo",
        "relay_port": 9857,
        "host_relay_port": 61234,
    }
    assert "auth.yaml" in seams.run[0] and "active.json" in seams.run[0]
    launch = next(cmd for cmd in seams.run if "agent-worktrees embody" in cmd)
    assert "cd /workspaces/repo" in launch
    assert "--bridge-scope-id anchor-repo@repo-1" in launch
    assert "--copilot-arg=--no-ask-user" in launch
    assert "--json" in launch
    assert seams.release == [("anchor-repo@repo-1", "r1")]
    assert _FakeLock.instances[0].released is True


def test_restricted_container_is_refused(monkeypatch, capsys):
    import agent_containers.config as config_mod
    import agent_containers.resolver as resolver_mod

    target = _target(RESTRICTED_PROFILE)
    monkeypatch.setattr(config_mod, "load_config", lambda: target.config)
    monkeypatch.setattr(resolver_mod, "resolve_live_exec_target", lambda name, config=None: target)
    rc = detach.cmd_detach(_args(), require_live_relay_port=lambda: 1, relay_healthy=lambda p: True)
    assert rc == 1
    assert "restricted" in capsys.readouterr().err


def test_missing_daemon_port_fails_before_keeper(seams, monkeypatch):
    monkeypatch.setattr(venue_copilot, "resolve_daemon_port", lambda: None)
    rc = detach.cmd_detach(_args(), require_live_relay_port=lambda: 1, relay_healthy=lambda p: True)
    assert rc == 1
    assert seams.keeper == []


def test_launch_failure_stops_started_keeper_and_kills_created_mux(seams, monkeypatch):
    unseeded = json.dumps({"ok": True, "created": True, "seed_submitted": False})

    def fake_run(argv, **kwargs):
        cmd = argv[-1]
        seams.run.append(cmd)
        if "agent-worktrees embody" in cmd:
            return types.SimpleNamespace(returncode=0, stdout=unseeded, stderr="")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(detach.subprocess, "run", fake_run)
    rc = detach.cmd_detach(_args(), require_live_relay_port=lambda: 61234, relay_healthy=lambda p: True)
    assert rc == 1
    assert "repo-1" in seams.stop_keeper
    assert any("tmux kill-session" in cmd for cmd in seams.run)


def test_rejoin_does_not_start_a_second_keeper(seams, monkeypatch, capsys):
    monkeypatch.setattr(
        forward_keeper,
        "ensure_running",
        lambda *a, **k: {"started": False, "state": {"pid": 123}},
    )
    resumed = json.dumps({"ok": True, "created": False, "resumed": True})

    def fake_run(argv, **kwargs):
        cmd = argv[-1]
        seams.run.append(cmd)
        if "agent-worktrees embody" in cmd:
            return types.SimpleNamespace(returncode=0, stdout=resumed, stderr="")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(detach.subprocess, "run", fake_run)
    rc = detach.cmd_detach(_args(), require_live_relay_port=lambda: 61234, relay_healthy=lambda p: True)
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["resumed"] is True
    assert seams.stop_keeper == []


def test_stop_kills_mux_stops_keeper_and_deregisters(seams, capsys):
    rc = detach.cmd_stop(_args(stop=True, detach=False))
    assert rc == 0
    assert any("tmux kill-session" in cmd for cmd in seams.run)
    assert seams.stop_keeper == ["repo-1"]
    assert seams.release == [("anchor-repo@repo-1", None)]
    assert seams.deregister == ["sid-42"]
    assert json.loads(capsys.readouterr().out)["deregistered"] == "sid-42"


def test_forward_keeper_state_reuse_and_replace(tmp_path, monkeypatch):
    monkeypatch.setattr(forward_keeper, "_STORE", shared_forward_keeper.KeeperStore(tmp_path))
    monkeypatch.setattr(shared_forward_keeper, "pid_alive", lambda pid: pid == 100)
    stopped = []
    monkeypatch.setattr(forward_keeper, "stop_keeper", lambda name: stopped.append(name) or True)

    class Proc:
        pid = 200

    forward_keeper._STORE.write("repo-1", {
        "pid": 100,
        "mux": "wt-anchor-repo",
        "venue_port": 41234,
    })
    assert forward_keeper.ensure_running("repo-1", venue_port=41234, mux="wt-anchor-repo")["started"] is False
    out = forward_keeper.ensure_running(
        "repo-1",
        venue_port=41235,
        mux="wt-anchor-other",
        popen=lambda *a, **k: Proc(),
    )
    assert out["started"] is True and stopped == ["repo-1"]
    assert forward_keeper.read_state("repo-1")["pid"] == 200


def test_forward_keeper_exits_when_mux_is_gone(tmp_path, monkeypatch):
    monkeypatch.setattr(forward_keeper, "_STORE", shared_forward_keeper.KeeperStore(tmp_path))
    monkeypatch.setattr("agent_containers.resolver.resolve_live_exec_target", lambda name: _target())
    monkeypatch.setattr("agent_containers.ssh_transport.prepare_ssh_config", lambda name, user: object())
    monkeypatch.setattr(forward_keeper, "_mux_exists", lambda cfg, mux: False)

    class Fwd:
        started = 0
        stopped = 0

        def __init__(self, *a, **k):
            pass

        async def start(self):
            Fwd.started += 1

        async def stop(self):
            Fwd.stopped += 1

    monkeypatch.setattr(forward_keeper, "SupervisedRelayForward", Fwd)
    rc = forward_keeper.cmd_forward_keeper(argparse.Namespace(
        name="repo-1",
        venue_port=41234,
        mux="wt-anchor-repo",
        relay_port=None,
        host_relay_port=None,
        probe_interval=1,
        startup_grace=0,
    ))
    assert rc == 0
    assert Fwd.started == 1 and Fwd.stopped == 1
    assert forward_keeper.read_state("repo-1") is None

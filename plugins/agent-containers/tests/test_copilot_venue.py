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
import subprocess

import pytest
from tests._hosting_owner import policy_reply


@pytest.fixture(autouse=True)
def _stub_plugin_preparation(monkeypatch):
    def stage(args, target, *, prepared_context=None, **kwargs):
        if prepared_context is not None:
            prepared_context["workspace"] = target.workspace_folder
        return ""
    monkeypatch.setattr("agent_containers.plugin_preparation.stage_for_attached_launch", stage)
    monkeypatch.setattr("agent_containers.container_shims.deploy", lambda *a, **kw: None)
    monkeypatch.setattr("agent_containers.container_shims.git_credential_environment", lambda: {})
    monkeypatch.setattr("agent_containers.copilot_process_auth.stage_env", lambda *a, **kw: None)
    monkeypatch.setattr("agent_containers.ssh_transport.container_environment",
                        lambda *a, **kw: {"PATH": "/usr/bin"})
import ssh_manager

from agent_containers import copilot_venue
from agent_containers.config import ContainersConfig, FleetConfig, RESTRICTED_PROFILE


@pytest.mark.parametrize("detach", [False, True])
def test_native_cli_parser_keeps_disabled_plugin_selections(detach):
    import argparse

    parser = argparse.ArgumentParser()
    copilot_venue.add_copilot_subparser(parser.add_subparsers())
    args = parser.parse_args([
        "copilot", "example", "--stage-plugin-disabled", "sample@synthetic",
        "--stage-plugin", "/plain source=false", *(["--detach"] if detach else []),
    ])
    assert args.stage_plugins == [
        {"source": "sample@synthetic", "enable": False}, "/plain source=false",
    ]
    assert args.detach is detach


def _args(**kw) -> Namespace:
    defaults = dict(
        name="repo-1",
        worktree_id="wt-A",
        driver="cli-mode",
        seed=None,
        ttl_seconds=None,
        ensure_mux=True,
        no_relay=False,
        force=False,
    )
    defaults.update(kw)
    return Namespace(**defaults)


def _config() -> tuple[ContainersConfig, FleetConfig]:
    config = ContainersConfig(
        copilot_auth_mode="legacy",
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
    def policy_run(argv, **kwargs):
        seen.setdefault("admissions", []).append(argv)
        return policy_reply(
            argv, container_id=target.container_id, workspace=target.workspace_folder,
        )
    monkeypatch.setattr("agent_containers.hosting_policy.subprocess", SimpleNamespace(
        run=policy_run, SubprocessError=subprocess.SubprocessError,
    ))

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


@pytest.mark.parametrize("child_alive", [False, True])
@pytest.mark.parametrize("failure", [None, "copy", "install"])
def test_attached_live_ledger_requires_preparation_before_remote_decision(
    monkeypatch, child_alive, failure,
):
    events = []
    monkeypatch.setattr("venue_copilot.live_session_for", lambda *a: {"status": "live"})

    def stage(args, target, **kwargs):
        events.append("stage")
        assert args.stage_plugins == [{"source": "sample@synthetic", "enable": False}]
        kwargs["prepared_context"]["workspace"] = target.workspace_folder
        return "REQUIRED_PLUGIN_PREPARATION && "

    def run(argv, **kwargs):
        assert argv[-1].startswith("REQUIRED_PLUGIN_PREPARATION && ")
        events.append("prepare")
        if not failure:
            events.append("reuse" if child_alive else "create")
        return SimpleNamespace(returncode=1 if failure else 0)

    monkeypatch.setattr("agent_containers.plugin_preparation.stage_for_attached_launch", stage)
    rc, _ = _call(
        _args(stage_plugins=[{"source": "sample@synthetic", "enable": False}]),
        target=_target(), monkeypatch=monkeypatch, subprocess_run=run,
    )
    assert rc == (1 if failure else 0)
    assert events == (["stage", "prepare"] if failure else [
        "stage", "prepare", "reuse" if child_alive else "create",
    ])


@pytest.mark.guard
@pytest.mark.parametrize("receipt", ["missing", "resumed", "created"])
def test_attached_failed_connection_uses_receipt_before_cleanup(monkeypatch, receipt):
    from agent_containers import copilot_process_auth as auth
    from agent_containers import ssh_transport

    target = _target()
    cleaned = []
    consumed = []
    monkeypatch.setattr(auth, "prepare_launch", lambda *a, **k: "/launch/auth.env")
    monkeypatch.setattr(auth, "stage_env", lambda *a, **k: "/launch/general.env")
    monkeypatch.setattr(ssh_transport, "container_environment", lambda *a: {"PATH": "/bin"})
    monkeypatch.setattr(ssh_transport, "write_remote_env", lambda *a: "/launch/general.env")
    monkeypatch.setattr(ssh_transport, "cleanup_remote_env", lambda *a: cleaned.append(a[-1]))

    def created(*args):
        if receipt == "missing":
            raise RuntimeError("missing launch receipt")
        return receipt == "created"

    monkeypatch.setattr(auth, "attached_created_child", created)
    monkeypatch.setattr(auth, "wait_for_consumption", lambda *a: consumed.append(a[-1]))
    kwargs = dict(
        target=target, monkeypatch=monkeypatch,
        subprocess_run=lambda *a, **k: SimpleNamespace(returncode=255),
    )
    if receipt == "missing":
        with pytest.raises(RuntimeError, match="missing launch receipt"):
            _call(_args(no_relay=True), **kwargs)
        assert cleaned == []
    else:
        assert _call(_args(no_relay=True), **kwargs)[0] == 255
        assert cleaned == ["/launch/auth.env", "/launch/general.env"]
    assert bool(consumed) is (receipt == "created")


@pytest.mark.guard
def test_attached_existing_session_cleans_unused_auth_without_waiting(monkeypatch):
    from agent_containers import copilot_process_auth as auth
    from agent_containers import ssh_transport

    target = _target()
    target.fleet.copilot_cli_account = "inference-user"
    cleaned = []
    monkeypatch.setattr(auth, "prepare_launch", lambda *a, **k: "/launch/auth.env")
    monkeypatch.setattr(auth, "attached_created_child", lambda *a: False)
    monkeypatch.setattr(auth, "wait_for_consumption", lambda *a: pytest.fail("existing child"))
    monkeypatch.setattr(ssh_transport, "container_environment", lambda *a: {"PATH": "/bin"})
    monkeypatch.setattr(ssh_transport, "write_remote_env", lambda *a: "/launch/general.env")
    monkeypatch.setattr(ssh_transport, "cleanup_remote_env", lambda *a: cleaned.append(a[-1]))
    assert _call(_args(no_relay=True), target=target, monkeypatch=monkeypatch)[0] == 0
    assert "/launch/auth.env" in cleaned


class TestCmdCopilotAnchorDefault:
    """Omitting --worktree-id defaults to anchor mode: a trusted container is
    conventionally anchor-only (its fleet image already clones the repo
    directly), matching headless ACP dispatch's own existing behavior."""

    def test_no_worktree_id_resolves_anchor_identity_from_workspace_folder(
        self, monkeypatch,
    ) -> None:
        seen = {}

        def venue_copilot_run(identity, *, connect, anchor=False, **kwargs):
            seen["identity"] = identity
            seen["anchor"] = anchor
            return connect("agent-worktrees copilot --anchor")

        rc, _ = _call(
            _args(worktree_id=None), target=_target(),
            venue_copilot_run=venue_copilot_run, monkeypatch=monkeypatch,
        )

        assert rc == 0
        assert seen == {"identity": "anchor-repo", "anchor": True}

    def test_explicit_worktree_id_still_wins_over_anchor_default(
        self, monkeypatch,
    ) -> None:
        seen = {}

        def venue_copilot_run(identity, *, connect, anchor=False, **kwargs):
            seen["identity"] = identity
            seen["anchor"] = anchor
            return connect("agent-worktrees copilot --worktree-id wt-A")

        rc, _ = _call(
            _args(), target=_target(), venue_copilot_run=venue_copilot_run,
            monkeypatch=monkeypatch,
        )

        assert rc == 0
        assert seen == {"identity": "wt-A", "anchor": False}

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
            assert kwargs["ttl_seconds"] == 300.0
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

    def test_explicit_attached_ttl_is_forwarded(self, monkeypatch) -> None:
        seen = {}

        def venue_copilot_run(worktree_id, *, connect, **kwargs):
            seen["ttl_seconds"] = kwargs["ttl_seconds"]
            return connect("agent-worktrees copilot --worktree-id wt-A")

        rc, _ = _call(
            _args(ttl_seconds=45.0),
            target=_target(),
            venue_copilot_run=venue_copilot_run,
            monkeypatch=monkeypatch,
        )

        assert rc == 0
        assert seen == {"ttl_seconds": 45.0}

    @pytest.mark.parametrize("flag", ["detach", "stop"])
    def test_ttl_seconds_is_attached_mode_only(self, monkeypatch, capsys, flag) -> None:
        rc, _ = _call(
            _args(ttl_seconds=45.0, **{flag: True}),
            target=_target(),
            monkeypatch=monkeypatch,
        )

        assert rc == 1
        assert "--ttl-seconds applies only to attached mode" in capsys.readouterr().err

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

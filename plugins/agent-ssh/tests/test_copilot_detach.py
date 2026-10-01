from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest
from ssh_manager import keeper_holds as shared_keeper_holds
from venue_copilot import detached as venue_detached

from agent_ssh import copilot_detach as detach
from agent_ssh.__main__ import main


def _args(**kw):
    base = dict(
        target="devbox",
        workspace="/workspaces/repo",
        seed="do the task",
        seed_file=None,
        copilot_args=["--no-ask-user"],
        driver="orchestrator",
        register_timeout=0.0,
        dry_run=False,
        detach=True,
        stop=False,
        ttl_seconds=None,
    )
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def seams(monkeypatch):
    # Hermetic: never read the developer's own ~/.copilot/settings.json model.
    monkeypatch.setattr(detach, "with_supervisor", lambda venue, ref=None: dict(venue))
    monkeypatch.setattr(detach, "model_copilot_args", lambda existing: [])
    calls = types.SimpleNamespace(
        remote=[],
        reserve=[],
        release=[],
        keeper=[],
        stop_keeper=[],
        deregister=[],
    )
    monkeypatch.setattr(detach, "_ssh_config", lambda target: object())
    monkeypatch.setattr(venue_detached, "resolve_daemon_port", lambda: 41234)
    monkeypatch.setattr(venue_detached, "resolve_local_auth_token", lambda: "tok")
    monkeypatch.setattr(
        venue_detached,
        "reserve_with_retry",
        lambda scope, venue, **kw: calls.reserve.append((scope, venue, kw)) or {"reservation_id": "r1"},
    )
    monkeypatch.setattr(venue_detached, "await_claim", lambda scope, rid, timeout: "sid-42")
    monkeypatch.setattr(
        venue_detached,
        "release_cli_mode",
        lambda scope, reservation_id=None: calls.release.append((scope, reservation_id)) or 1,
    )
    monkeypatch.setattr(
        detach,
        "ensure_keeper",
        lambda *a, **k: calls.keeper.append((a, k))
        or {"started": True, "hold_added": True, "state": {"pid": 123}},
    )
    monkeypatch.setattr(
        detach,
        "stop_keeper",
        lambda target, **kw: calls.stop_keeper.append((target, kw.get("hold_id"))) or True,
    )
    monkeypatch.setattr(detach, "read_keeper_state", lambda target: None)
    monkeypatch.setattr(
        "venue_copilot.live_session_for",
        lambda handle: {"session_id": "sid-42", "venue": {"target": "devbox"}},
    )
    monkeypatch.setattr(
        "venue_copilot.detached.deregister_live_session",
        lambda sid: calls.deregister.append(sid) or True,
    )
    created = json.dumps({
        "ok": True,
        "created": True,
        "resumed": False,
        "seed_submitted": True,
        "session": "wt-anchor-repo",
    })

    def remote(_cfg, command, *, timeout=60.0):
        calls.remote.append(command)
        if "printf posix" in command:
            return 0, "posix", ""
        if "command -v bash" in command:
            return 0, "", ""
        if "curl -fsS" in command:
            return 0, "", ""
        if "agent-worktrees embody" in command:
            return 0, created, ""
        if "tmux kill-session" in command:
            return 0, "STOPPED\n", ""
        return 0, "", ""

    monkeypatch.setattr(detach, "_remote", remote)
    return calls


def test_detach_success_reserves_ssh_venue_and_reports_handle(seams, capsys):
    rc = detach.cmd_detach(_args())

    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["session_id"] == "sid-42"
    assert out["scope_id"] == "anchor-repo@devbox"
    assert out["venue"] == {
        "kind": "ssh",
        "target": "devbox",
        "mux_session_name": "wt-anchor-repo",
    }
    assert out["commands"]["attach"] == "ssh -t devbox tmux attach -t wt-anchor-repo"
    assert "agent-ssh copilot devbox --stop --workspace /workspaces/repo" == out["commands"]["stop"]
    assert seams.reserve[0][0] == "anchor-repo@devbox"
    assert seams.reserve[0][1]["kind"] == "ssh"
    assert seams.keeper[0][1].items() >= {
        "venue_port": 41234,
        "mux": "wt-anchor-repo",
        "hold_id": "anchor-repo@devbox",
    }.items()
    launch = next(command for command in seams.remote if "agent-worktrees embody" in command)
    assert "cd /workspaces/repo" in launch
    assert "--bridge-scope-id anchor-repo@devbox" in launch
    assert "--copilot-arg=--no-ask-user" in launch
    assert "--json" in launch
    assert seams.release == [("anchor-repo@devbox", "r1")]


def test_attached_default_uses_venue_copilot_over_ssh(seams, monkeypatch):
    monkeypatch.setattr(
        detach,
        "_ssh_config",
        lambda target: types.SimpleNamespace(
            config_file=None,
            port=None,
            identity_file=None,
            extra_options={},
            ssh_target=target,
        ),
    )
    monkeypatch.setattr(detach, "_ensure_posix", lambda _cfg: None)
    monkeypatch.setattr(detach, "_ensure_remote_tooling", lambda _cfg: None)
    monkeypatch.setattr(detach, "resolve_daemon_port", lambda: 41234)
    monkeypatch.setattr(detach, "resolve_local_auth_token", lambda: "tok")
    seen = {}

    def fake_run_venue(identity, *, connect, **kwargs):
        seen["identity"] = identity
        seen["kwargs"] = kwargs
        return connect("agent-worktrees copilot --anchor")

    class FakeProcess:
        pid = 999

        def wait(self):
            return 0

    def fake_popen(argv, **kwargs):
        seen["ssh_argv"] = argv
        seen["subprocess_kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(detach, "run_venue_copilot", fake_run_venue)
    monkeypatch.setattr(detach.subprocess, "Popen", fake_popen)

    rc = detach.cmd_attached(
        _args(detach=False, workspace="/workspaces/repo", copilot_args=[]),
    )

    assert rc == 0
    assert seen["identity"] == "anchor-repo"
    assert seen["kwargs"] == {
        "anchor": True,
        "ttl_seconds": 300.0,
        "driver": "orchestrator",
        "seed": "do the task",
        "ensure_mux": True,
    }
    argv = seen["ssh_argv"]
    assert "-t" in argv
    assert "-R" not in argv
    assert seams.keeper[0][1].items() >= {
        "venue_port": 41234,
        "mux": "wt-anchor-repo",
        "hold_pid": os.getpid(),
    }.items()
    assert seams.keeper[0][1]["hold_id"].startswith(f"attached:{os.getpid()}:")
    assert seams.stop_keeper == [("devbox", seams.keeper[0][1]["hold_id"])]
    remote_command = argv[-1]
    assert remote_command.startswith("bash -lc ")
    assert "cd /workspaces/repo" in remote_command
    assert "trustedFolders" in remote_command
    assert "agent-worktrees copilot --anchor" in remote_command
    assert remote_command.index("cd /workspaces/repo") < remote_command.index(
        "agent-worktrees copilot --anchor"
    )
    assert "auth.yaml" not in remote_command
    assert any("auth.yaml" in command for command in seams.remote)


def test_attached_skips_reverse_forward_when_live_keeper_holds_route(seams, monkeypatch):
    monkeypatch.setattr(
        detach,
        "_ssh_config",
        lambda target: types.SimpleNamespace(
            config_file=None,
            port=None,
            identity_file=None,
            extra_options={},
            ssh_target=target,
        ),
    )
    monkeypatch.setattr(detach, "_ensure_posix", lambda _cfg: None)
    monkeypatch.setattr(detach, "_ensure_remote_tooling", lambda _cfg: None)
    monkeypatch.setattr(detach, "resolve_daemon_port", lambda: 41234)
    monkeypatch.setattr(detach, "resolve_local_auth_token", lambda: "tok")
    monkeypatch.setattr(detach, "read_keeper_state", lambda target: {"venue_port": 41234})
    monkeypatch.setattr(detach._STORE, "alive", lambda key: True)
    monkeypatch.setattr(detach, "_update_keeper_hold_pid", lambda *a, **k: None)
    seen = {}

    monkeypatch.setattr(
        detach,
        "run_venue_copilot",
        lambda _identity, *, connect, **_kwargs: connect("agent-worktrees copilot --anchor"),
    )

    class FakeProcess:
        pid = 999

        def wait(self):
            return 0

    def fake_popen(argv, **kwargs):
        seen["ssh_argv"] = argv
        return FakeProcess()

    monkeypatch.setattr(detach.subprocess, "Popen", fake_popen)

    assert detach.cmd_attached(_args(detach=False, workspace="/workspaces/repo", copilot_args=[])) == 0
    assert "-R" not in seen["ssh_argv"]


def test_two_attached_holds_share_keeper_and_first_exit_keeps_route(
    tmp_path: Path,
    monkeypatch,
):
    store = detach.KeeperStore(tmp_path)
    monkeypatch.setattr(detach, "_STORE", store)
    monkeypatch.setattr(store, "alive", lambda key: store.read(key) is not None)
    monkeypatch.setattr(
        detach,
        "process_identity",
        lambda pid: "keeper-id" if pid == 1000 else f"id-{pid}",
    )
    monkeypatch.setattr(
        detach,
        "spawn_keeper",
        lambda _argv, _env, state, **_kwargs: {
            **state,
            "pid": 1000,
            "pid_identity": "keeper-id",
        },
    )

    assert detach.ensure_keeper(
        "devbox",
        venue_port=41234,
        mux="wt-anchor-repo",
        hold_id="attached:first",
        hold_pid=111,
    )["started"] is True
    assert detach.ensure_keeper(
        "devbox",
        venue_port=41234,
        mux="wt-anchor-repo",
        hold_id="attached:second",
        hold_pid=222,
    )["started"] is False

    assert detach.stop_keeper("devbox", hold_id="attached:first") is False
    state = detach.read_keeper_state("devbox")
    assert state is not None
    assert sorted(state["holds"]) == ["attached:second"]
    assert detach._STORE.alive(detach._state_key("devbox")) is True

    detach.stop_keeper("devbox", hold_id="attached:second")
    assert detach.read_keeper_state("devbox") is None


def test_attached_and_detached_holds_share_keeper(
    tmp_path: Path,
    monkeypatch,
):
    store = detach.KeeperStore(tmp_path)
    monkeypatch.setattr(detach, "_STORE", store)
    monkeypatch.setattr(store, "alive", lambda key: store.read(key) is not None)
    monkeypatch.setattr(
        detach,
        "process_identity",
        lambda pid: "keeper-id" if pid == 1000 else f"id-{pid}",
    )
    monkeypatch.setattr(
        detach,
        "spawn_keeper",
        lambda _argv, _env, state, **_kwargs: {
            **state,
            "pid": 1000,
            "pid_identity": "keeper-id",
        },
    )

    detach.ensure_keeper(
        "devbox",
        venue_port=41234,
        mux="wt-anchor-repo",
        hold_id="attached:first",
        hold_pid=111,
    )
    got = detach.ensure_keeper(
        "devbox",
        venue_port=41234,
        mux="wt-anchor-repo",
        hold_id="anchor-repo@devbox",
    )

    assert got["started"] is False
    assert got["hold_added"] is True
    refreshed = detach.ensure_keeper(
        "devbox",
        venue_port=41234,
        mux="wt-anchor-repo",
        hold_id="anchor-repo@devbox",
    )
    assert refreshed["started"] is False
    assert refreshed["hold_added"] is False
    assert sorted(got["state"]["holds"]) == ["anchor-repo@devbox", "attached:first"]
    assert detach.stop_keeper("devbox", hold_id="anchor-repo@devbox") is False
    assert sorted(detach.read_keeper_state("devbox")["holds"]) == ["attached:first"]


def test_stop_uses_detached_hold_mux_when_attached_hold_is_aggregate_mux(
    tmp_path: Path, monkeypatch, capsys,
):
    store = detach.KeeperStore(tmp_path)
    monkeypatch.setattr(detach, "_STORE", store)
    monkeypatch.setattr(detach, "_ssh_config", lambda target: object())
    monkeypatch.setattr(detach, "_ensure_posix", lambda _cfg: None)
    commands = []

    def remote(_cfg, command, *, timeout=60.0):
        commands.append(command)
        if "tmux kill-session" in command:
            return 0, "STOPPED\n", ""
        return 0, "", ""

    monkeypatch.setattr(detach, "_remote", remote)
    monkeypatch.setattr(
        "venue_copilot.live_session_for",
        lambda handle: {"session_id": "sid-42", "venue": {"target": "devbox"}},
    )
    monkeypatch.setattr("venue_copilot.detached.release_cli_mode", lambda *a, **k: 1)
    monkeypatch.setattr("venue_copilot.detached.deregister_live_session", lambda sid: True)
    state = {
        "keeper_protocol": 2,
        "pid": 1000,
        "pid_identity": "keeper-id",
        "target": "devbox",
        "venue_port": 41234,
        "mux": detach._attached_hold_mux(os.getpid()),
        "holds": {
            "attached:first": {
                "mux": detach._attached_hold_mux(os.getpid()),
                "updated_at": 1.0,
            },
            "anchor-repo@devbox": {"mux": "wt-anchor-repo", "updated_at": 2.0},
        },
    }
    store.write("devbox", state)

    assert detach.cmd_stop(_args(stop=True, detach=False)) == 0

    assert any("tmux kill-session -t =wt-anchor-repo" in command for command in commands)
    assert not any("__attached_pid__" in command for command in commands)
    assert json.loads(capsys.readouterr().out)["mux_session"] == "wt-anchor-repo"


def test_stop_hold_mux_error_still_stops_releases_and_deregisters(
    seams, monkeypatch, caplog
):
    monkeypatch.setattr(
        shared_keeper_holds.KeeperHoldStore,
        "hold_mux",
        lambda *a, **k: (_ for _ in ()).throw(PermissionError("pending delete")),
    )
    caplog.set_level("WARNING", logger="ssh-manager.keeper_holds")

    rc = detach.cmd_stop(_args(stop=True, detach=False))

    assert rc == 0
    assert "Could not read forward-keeper hold for devbox/anchor-repo@devbox" in caplog.text
    assert any("tmux kill-session" in command for command in seams.remote)
    assert seams.release == [("anchor-repo@devbox", None)]
    assert seams.deregister == ["sid-42"]


def test_stop_keeper_lock_timeout_still_releases_and_deregisters(
    tmp_path: Path, monkeypatch, capsys
):
    store = detach.KeeperStore(tmp_path)
    monkeypatch.setattr(detach, "_STORE", store)
    monkeypatch.setattr(
        detach,
        "_holds",
        lambda: shared_keeper_holds.KeeperHoldStore(
            store,
            protocol=detach._KEEPER_PROTOCOL,
            lock_timeout=0.05,
            lock_poll=0.01,
        ),
    )
    monkeypatch.setattr(detach, "_ssh_config", lambda target: object())
    monkeypatch.setattr(detach, "_ensure_posix", lambda _cfg: None)
    calls = types.SimpleNamespace(remote=[], release=[], deregister=[])
    store.write(
        "devbox",
        {
            "keeper_protocol": 2,
            "pid": 1000,
            "target": "devbox",
            "venue_port": 41234,
            "holds": {
                "anchor-repo@devbox": {"mux": "wt-anchor-repo", "updated_at": 1.0},
            },
        },
    )

    def remote(_cfg, command, *, timeout=60.0):
        calls.remote.append(command)
        if "tmux kill-session" in command:
            return 0, "STOPPED\n", ""
        return 0, "", ""

    monkeypatch.setattr(detach, "_remote", remote)
    monkeypatch.setattr(
        "venue_copilot.live_session_for",
        lambda handle: {"session_id": "sid-42", "venue": {"target": "devbox"}},
    )
    monkeypatch.setattr(
        "venue_copilot.detached.release_cli_mode",
        lambda scope, reservation_id=None: calls.release.append((scope, reservation_id)) or 1,
    )
    monkeypatch.setattr(
        "venue_copilot.detached.deregister_live_session",
        lambda sid: calls.deregister.append(sid) or True,
    )
    holder = tmp_path / "hold_keeper_lock.py"
    holder.write_text(
        textwrap.dedent(
            """\
            import sys
            import time
            from pathlib import Path
            from ssh_manager.forward_keeper import KeeperStore
            from ssh_manager.keeper_holds import KeeperHoldStore

            holds = KeeperHoldStore(KeeperStore(Path(sys.argv[1])), lock_timeout=1.0, lock_poll=0.01)
            with holds.lock("devbox"):
                print("READY", flush=True)
                time.sleep(60)
            """
        ),
        encoding="utf-8",
    )
    proc = subprocess.Popen(
        [sys.executable, str(holder), str(tmp_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "READY"

        assert detach.cmd_stop(_args(stop=True, detach=False)) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)

    captured = capsys.readouterr()
    assert "[WARN] could not update the forward keeper" in captured.err
    assert any("tmux kill-session" in command for command in calls.remote)
    assert calls.release == [("anchor-repo@devbox", None)]
    assert calls.deregister == ["sid-42"]


def test_workspace_resolution_order_prefers_explicit_then_host_config(tmp_path: Path):
    config = tmp_path / "copilot-hosts.json"
    detach.set_host_workspace("devbox", "/workspaces/configured", config)

    assert detach.resolve_workspace(
        _args(workspace="/workspaces/explicit"),
    ) == "/workspaces/explicit"
    assert (
        detach._workspace_from_host_config("DEVBOX", config)
        == "/workspaces/configured"
    )


def test_copilot_config_set_writes_host_workspace(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(detach, "_copilot_config_path", lambda: tmp_path / "copilot-hosts.json")

    assert main(["copilot-config", "set", "devbox", "--workspace", "/workspaces/repo"]) == 0

    assert detach._workspace_from_host_config("devbox", tmp_path / "copilot-hosts.json") == (
        "/workspaces/repo"
    )


def test_copilot_config_set_canonicalizes_host_alias_casing(tmp_path: Path):
    config = tmp_path / "copilot-hosts.json"

    detach.set_host_workspace("DevBox", "/workspaces/old", config)
    detach.set_host_workspace("devbox", "/workspaces/new", config)

    raw = json.loads(config.read_text(encoding="utf-8"))
    assert raw["hosts"] == {"devbox": {"workspace": "/workspaces/new"}}
    assert detach._workspace_from_host_config("DEVBOX", config) == "/workspaces/new"


def test_copilot_config_set_refuses_corrupt_existing_file(tmp_path: Path):
    config = tmp_path / "copilot-hosts.json"
    config.write_text('{"hosts": {', encoding="utf-8")

    with pytest.raises(detach.CopilotConfigError) as exc:
        detach.set_host_workspace("devbox", "/workspaces/new", config)

    text = str(exc.value)
    assert str(config) in text
    assert "not valid JSON" in text
    assert config.read_text(encoding="utf-8") == '{"hosts": {'


def test_resolve_workspace_reports_corrupt_copilot_config(tmp_path: Path, monkeypatch):
    config = tmp_path / "copilot-hosts.json"
    config.write_text('{"hosts": {', encoding="utf-8")
    monkeypatch.setattr(detach, "_copilot_config_path", lambda: config)

    with pytest.raises(detach.CopilotConfigError) as exc:
        detach.resolve_workspace(_args(workspace=None))

    assert str(config) in str(exc.value)
    assert "not valid JSON" in str(exc.value)


def test_copilot_config_cli_refuses_corrupt_existing_file(tmp_path: Path, monkeypatch, capsys):
    config = tmp_path / "copilot-hosts.json"
    config.write_text('{"hosts": {', encoding="utf-8")
    monkeypatch.setattr(detach, "_copilot_config_path", lambda: config)

    rc = main(["copilot-config", "set", "devbox", "--workspace", "/workspaces/new"])

    assert rc == 2
    err = capsys.readouterr().err
    assert str(config) in err
    assert "not valid JSON" in err
    assert config.read_text(encoding="utf-8") == '{"hosts": {'


def test_missing_workspace_error_names_configuration_options(monkeypatch):
    monkeypatch.setattr(detach, "_workspace_from_host_config", lambda target: None)

    with pytest.raises(ValueError) as exc:
        detach.resolve_workspace(_args(workspace=None))

    text = str(exc.value)
    assert "--workspace /path/to/checkout" in text
    assert "agent-ssh copilot-config set" in text


def test_dry_run_without_workspace_fails_before_remote_ssh(seams, monkeypatch):
    calls = []
    monkeypatch.setattr(detach, "_workspace_from_host_config", lambda target: None)
    monkeypatch.setattr(detach, "_ensure_posix", lambda _cfg: calls.append("posix"))

    rc = detach.cmd_detach(_args(workspace=None, dry_run=True))

    assert rc == 1
    assert calls == []
    assert seams.remote == []


def test_non_dry_run_checks_posix_before_missing_workspace(seams, monkeypatch, capsys):
    monkeypatch.setattr(detach, "_workspace_from_host_config", lambda target: None)
    monkeypatch.setattr(
        detach,
        "_ensure_posix",
        lambda _cfg: (_ for _ in ()).throw(RuntimeError("Windows SSH targets are not supported yet")),
    )

    rc = detach.cmd_detach(_args(workspace=None))

    assert rc == 1
    assert "Windows SSH targets are not supported yet" in capsys.readouterr().err


def test_ttl_seconds_with_detach_is_usage_error(seams, capsys):
    rc = detach.cmd_detach(_args(ttl_seconds=30.0))

    assert rc == 2
    assert "--ttl-seconds applies only to attached mode" in capsys.readouterr().err
    assert seams.reserve == []


def test_non_posix_target_is_refused(seams, monkeypatch, capsys):
    monkeypatch.setattr(detach, "_remote", lambda *a, **k: (1, "", "not powershell syntax"))
    rc = detach.cmd_detach(_args())
    assert rc == 1
    assert "Windows SSH targets are not supported yet" in capsys.readouterr().err
    assert seams.reserve == []


def test_missing_agent_worktrees_message(seams, monkeypatch, capsys):
    def remote(_cfg, command, *, timeout=60.0):
        if "printf posix" in command or "command -v bash" in command or "curl -fsS" in command:
            return 0, "", ""
        if "agent-worktrees embody" in command:
            return 127, "", "agent-worktrees: command not found"
        return 0, "", ""

    monkeypatch.setattr(detach, "_remote", remote)
    rc = detach.cmd_detach(_args())
    assert rc == 1
    assert "agent-worktrees is not installed on the SSH target" in capsys.readouterr().err
    assert seams.stop_keeper == [("devbox", "anchor-repo@devbox")]


def test_launch_failure_cleanup_kills_mux_and_stops_keeper(seams, monkeypatch):
    unseeded = json.dumps({"ok": True, "created": True, "seed_submitted": False})

    def remote(_cfg, command, *, timeout=60.0):
        seams.remote.append(command)
        if "agent-worktrees embody" in command:
            return 0, unseeded, ""
        if "tmux kill-session" in command:
            return 0, "STOPPED\n", ""
        return 0, "", ""

    monkeypatch.setattr(detach, "_remote", remote)
    assert detach.cmd_detach(_args()) == 1
    assert any("tmux kill-session" in command for command in seams.remote)
    assert seams.stop_keeper == [("devbox", "anchor-repo@devbox")]


def test_rejoin_reuses_keeper_and_does_not_stop_it(seams, monkeypatch, capsys):
    monkeypatch.setattr(
        detach,
        "ensure_keeper",
        lambda *a, **k: {"started": False, "state": {"pid": 123}},
    )
    resumed = json.dumps({"ok": True, "created": False, "resumed": True})

    def remote(_cfg, command, *, timeout=60.0):
        if "agent-worktrees embody" in command:
            return 0, resumed, ""
        return 0, "", ""

    monkeypatch.setattr(detach, "_remote", remote)
    assert detach.cmd_detach(_args()) == 0
    assert json.loads(capsys.readouterr().out)["resumed"] is True
    assert seams.stop_keeper == []


def test_rejoin_launch_failure_does_not_release_existing_keeper_hold(seams, monkeypatch):
    monkeypatch.setattr(
        detach,
        "ensure_keeper",
        lambda *a, **k: {"started": False, "state": {"pid": 123}},
    )
    unseeded = json.dumps({"ok": True, "created": True, "seed_submitted": False})

    def remote(_cfg, command, *, timeout=60.0):
        seams.remote.append(command)
        if "agent-worktrees embody" in command:
            return 0, unseeded, ""
        if "tmux kill-session" in command:
            return 0, "STOPPED\n", ""
        return 0, "", ""

    monkeypatch.setattr(detach, "_remote", remote)

    assert detach.cmd_detach(_args()) == 1

    assert any("tmux kill-session" in command for command in seams.remote)
    assert seams.stop_keeper == []


def test_stop_kills_mux_stops_keeper_and_deregisters(seams, capsys):
    rc = detach.cmd_stop(_args(stop=True, detach=False))
    assert rc == 0
    assert any("tmux kill-session" in command for command in seams.remote)
    assert seams.stop_keeper == [("devbox", "anchor-repo@devbox")]
    assert seams.release == [("anchor-repo@devbox", None)]
    assert seams.deregister == ["sid-42"]
    assert json.loads(capsys.readouterr().out)["deregistered"] == "sid-42"


def test_dry_run_names_ref_files_and_a_missing_one_fails(tmp_path, capsys):
    har = tmp_path / "trace.har"
    har.write_text("{}")
    assert detach.cmd_detach(_args(dry_run=True, ref_files=[str(har)])) == 0
    assert json.loads(capsys.readouterr().out)["ref_files"] == ["trace.har"]
    assert detach.cmd_detach(_args(dry_run=True, ref_files=[str(tmp_path / "nope.har")])) == 1
    assert "reference file not found" in capsys.readouterr().err


def test_detached_session_mirrors_the_callers_model(monkeypatch):
    seen = []
    monkeypatch.setattr(detach, "model_copilot_args", lambda existing: seen.append(list(existing)) or ["--model=example-model"])
    assert detach._with_caller_model(["--no-ask-user"]) == ["--no-ask-user", "--model=example-model"]
    assert seen == [["--no-ask-user"]]


def test_forward_keeper_rewrites_state_when_relay_pid_changes(tmp_path, monkeypatch):
    store = detach.KeeperStore(tmp_path)
    monkeypatch.setattr(detach, "_STORE", store)
    monkeypatch.setattr(detach, "_ssh_config", lambda target: object())
    monkeypatch.setenv(detach._KEEPER_TOKEN_ENV, "tok")

    class Forward:
        def __init__(self, *_args, on_pid_change=None, **_kwargs):
            self._pid = None
            self._on_pid_change = on_pid_change

        @property
        def process_pid(self):
            return self._pid

        @property
        def process_birth_identity(self):
            return None if self._pid is None else f"id-{self._pid}"

        async def start(self):
            self._pid = 111
            assert self._on_pid_change is not None
            self._on_pid_change()

        def restart(self):
            self._pid = 222
            assert self._on_pid_change is not None
            self._on_pid_change()

        async def stop(self):
            return None

    async def fake_loop(forwards, **kwargs):
        kwargs["write_state"]()
        await forwards[0].start()
        kwargs["write_state"]()
        forwards[0].restart()
        return 0

    monkeypatch.setattr(detach, "SupervisedRelayForward", Forward)
    monkeypatch.setattr(detach, "run_supervised_loop", fake_loop)

    args = argparse.Namespace(
        target="devbox",
        venue_port=41234,
        mux="wt-anchor-repo",
        probe_interval=15.0,
        startup_grace=300.0,
    )

    assert asyncio.run(detach._run_forward_keeper(args)) == 0
    assert store.read("devbox")["children"] == [{"pid": 222, "identity": "id-222"}]


def test_keeper_hold_store_reads_legacy_single_mux_state(tmp_path, monkeypatch):
    store = detach.KeeperStore(tmp_path)
    monkeypatch.setattr(detach, "_STORE", store)

    store.write(
        "devbox",
        {"pid": 101, "pid_identity": "keep-101", "mux": "wt-legacy", "started_at": 123.0},
    )

    assert detach._holds().read_holds(store.read("devbox")) == {
        "wt-legacy": {"mux": "wt-legacy", "updated_at": 123.0}
    }


def test_keeper_hold_store_uses_sanitized_keeper_state_path(tmp_path, monkeypatch):
    store = detach.KeeperStore(tmp_path)
    monkeypatch.setattr(detach, "_STORE", store)

    path = detach._holds().state_path("codespace:repo/branch").with_suffix(".lock")

    assert path.parent == tmp_path
    assert path.name == "codespace-repo-branch.lock"

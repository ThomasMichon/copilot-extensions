from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import os
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest
from ssh_manager import keeper_holds as shared_keeper_holds
from agent_procutil import no_window_flags
from venue_copilot import detached as venue_detached
from venue_copilot.refs import deliver_note as real_deliver_note

from agent_ssh import copilot_detach as detach
from agent_ssh.copilot_detach import ensure_keeper as real_ensure_keeper, stop_keeper as real_stop_keeper
from agent_ssh.copilot_plugins import resolve_launch as real_resolve_launch
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


@pytest.fixture(autouse=True)
def native_owner(monkeypatch):
    monkeypatch.setattr("agent_ssh.copilot_plugins.resolve_launch", lambda *a: None)


@pytest.fixture
def seams(monkeypatch, tmp_path):
    # Hermetic: never read the developer's own ~/.copilot/settings.json model.
    monkeypatch.setattr(detach, "with_supervisor", lambda venue, ref=None: dict(venue))
    monkeypatch.setattr(detach, "model_copilot_args", lambda existing: [])
    monkeypatch.setattr(detach, "_STORE", detach.KeeperStore(tmp_path / "keepers"))
    context = {"version": 1, "cwd": "/workspaces/repo", "plugins": [], "logical_payloads": {}}
    monkeypatch.setattr("agent_ssh.copilot_plugins.prepare", lambda *a: dict(context))
    monkeypatch.setattr("venue_copilot.refs.deliver_note", lambda *a, **kw: True)
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
        "seed_submitted": False,
        "bridge_seed_deferred": True,
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


def test_owned_refusal_preserves_fixed_reason_without_replay(seams, monkeypatch, capsys):
    attempts = []

    def run(argv, **kwargs):
        attempts.append(argv)
        return types.SimpleNamespace(returncode=69, stdout=json.dumps({
            "owned_delivery_version": 1, "admission_status": "not_admitted",
            "reason": "plugins_not_ready", "error": "private readiness detail",
        }))

    monkeypatch.setattr(
        "venue_copilot.refs.deliver_note",
        lambda sid, note, **kwargs: real_deliver_note(sid, note, run=run, **kwargs),
    )
    assert detach.cmd_detach(_args()) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False and report["refusal_reason"] == "plugins_not_ready"
    assert "plugins_not_ready" in report["error"]
    assert "private readiness detail" not in json.dumps(report)
    assert len(attempts) == 1


def test_streamed_receiver_transport_is_isolated_from_terminal_signals(monkeypatch):
    seen = {}
    monkeypatch.setattr(detach, "build_remote_exec_args", lambda *a, **kw: ["ssh", "synthetic"])
    def run(argv, **kwargs):
        seen.update(kwargs)
        return types.SimpleNamespace(returncode=0, stdout=b"ready", stderr=b"")
    monkeypatch.setattr(detach.subprocess, "run", run)
    assert detach._remote_input(object(), "prepare", b"program", timeout=30) == (0, "ready", "")
    assert seen["start_new_session"] is (os.name != "nt")
    assert seen["creationflags"] == no_window_flags()
    assert seen["input"] == b"program"


def test_stop_checks_explicit_repository_fence_before_termination(seams, monkeypatch):
    monkeypatch.setattr("agent_ssh.copilot_plugins.resolve_launch", real_resolve_launch)
    monkeypatch.setattr("agent_ssh.copilot_plugins.probe_checkout", lambda *a: {
        "cwd": "/workspaces/repo", "root": "/workspaces/repo", "repository": "other/project",
    })
    assert detach.cmd_stop(_args(stop=True, detach=False, expected_repository="example/project")) == 1
    assert seams.release == [] and seams.deregister == [] and seams.stop_keeper == []
    assert not any("tmux kill-session" in command for command in seams.remote)
    assert not any("agent-worktrees embody" in command for command in seams.remote)


def test_reservation_covers_preparation_and_supported_native_launch_budgets(seams, monkeypatch, capsys):
    clock = [1000.0]
    def prepare(*a):
        clock[0] += 750.0  # Probe + preparation + both transport grace periods.
        return {"version": 1, "cwd": "/workspaces/repo", "plugins": [], "logical_payloads": {}}
    monkeypatch.setattr("agent_ssh.copilot_plugins.prepare", prepare)
    remote = detach._remote
    def launch(_cfg, command, **kwargs):
        if "agent-worktrees embody" in command:
            clock[0] += venue_detached._SEEDED_LAUNCH_TIMEOUT
        return remote(_cfg, command, **kwargs)
    monkeypatch.setattr(detach, "_remote", launch)
    def claim(*a):
        assert clock[0] < 1000.0 + seams.reserve[0][2]["ttl_seconds"]
        return "sid-42"
    monkeypatch.setattr(venue_detached, "await_claim", claim)
    assert detach.cmd_detach(_args()) == 0
    assert seams.reserve[0][2]["ttl_seconds"] >= 750.0 + venue_detached._SEEDED_LAUNCH_TIMEOUT + 120.0
    assert "preparation_reservation_budget" not in json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("attached", [False, True])
def test_preparation_failure_prevents_native_launch_and_attach(seams, monkeypatch, attached):
    def refuse(*a):
        raise RuntimeError("required plugin absent")
    monkeypatch.setattr("agent_ssh.copilot_plugins.prepare", refuse)
    monkeypatch.setattr(detach.subprocess, "Popen", lambda *a, **kw: pytest.fail("must not attach"))
    args = _args(detach=not attached, copilot_args=[] if attached else ["--no-ask-user"])
    assert (detach.cmd_attached(args) if attached else detach.cmd_detach(args)) == 1
    assert not any("agent-worktrees embody" in command for command in seams.remote)
    assert seams.stop_keeper


@pytest.mark.parametrize("attached", [False, True])
def test_unknown_seed_admission_retains_session_keeper_and_journal(seams, monkeypatch, attached):
    from agent_ssh import copilot_delivery
    monkeypatch.setattr("venue_copilot.refs.deliver_note", lambda *a, **kw: False)
    monkeypatch.setattr(detach.subprocess, "Popen", lambda *a, **kw: pytest.fail("must not attach"))
    args = _args(detach=not attached, copilot_args=[] if attached else ["--no-ask-user"])
    assert (detach.cmd_attached(args) if attached else detach.cmd_detach(args)) == 75
    assert seams.stop_keeper == [] and seams.release == []
    assert not any("tmux kill-session" in command for command in seams.remote)
    pending = copilot_delivery.read("devbox", "anchor-repo@devbox")
    assert pending["status"] == "unknown"
    assert pending["envelope"]["prompt"] == "do the task"
    assert pending["requested_workspace"] == "/workspaces/repo"
    assert pending["keeper_hold_id"] == seams.keeper[0][1]["hold_id"]


@pytest.mark.parametrize("stopped", [False, True])
def test_unknown_delivery_verified_stop_retires_evidence_and_allows_fresh_launch(
    seams, monkeypatch, stopped,
):
    from agent_ssh import copilot_delivery

    monkeypatch.setattr("venue_copilot.refs.deliver_note", lambda *a, **kw: False)
    assert detach.cmd_detach(_args()) == 75
    pending = copilot_delivery.read("devbox", "anchor-repo@devbox")
    remote = detach._remote
    def stop(_cfg, command, **kwargs):
        if "tmux kill-session" in command and not stopped:
            return 3, "STILL_RUNNING", ""
        return remote(_cfg, command, **kwargs)
    monkeypatch.setattr(detach, "_remote", stop)
    assert detach.cmd_stop(_args(stop=True, detach=False)) == (0 if stopped else 1)
    current = copilot_delivery.read("devbox", "anchor-repo@devbox")
    archives = list((copilot_delivery._store().state_dir / "retired").glob("*.json"))
    if not stopped:
        assert current == pending and archives == []
        assert seams.release == [] and seams.stop_keeper == [] and seams.deregister == []
        return
    assert current is None and len(archives) == 1
    assert json.loads(archives[0].read_text()) == {
        "version": 1, "status": "verified_stopped", "target": "devbox", "delivery": pending,
    }
    monkeypatch.setattr("venue_copilot.refs.deliver_note", lambda *a, **kw: True)
    assert detach.cmd_detach(_args(seed="fresh task")) == 0
    assert len(seams.reserve) == 2
    assert json.loads(archives[0].read_text())["delivery"] == pending


def test_legacy_worktrees_receipt_refuses_unproven_typed_seed(seams, monkeypatch):
    def remote(_cfg, command, *, timeout=60):
        seams.remote.append(command)
        if "agent-worktrees embody" in command:
            return 0, json.dumps({"ok": True, "created": True, "seed_submitted": True}), ""
        return 0, "", ""
    monkeypatch.setattr(detach, "_remote", remote)
    assert detach.cmd_detach(_args()) == 1
    assert any("tmux kill-session" in command for command in seams.remote)


def test_attached_prepares_and_admits_before_mux_attach(seams, monkeypatch):
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
    seen = {}

    class FakeProcess:
        pid = 999

        def wait(self):
            return 0

    def fake_popen(argv, **kwargs):
        seen["ssh_argv"] = argv
        seen["subprocess_kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(detach.subprocess, "Popen", fake_popen)

    rc = detach.cmd_attached(
        _args(detach=False, workspace="/workspaces/repo", copilot_args=[]),
    )

    assert rc == 0
    assert seams.reserve[0][0] == "anchor-repo@devbox"
    argv = seen["ssh_argv"]
    assert "-t" in argv
    assert "-R" not in argv
    assert seams.keeper[0][1].items() >= {
        "venue_port": 41234,
        "mux": "wt-anchor-repo",
    }.items()
    assert seams.keeper[0][1]["hold_id"].startswith(f"attached:{os.getpid()}:")
    assert seams.stop_keeper == [("devbox", seams.keeper[0][1]["hold_id"])]
    remote_command = argv[-1]
    assert remote_command.startswith("bash -lc ")
    assert "tmux attach-session -t" in remote_command
    launch = next(command for command in seams.remote if "agent-worktrees embody" in command)
    assert "cd /workspaces/repo" in launch
    assert "trustedFolders" in launch
    assert "--bridge-seed-pending" in launch
    assert "do the task" not in launch
    assert "auth.yaml" not in remote_command
    assert any("auth.yaml" in command for command in seams.remote)


@pytest.mark.parametrize("failure", ["reservation", "popen"])
def test_attached_releases_its_keeper_hold_when_launch_fails(seams, monkeypatch, failure):
    monkeypatch.setattr(
        detach, "_ssh_config",
        lambda target: types.SimpleNamespace(
            config_file=None, port=None, identity_file=None, extra_options={}, ssh_target=target),
    )
    monkeypatch.setattr(detach, "_ensure_posix", lambda _cfg: None)
    monkeypatch.setattr(detach, "_ensure_remote_tooling", lambda _cfg: None)
    monkeypatch.setattr(detach, "resolve_daemon_port", lambda: 41234)
    if failure == "reservation":
        def refuse(*a, **kw):
            raise detach.VenueCopilotError("reservation refused")
        monkeypatch.setattr(venue_detached, "reserve_with_retry", refuse)

    def failing_popen(argv, **kwargs):
        raise OSError("ssh could not start")

    monkeypatch.setattr(detach.subprocess, "Popen", failing_popen)

    args = _args(detach=False, workspace="/workspaces/repo", copilot_args=[])
    assert detach.cmd_attached(args) == 1
    assert seams.stop_keeper == [("devbox", seams.keeper[0][1]["hold_id"])]


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
    monkeypatch.setattr(detach, "read_keeper_state", lambda target: {"venue_port": 41234})
    monkeypatch.setattr(detach._STORE, "alive", lambda key: True)
    monkeypatch.setattr(detach, "_update_keeper_hold_pid", lambda *a, **k: None)
    seen = {}

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


def test_stale_launch_cleanup_does_not_remove_refreshed_keeper_hold(
    tmp_path: Path,
    monkeypatch,
):
    store = detach.KeeperStore(tmp_path)
    monkeypatch.setattr(detach, "_STORE", store)
    monkeypatch.setattr(store, "alive", lambda key: store.read(key) is not None)
    stamps = itertools.count(1000.0)
    monkeypatch.setattr(shared_keeper_holds.time, "time", lambda: next(stamps))
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

    first = detach.ensure_keeper(
        "devbox",
        venue_port=41234,
        mux="wt-anchor-repo",
        hold_id="anchor-repo@devbox",
    )
    second = detach.ensure_keeper(
        "devbox",
        venue_port=41234,
        mux="wt-anchor-repo",
        hold_id="anchor-repo@devbox",
    )

    assert first["hold_added"] is True
    assert second["hold_added"] is False
    assert (
        detach.stop_keeper(
            "devbox",
            hold_id="anchor-repo@devbox",
            expected_updated_at=first["hold_updated_at"],
        )
        is False
    )
    state = detach.read_keeper_state("devbox")
    assert state is not None
    assert "anchor-repo@devbox" in state["holds"]
    assert detach._STORE.alive(detach._state_key("devbox")) is True


@pytest.fixture
def real_keeper(seams, monkeypatch):
    store = detach._STORE
    monkeypatch.setattr(detach, "ensure_keeper", real_ensure_keeper)
    monkeypatch.setattr(detach, "stop_keeper", real_stop_keeper)
    monkeypatch.setattr(detach, "read_keeper_state", lambda target: store.read(target))
    monkeypatch.setattr(store, "alive", lambda key: store.read(key) is not None)
    monkeypatch.setattr(store, "stop", lambda key: store.remove(key) or False)
    monkeypatch.setattr(detach, "process_identity", lambda pid: f"synthetic-{pid}")
    monkeypatch.setattr(detach, "spawn_keeper", lambda _argv, _env, state, **_kw: {
        **state, "pid": 99999, "pid_identity": "synthetic-99999",
    })
    return store


@pytest.mark.parametrize("selection", ["parent", "linked"])
def test_keeper_runtime_root_uses_real_admission(seams, tmp_path, monkeypatch, selection):
    from agent_procutil.daemon_instances import (
        cancel_instance_reservation, inspect_instances, reserve_instance,
    )

    launch = tmp_path / "launch"
    launch.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    root = launch / ".." / "state"
    if selection == "linked":
        root = launch / "linked"
        try:
            root.symlink_to(state, target_is_directory=True)
        except OSError as error:
            pytest.skip(f"native directory symlink unavailable: {error}")
    store = detach.KeeperStore(root)
    if selection == "parent":
        assert ".." in store.state_path("devbox").parts
    monkeypatch.setattr(detach, "_STORE", store)
    monkeypatch.setattr(detach, "read_keeper_state", lambda target: store.read(target))
    monkeypatch.setattr(store, "alive", lambda key: store.read(key) is not None)
    monkeypatch.setattr(store, "stop", lambda key: store.remove(key) or False)
    admitted = []

    def spawn(argv, environment, payload, *, popen, **kwargs):
        instance = popen.keywords["instance"]
        receipt = reserve_instance(instance, environment=environment)
        try:
            row = next(row for row in inspect_instances(receipt.registry_root)
                       if row["instance_id"] == receipt.instance_id)
            assert row["runtime_root"] == str(state) and row["state"] == "reserved"
            admitted.append(instance.runtime_root)
        finally:
            cancel_instance_reservation(receipt, creation_refused=True)
        return {**payload, "pid": 99999, "pid_identity": "synthetic-99999"}

    monkeypatch.setattr(detach, "spawn_keeper", spawn)
    if selection == "linked":
        with pytest.raises((OSError, ValueError, RuntimeError)):
            real_ensure_keeper("devbox", venue_port=41234, mux="fixture", probe=lambda _: True)
        assert admitted == []
    else:
        result = real_ensure_keeper("devbox", venue_port=41234, mux="fixture", probe=lambda _: True)
        assert result["started"] and admitted == [state]


@pytest.mark.parametrize("other_hold", [False, True])
def test_attached_recovery_reuses_and_releases_original_hold(
    real_keeper, monkeypatch, other_hold,
):
    from agent_ssh import copilot_delivery
    from venue_copilot.refs import owned_result

    monkeypatch.setattr(detach, "_mux_exists", lambda *a: True)
    monkeypatch.setattr(detach, "build_remote_exec_args", lambda *a, **kw: ["ssh", "synthetic"])
    monkeypatch.setattr("venue_copilot.refs.deliver_note", lambda *a, **kw: False)
    monkeypatch.setattr("venue_copilot.release_cli_mode", lambda *a, **kw: 1)
    monkeypatch.setattr(detach.subprocess, "Popen", lambda *a, **kw: pytest.fail("must not attach"))
    args = _args(detach=False, copilot_args=[])
    assert detach.cmd_attached(args) == 75
    pending = copilot_delivery.read("devbox", "anchor-repo@devbox")
    original_hold = pending["keeper_hold_id"]
    assert set(real_keeper.read("devbox")["holds"]) == {original_hold}
    if other_hold:
        detach.ensure_keeper(
            "devbox", venue_port=41234, mux="wt-other", hold_id="other-scope",
            probe=lambda mux: True,
        )
    admitted = [False]
    def reconcile(entry):
        assert entry["operation"] == pending["operation"]
        assert entry["keeper_hold_id"] == original_hold
        assert set(real_keeper.read("devbox")["holds"]) == (
            {original_hold, "other-scope"} if other_hold else {original_hold}
        )
        envelope = entry["envelope"]
        return owned_result(
            admitted[0], session_id=entry["session_id"], operation=entry["operation"],
            note=envelope["prompt"], context=envelope["plugin_context"],
        )
    monkeypatch.setattr(copilot_delivery, "reconcile_owned_delivery", reconcile)
    assert detach.cmd_attached(args) == 75
    admitted[0] = True
    monkeypatch.setattr(copilot_delivery, "settle_deferred_seed", lambda *a: True)
    class Child:
        pid = 12345
        def wait(self):
            hold = real_keeper.read("devbox")["holds"][original_hold]
            assert hold["mux"] == detach._attached_hold_mux(self.pid)
            return 0
    monkeypatch.setattr(detach.subprocess, "Popen", lambda *a, **kw: Child())
    assert detach.cmd_attached(args) == 0
    assert copilot_delivery.read("devbox", "anchor-repo@devbox") is None
    state = real_keeper.read("devbox")
    if other_hold:
        assert set(state["holds"]) == {"other-scope"}
    else:
        assert state is None


def test_attached_pid_handoff_releases_with_refreshed_timestamp(real_keeper, monkeypatch):
    stamps = itertools.count(1000.0)
    monkeypatch.setattr(shared_keeper_holds.time, "time", lambda: next(stamps))
    monkeypatch.setattr(detach, "_mux_exists", lambda *a: True)
    monkeypatch.setattr(detach, "_ssh_config", lambda target: types.SimpleNamespace(
        config_file=None, port=None, identity_file=None, extra_options={}, ssh_target=target,
    ))
    seen = {}
    class Child:
        pid = 12345
        def wait(self):
            state = real_keeper.read("devbox")
            hold_id, hold = next(iter(state["holds"].items()))
            seen["hold_id"] = hold_id
            assert hold["mux"] == detach._attached_hold_mux(self.pid)
            seen["timestamp"] = hold["updated_at"]
            return 0
    monkeypatch.setattr(detach.subprocess, "Popen", lambda *a, **kw: Child())
    assert detach.cmd_attached(_args(detach=False, copilot_args=[])) == 0
    assert seen["timestamp"] > 1000.0
    state = real_keeper.read("devbox")
    assert state is None or seen["hold_id"] not in state["holds"]


@pytest.mark.parametrize("retained", ["none", "peer", "refreshed"])
def test_pending_attached_stop_retires_original_hold_before_immediate_fresh_attach(
    real_keeper, monkeypatch, retained,
):
    from agent_ssh import copilot_delivery

    clock, live = [1000.0], [True]
    monkeypatch.setattr(shared_keeper_holds.time, "time", lambda: clock[0])
    monkeypatch.setattr(detach, "_mux_exists", lambda _cfg, mux: mux == "wt-peer" or live[0])
    monkeypatch.setattr(detach, "build_remote_exec_args", lambda *a, **kw: ["ssh", "synthetic"])
    monkeypatch.setattr("venue_copilot.refs.deliver_note", lambda *a, **kw: False)
    monkeypatch.setattr(detach.subprocess, "Popen", lambda *a, **kw: pytest.fail("must not attach"))
    args = _args(detach=False, copilot_args=[])
    assert detach.cmd_attached(args) == 75
    pending = copilot_delivery.read("devbox", "anchor-repo@devbox")
    original = pending["keeper_hold_id"]
    if retained == "peer":
        detach.ensure_keeper("devbox", venue_port=41234, mux="wt-peer", hold_id="peer", probe=lambda mux: True)
    remote = detach._remote
    def terminate(_cfg, command, **kwargs):
        if "tmux kill-session" in command:
            live[0] = False
            if retained == "refreshed":
                clock[0] += 1
                detach._update_keeper_hold_mux("devbox", original, pending["mux_session"])
        elif "agent-worktrees embody" in command:
            live[0] = True
        return remote(_cfg, command, **kwargs)
    monkeypatch.setattr(detach, "_remote", terminate)
    assert detach.cmd_stop(_args(stop=True, detach=False, copilot_args=[])) == 0
    assert copilot_delivery.read("devbox", "anchor-repo@devbox") is None
    expected = {"peer"} if retained == "peer" else {original} if retained == "refreshed" else set()
    state = real_keeper.read("devbox")
    assert set(state["holds"] if state else {}) == expected
    monkeypatch.setattr("venue_copilot.refs.deliver_note", lambda *a, **kw: True)
    class Child:
        pid = 12345
        def wait(self):
            holds = real_keeper.read("devbox")["holds"]
            assert len(holds) == len(expected) + 1
            assert original in holds if retained == "refreshed" else original not in holds
            return 0
    monkeypatch.setattr(detach.subprocess, "Popen", lambda *a, **kw: Child())
    assert detach.cmd_attached(args) == 0
    state = real_keeper.read("devbox")
    assert set(state["holds"] if state else {}) == expected
    if retained == "refreshed":
        assert state["holds"][original]["updated_at"] == 1001.0


def test_launcher_hold_survives_preparation_beyond_mux_startup_grace(real_keeper, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(shared_keeper_holds.time, "time", lambda: clock[0])
    monkeypatch.setattr(detach, "_mux_exists", lambda *a: False)
    def prepare(*a):
        clock[0] += 400.0  # No mux exists; the real launcher process is still alive.
        _state, holds, _live = detach._holds().prune_snapshot(
            "devbox", probe=lambda mux: detach._probe_hold(object(), mux),
        )
        hold = holds["anchor-repo@devbox"]
        assert hold["mux"] == detach._attached_hold_mux(os.getpid())
        assert real_keeper.alive("devbox")
        return {"version": 1, "cwd": "/workspaces/repo", "plugins": [], "logical_payloads": {}}
    monkeypatch.setattr("agent_ssh.copilot_plugins.prepare", prepare)
    assert detach.cmd_detach(_args()) == 0
    state = real_keeper.read("devbox")
    assert state["holds"]["anchor-repo@devbox"]["mux"] == "wt-anchor-repo"
    assert state["holds"]["anchor-repo@devbox"]["updated_at"] == 1400.0


@pytest.mark.parametrize("failure", ["reservation", "preparation"])
def test_failed_rejoin_preserves_existing_live_mux_hold(real_keeper, monkeypatch, failure):
    clock = [1000.0]
    monkeypatch.setattr(shared_keeper_holds.time, "time", lambda: clock[0])
    monkeypatch.setattr(detach, "_mux_exists", lambda *a: True)
    detach.ensure_keeper(
        "devbox", venue_port=41234, mux="wt-anchor-repo", hold_id="anchor-repo@devbox",
        probe=lambda mux: detach._probe_hold(object(), mux),
    )
    def refuse(*a, **kw):
        state = real_keeper.read("devbox")
        assert state["holds"]["anchor-repo@devbox"]["mux"] == "wt-anchor-repo"
        raise RuntimeError("required plugin unavailable")
    monkeypatch.setattr(
        "venue_copilot.detached.reserve_with_retry" if failure == "reservation"
        else "agent_ssh.copilot_plugins.prepare", refuse,
    )
    assert detach.cmd_detach(_args()) == 1
    # The launcher's PID is now gone; only the original native mux is live.
    monkeypatch.setattr(detach, "process_identity", lambda pid: None)
    clock[0] += 400.0
    _state, holds, live_muxes = detach._holds().prune_snapshot(
        "devbox", probe=lambda mux: detach._probe_hold(object(), mux),
    )
    assert holds["anchor-repo@devbox"]["mux"] == "wt-anchor-repo"
    assert "wt-anchor-repo" in live_muxes and real_keeper.alive("devbox")


@pytest.mark.parametrize("failure", ["reservation", "preparation"])
def test_failed_rejoin_restores_mux_hold_after_indeterminate_probe(real_keeper, monkeypatch, failure):
    clock = [1000.0]
    scope = "anchor-repo@devbox"
    monkeypatch.setattr(shared_keeper_holds.time, "time", lambda: clock[0])
    detach.ensure_keeper("devbox", venue_port=41234, mux="wt-anchor-repo", hold_id=scope)
    _state, holds, _live = detach._holds().prune_snapshot("devbox", probe=lambda mux: True)
    original = dict(holds[scope])
    clock[0] += 30
    monkeypatch.setattr(detach, "_mux_exists", lambda *a: None)
    def refuse(*a, **kw):
        assert real_keeper.read("devbox")["holds"][scope]["mux"] == detach._attached_hold_mux(os.getpid())
        raise RuntimeError("required plugin unavailable")
    monkeypatch.setattr(
        "venue_copilot.detached.reserve_with_retry" if failure == "reservation"
        else "agent_ssh.copilot_plugins.prepare", refuse,
    )
    assert detach.cmd_detach(_args()) == 1
    assert real_keeper.read("devbox")["holds"][scope] == original
    monkeypatch.setattr(detach, "process_identity", lambda pid: None)
    clock[0] += 400
    _state, retained, _live = detach._holds().prune_snapshot(
        "devbox", probe=lambda mux: detach._probe_hold(object(), mux),
    )
    assert retained[scope] == original and real_keeper.alive("devbox")


def test_original_mux_restoration_cannot_overwrite_a_newer_owner(real_keeper):
    scope = "anchor-repo@devbox"
    current = {"mux": "wt-new-owner", "updated_at": 30.0, "confirmed_at": 30.0}
    real_keeper.write("devbox", {"holds": {scope: current}})
    adapter = detach._SshAdapter(target="devbox", ssh_config=object(), hold_id=scope)
    adapter._launch_pending = True
    adapter._hold_updated_at = 20.0
    adapter._previous_hold = {"mux": "wt-original", "updated_at": 10.0}
    with pytest.raises(RuntimeError, match="hold changed"):
        adapter.restore_keeper_hold()
    assert real_keeper.read("devbox")["holds"][scope] == current


def test_stop_during_preparation_refuses_pid_hold_without_releasing_native_ownership(
    real_keeper, seams, monkeypatch,
):
    def prepare(*a):
        hold = real_keeper.read("devbox")["holds"]["anchor-repo@devbox"]
        assert hold["mux"] == detach._attached_hold_mux(os.getpid())
        assert detach.cmd_stop(_args(stop=True, detach=False)) == 1
        assert seams.release == [] and seams.deregister == []
        assert not any("tmux kill-session" in command for command in seams.remote)
        return {"version": 1, "cwd": "/workspaces/repo", "plugins": [], "logical_payloads": {}}
    monkeypatch.setattr("agent_ssh.copilot_plugins.prepare", prepare)
    assert detach.cmd_detach(_args()) == 0
    assert real_keeper.read("devbox")["holds"]["anchor-repo@devbox"]["mux"] == "wt-anchor-repo"


@pytest.mark.parametrize("attached", [False, True])
def test_active_preparation_excludes_stop_even_without_a_scoped_keeper_hold(seams, monkeypatch, attached):
    def prepare(*a):
        assert detach.cmd_stop(_args(stop=True, detach=False)) == 1
        assert seams.release == [] and seams.deregister == []
        assert not any("tmux kill-session" in command for command in seams.remote)
        raise RuntimeError("required plugin unavailable")
    monkeypatch.setattr("agent_ssh.copilot_plugins.prepare", prepare)
    args = _args(detach=not attached, copilot_args=[] if attached else ["--no-ask-user"])
    assert (detach.cmd_attached(args) if attached else detach.cmd_detach(args)) == 1


def test_competing_launch_cannot_mutate_an_active_scope(seams, monkeypatch):
    context = {"version": 1, "cwd": "/workspaces/repo", "plugins": [], "logical_payloads": {}}
    def prepare(*a):
        if len(seams.reserve) == 1:
            assert detach.cmd_detach(_args(seed="competing task")) == 1
            assert len(seams.reserve) == len(seams.keeper) == 1
        return dict(context)
    monkeypatch.setattr("agent_ssh.copilot_plugins.prepare", prepare)
    assert detach.cmd_detach(_args()) == 0


def test_lifecycle_lock_is_cross_process_and_scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(detach, "_STORE", detach.KeeperStore(tmp_path))
    program = """
import sys
from pathlib import Path
from agent_ssh import copilot_detach as owner
owner._STORE = owner.KeeperStore(Path(sys.argv[1]))
try:
    with owner._lifecycle_lock(sys.argv[2]):
        print("ACQUIRED")
except RuntimeError:
    print("BUSY")
    raise SystemExit(3)
"""
    def attempt(scope):
        return subprocess.run(
            [sys.executable, "-c", program, str(tmp_path), scope],
            capture_output=True, text=True, timeout=15, creationflags=no_window_flags(),
        )
    with detach._lifecycle_lock("anchor-repo@host"):
        blocked = attempt("anchor-repo@host")
        assert blocked.returncode == 3 and blocked.stdout.strip() == "BUSY", blocked.stderr
        other = attempt("anchor-other@host")
        assert other.returncode == 0 and other.stdout.strip() == "ACQUIRED", other.stderr
    released = attempt("anchor-repo@host")
    assert released.returncode == 0 and released.stdout.strip() == "ACQUIRED", released.stderr


def test_failed_keeper_handoff_cleans_up_native_session(seams, monkeypatch):
    def fail(*a):
        raise RuntimeError("keeper handoff failed")
    monkeypatch.setattr(detach._SshAdapter, "handoff_keeper", fail)
    assert detach.cmd_detach(_args()) == 1
    assert any("tmux kill-session" in command for command in seams.remote)
    assert seams.stop_keeper == [("devbox", "anchor-repo@devbox")]
    assert seams.release == [("anchor-repo@devbox", "r1")]

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


def test_workspace_normalization_trims_slashes_but_keeps_the_root():
    assert detach._normalize_workspace("/workspaces/repo//") == "/workspaces/repo"
    assert detach._normalize_workspace("/") == "/"
    assert detach._normalize_workspace(" /// ") == "/"
    with pytest.raises(ValueError):
        detach._normalize_workspace("  ")


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


@pytest.mark.parametrize("action", ["set", "list"])
def test_copilot_config_cli_reports_filesystem_errors_as_failures(action, monkeypatch, capsys):
    """A read-only home (or any other OSError) is a [FAIL] result, not a traceback."""
    def _denied(*_a, **_k):
        raise PermissionError("read-only file system")

    monkeypatch.setattr(detach, "set_host_workspace", _denied)
    monkeypatch.setattr(detach, "_load_copilot_config", _denied)
    argv = ["copilot-config", action]
    if action == "set":
        argv += ["devbox", "--workspace", "/workspaces/new"]

    assert main(argv) == 2
    assert "[FAIL] read-only file system" in capsys.readouterr().err


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


def test_unsubmitted_seed_on_registered_session_is_delivered_over_bridge(seams, monkeypatch, capsys):
    from venue_copilot import refs as venue_refs

    unseeded = json.dumps({
        "ok": True, "created": True, "seed_submitted": False, "bridge_seed_deferred": True,
    })
    sent = []
    monkeypatch.setattr(venue_refs, "deliver_note", lambda sid, note, **kw: sent.append((sid, note)) or True)

    def remote(_cfg, command, *, timeout=60.0):
        seams.remote.append(command)
        if "agent-worktrees embody" in command:
            return 0, unseeded, ""
        if "tmux kill-session" in command:
            return 0, "STOPPED\n", ""
        return 0, "", ""

    monkeypatch.setattr(detach, "_remote", remote)
    assert detach.cmd_detach(_args()) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["seed_delivery"] == "bridge"
    assert out["seeded"] is True
    assert sent == [("sid-42", "do the task")]
    assert not any("tmux kill-session" in command for command in seams.remote)
    assert seams.stop_keeper == []


def test_unsubmitted_seed_without_registration_kills_mux_and_stops_keeper(seams, monkeypatch):
    unseeded = json.dumps({
        "ok": True, "created": True, "seed_submitted": False, "bridge_seed_deferred": True,
    })
    monkeypatch.setattr(venue_detached, "await_claim", lambda scope, rid, timeout: None)

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
    resumed = json.dumps({
        "ok": True, "created": False, "resumed": True, "bridge_seed_deferred": True,
    })

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
    unseeded = json.dumps({
        "ok": True, "created": True, "seed_submitted": False, "bridge_seed_deferred": True,
    })

    def remote(_cfg, command, *, timeout=60.0):
        seams.remote.append(command)
        if "agent-worktrees embody" in command:
            return 0, unseeded, ""
        if "tmux kill-session" in command:
            return 0, "STOPPED\n", ""
        return 0, "", ""

    monkeypatch.setattr(detach, "_remote", remote)
    # A real launch failure: the created session never registers.
    monkeypatch.setattr(venue_detached, "await_claim", lambda scope, rid, timeout: None)

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


def test_superseded_forward_keeper_never_overwrites_the_new_instance(tmp_path, monkeypatch):
    store = detach.KeeperStore(tmp_path)
    monkeypatch.setattr(detach, "_STORE", store)
    monkeypatch.setattr(detach, "_ssh_config", lambda target: object())
    monkeypatch.setenv(detach._KEEPER_TOKEN_ENV, "old")
    current = {
        "target": "devbox", "venue_port": 41234, "instance_token": "new",
        "holds": {"attached:2": {"mux": "wt-b", "updated_at": 1.0, "confirmed_at": 1.0}},
    }
    store.write("devbox", current)

    class Forward:
        process_pid = None
        process_birth_identity = None

        def __init__(self, *_args, **_kwargs):
            pass

    async def fake_loop(forwards, **kwargs):
        kwargs["write_state"]()
        return 0

    monkeypatch.setattr(detach, "SupervisedRelayForward", Forward)
    monkeypatch.setattr(detach, "run_supervised_loop", fake_loop)
    args = argparse.Namespace(
        target="devbox", venue_port=41234, mux="wt-a", probe_interval=15.0, startup_grace=300.0,
    )

    assert asyncio.run(detach._run_forward_keeper(args)) == 0
    assert store.read("devbox") == current


def test_attached_reaps_ssh_child_when_hold_bookkeeping_fails(seams, monkeypatch):
    monkeypatch.setattr(
        detach, "_ssh_config",
        lambda target: types.SimpleNamespace(
            config_file=None, port=None, identity_file=None, extra_options={}, ssh_target=target),
    )
    monkeypatch.setattr(detach, "_ensure_posix", lambda _cfg: None)
    monkeypatch.setattr(detach, "_ensure_remote_tooling", lambda _cfg: None)
    monkeypatch.setattr(detach, "resolve_daemon_port", lambda: 41234)
    events = []

    class FakeProcess:
        pid = 999

        def terminate(self):
            events.append("terminate")

        def wait(self, timeout=None):
            events.append("wait")
            return 0

    monkeypatch.setattr(detach.subprocess, "Popen", lambda argv, **kw: FakeProcess())

    def failing_update(*_a, **_k):
        raise RuntimeError("Could not acquire forward-keeper state lock")

    monkeypatch.setattr(detach, "_update_keeper_hold_pid", failing_update)

    assert detach.cmd_attached(_args(detach=False, workspace="/workspaces/repo", copilot_args=[])) == 1
    assert events == ["terminate", "wait"]
    assert seams.stop_keeper == [("devbox", seams.keeper[0][1]["hold_id"])]


def test_attached_result_survives_a_failed_final_hold_release(seams, monkeypatch, capsys):
    monkeypatch.setattr(
        detach, "_ssh_config",
        lambda target: types.SimpleNamespace(
            config_file=None, port=None, identity_file=None, extra_options={}, ssh_target=target),
    )
    monkeypatch.setattr(detach, "_ensure_posix", lambda _cfg: None)
    monkeypatch.setattr(detach, "_ensure_remote_tooling", lambda _cfg: None)
    monkeypatch.setattr(detach, "resolve_daemon_port", lambda: 41234)
    monkeypatch.setattr(
        detach.subprocess, "Popen",
        lambda argv, **kw: types.SimpleNamespace(pid=999, wait=lambda timeout=None: 7),
    )

    def failing_stop(*_a, **_k):
        raise RuntimeError("Could not acquire forward-keeper state lock")

    monkeypatch.setattr(detach, "stop_keeper", failing_stop)

    assert detach.cmd_attached(_args(detach=False, workspace="/workspaces/repo", copilot_args=[])) == 7
    assert "could not release the attached hold" in capsys.readouterr().err


def test_keeper_hold_store_uses_sanitized_keeper_state_path(tmp_path, monkeypatch):
    store = detach.KeeperStore(tmp_path)
    monkeypatch.setattr(detach, "_STORE", store)

    path = detach._holds().state_path("codespace:repo/branch").with_suffix(".lock")

    assert path.parent == tmp_path
    assert path.name == "codespace-repo-branch.lock"

from __future__ import annotations

import argparse
import json
import types

import pytest
from venue_copilot import detached as venue_detached

from agent_ssh import copilot_detach as detach


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
    )
    base.update(kw)
    return argparse.Namespace(**base)


@pytest.fixture
def seams(monkeypatch):
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
        lambda *a, **k: calls.keeper.append((a, k)) or {"started": True, "state": {"pid": 123}},
    )
    monkeypatch.setattr(detach, "stop_keeper", lambda target: calls.stop_keeper.append(target) or True)
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
    assert seams.keeper[0][1] == {"venue_port": 41234, "mux": "wt-anchor-repo"}
    launch = next(command for command in seams.remote if "agent-worktrees embody" in command)
    assert "cd /workspaces/repo" in launch
    assert "--bridge-scope-id anchor-repo@devbox" in launch
    assert "--copilot-arg=--no-ask-user" in launch
    assert "--json" in launch
    assert seams.release == [("anchor-repo@devbox", "r1")]


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
    assert seams.stop_keeper == ["devbox"]


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
    assert seams.stop_keeper == ["devbox"]


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


def test_stop_kills_mux_stops_keeper_and_deregisters(seams, capsys):
    rc = detach.cmd_stop(_args(stop=True, detach=False))
    assert rc == 0
    assert any("tmux kill-session" in command for command in seams.remote)
    assert seams.stop_keeper == ["devbox"]
    assert seams.release == [("anchor-repo@devbox", None)]
    assert seams.deregister == ["sid-42"]
    assert json.loads(capsys.readouterr().out)["deregistered"] == "sid-42"

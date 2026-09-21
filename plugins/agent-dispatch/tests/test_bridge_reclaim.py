"""Tests for agent_dispatch.bridge_reclaim -- the reclaim path's replacement
for the removed ``create --reclaim`` (agent-bridge-cold-resume Phase 3,
#6744): resume, and if a live interactive CLI holds the worktree, actually
stop it (via agent-worktrees) before forcing the take-over."""

from __future__ import annotations

import json
import subprocess

from agent_dispatch import bridge_reclaim


def _proc(cmd, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)


def _resume(monkeypatch, fake_run, **overrides):
    monkeypatch.setattr(bridge_reclaim.subprocess, "run", fake_run)
    monkeypatch.setattr(
        bridge_reclaim, "agent_worktrees_launch_prefix",
        lambda: ["/usr/bin/agent-worktrees"],
    )
    kwargs = dict(
        exe=["/usr/bin/agent-bridge"], agent="task-worker",
        caller="agent-dispatch:w1", wait=True, json_output=False, timeout=None,
    )
    kwargs.update(overrides)
    return bridge_reclaim.resume_worktree_and_send("wt-1", "the seed", **kwargs)


def test_resume_then_send_happy_path_no_holder(monkeypatch):
    """The common case: no live interactive CLI holds the worktree at all --
    a plain (non-forcing) resume succeeds outright, no stop is attempted."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[1:3] == ["--json", "resume"]:
            return _proc(cmd, 0, json.dumps({"session_id": "resumed-9"}))
        return _proc(cmd, 0, "ok")

    result = _resume(monkeypatch, fake_run, wait=False)
    assert result.returncode == 0
    assert len(calls) == 2
    assert calls[0] == ["/usr/bin/agent-bridge", "--json", "resume", "wt-1"]
    assert "--force" not in calls[0]
    assert calls[1] == [
        "/usr/bin/agent-bridge", "send", "resumed-9", "--prompt-file", "-",
        "--caller", "agent-dispatch:w1", "--no-wait",
    ]


def test_resume_failure_short_circuits(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _proc(cmd, 1, "", "connect refused")

    result = _resume(monkeypatch, fake_run)
    assert result.returncode == 1
    assert len(calls) == 1  # send is never attempted, no stop attempted either


def test_live_cli_holder_is_stopped_then_force_resumed(monkeypatch):
    """The actual 'reclaim' verb: kill the interactive CLI holding the
    worktree (agent-worktrees restart), THEN force-take-over via resume
    --force -- never bypass the guard without confirming the holder is
    actually gone first."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[:2] == ["/usr/bin/agent-worktrees", "restart"]:
            return _proc(cmd, 0, json.dumps(
                {"ok": True, "had_session": True, "method": "graceful"}
            ))
        if cmd[1:3] == ["--json", "resume"]:
            if "--force" not in cmd:
                return _proc(
                    cmd, 1,
                    '{"error": "a live interactive CLI (live-7) still holds '
                    'worktree wt-1", "reason": "live_cli_holds_worktree", '
                    '"session_id": "live-7"}',
                    "",
                )
            return _proc(cmd, 0, json.dumps({"session_id": "reclaimed-1"}))
        return _proc(cmd, 0, "ok")

    result = _resume(monkeypatch, fake_run)
    assert result.returncode == 0
    kinds = [(c[0], c[1] if len(c) > 1 else None) for c in calls]
    assert kinds == [
        ("/usr/bin/agent-bridge", "--json"),   # 1st resume, no --force -> 409
        ("/usr/bin/agent-worktrees", "restart"),  # stop the interactive CLI
        ("/usr/bin/agent-bridge", "--json"),   # 2nd resume, --force -> ok
        ("/usr/bin/agent-bridge", "send"),
    ]
    assert calls[1] == ["/usr/bin/agent-worktrees", "restart", "wt-1", "--json"]
    assert "--force" in calls[2]
    assert "reclaimed-1" in calls[3]


def test_stop_failure_is_reported_without_forcing_through(monkeypatch):
    """If agent-worktrees can't confirm the interactive CLI actually stopped,
    this must NOT fall through to '--force' anyway -- that would risk the
    exact duplicate-controller race the guard exists to prevent."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[:2] == ["/usr/bin/agent-worktrees", "restart"]:
            return _proc(cmd, 0, json.dumps({"ok": False, "error": "quit hung"}))
        if cmd[1:3] == ["--json", "resume"]:
            return _proc(
                cmd, 1,
                '{"reason": "live_cli_holds_worktree", "session_id": "live-7"}',
                "",
            )
        return _proc(cmd, 0, "ok")

    result = _resume(monkeypatch, fake_run)
    assert result.returncode == 1
    assert "could not stop the interactive CLI" in result.stderr
    assert len(calls) == 2  # one resume attempt, one stop attempt -- never a 2nd resume
    assert not any("--force" in c for c in calls)


def test_missing_session_id_reports_failure_not_a_legacy_fallback(monkeypatch):
    """A successful (returncode 0) resume whose stdout carries no session_id
    means a not-yet-upgraded daemon already resumed/created a session via
    its old human '[OK] ...' text -- NOT a genuine new failure, but there is
    no safe way to recover that session's id, so this reports failure rather
    than guessing (the caller degrades by leaving the task queued)."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _proc(cmd, 0, "[OK] Worktree wt-1 loaded as owned session s1 (idle)")

    result = _resume(monkeypatch, fake_run, wait=False)
    assert result.returncode == 1
    assert "no session_id" in result.stderr
    assert len(calls) == 1  # never falls back to 'create'


def test_busy_reused_session_is_ended_then_resumed_again_for_its_replacement(
    monkeypatch,
):
    """resume *reuses* an existing live session; if it is mid-turn, plain
    send refuses busy (exit 75). 'end --force' *deletes* that exact session,
    so the prompt can no longer reach it -- this path resumes the worktree
    again (getting its replacement) and sends to THAT session, once."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[1:3] == ["--json", "resume"]:
            sid = "reused-1" if calls.count(list(cmd)) == 1 else "fresh-2"
            return _proc(cmd, 0, json.dumps({"session_id": sid}))
        if cmd[1] == "send" and "reused-1" in cmd:
            return _proc(cmd, bridge_reclaim._SEND_BUSY_EXIT, "", "busy")
        if cmd[1] == "end":
            return _proc(cmd, 0, "ended")
        return _proc(cmd, 0, "ok")  # send to fresh-2

    result = _resume(monkeypatch, fake_run)
    assert result.returncode == 0
    kinds = [c[1] for c in calls]
    assert kinds == ["--json", "send", "end", "--json", "send"]
    assert calls[2] == ["/usr/bin/agent-bridge", "end", "reused-1", "--force"]
    assert "fresh-2" in calls[4]
    assert not any("--force" in c for c in calls if c[1] == "--json")


def test_json_output_reshapes_send_result(monkeypatch):
    def fake_run(cmd, **kwargs):
        if cmd[1:3] == ["--json", "resume"]:
            return _proc(cmd, 0, json.dumps({"session_id": "resumed-9"}))
        return _proc(cmd, 0, "raw send output")

    result = _resume(monkeypatch, fake_run, json_output=True)
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"session_id": "resumed-9"}

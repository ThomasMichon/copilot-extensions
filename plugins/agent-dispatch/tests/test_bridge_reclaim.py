"""Tests for agent_dispatch.bridge_reclaim (the reclaim path's two-call
equivalent of the removed ``create --reclaim``, agent-bridge-cold-resume
Phase 3, #6744)."""

from __future__ import annotations

import json
import subprocess

from agent_dispatch import bridge_reclaim


def _proc(cmd, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)


def _resume(monkeypatch, fake_run, **overrides):
    monkeypatch.setattr(bridge_reclaim.subprocess, "run", fake_run)
    kwargs = dict(
        exe=["/usr/bin/agent-bridge"], agent="task-worker",
        caller="agent-dispatch:w1", wait=True, json_output=False, timeout=None,
    )
    kwargs.update(overrides)
    return bridge_reclaim.resume_worktree_and_send("wt-1", "the seed", **kwargs)


def test_resume_then_send_happy_path(monkeypatch):
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
    assert len(calls) == 1  # send is never attempted


def test_never_forces_past_a_live_cli_holder(monkeypatch):
    """Single-controller invariant: 'resume' is called without '--force', so
    a genuine live interactive CLI holder's 409 refusal is surfaced as-is --
    agent-dispatch judged the *task* stale, never that a human's own attached
    session should be torn out from under them."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _proc(
            cmd, 1,
            '{"error": "a live interactive CLI (live-7) still holds worktree '
            'wt-1", "reason": "live_cli_holds_worktree", "session_id": "live-7"}',
            "",
        )

    result = _resume(monkeypatch, fake_run)
    assert result.returncode == 1
    assert "live_cli_holds_worktree" in result.stdout
    assert len(calls) == 1  # send is never attempted
    assert "--force" not in calls[0]


def test_missing_session_id_falls_back_to_legacy_create_reclaim(monkeypatch):
    """A successful (returncode 0) resume whose stdout carries no session_id
    is a not-yet-upgraded daemon's old human ``[OK] ...`` text, not a new
    failure mode -- falls back to the legacy 'create --reclaim' invocation."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[1:3] == ["--json", "resume"]:
            return _proc(cmd, 0, "[OK] Worktree wt-1 loaded as owned session s1 (idle)")
        return _proc(cmd, 0, "legacy create ok")

    result = _resume(monkeypatch, fake_run, wait=False)
    assert result.returncode == 0
    assert len(calls) == 2
    assert calls[1] == [
        "/usr/bin/agent-bridge", "create", "--worktree-id", "wt-1", "--reclaim",
        "task-worker", "the seed", "--caller", "agent-dispatch:w1", "--no-wait",
    ]


def test_busy_reused_session_is_ended_and_retried(monkeypatch):
    """resume *reuses* an existing live session; if it is mid-turn, plain
    send refuses busy (exit 75) -- this path ends it and retries once,
    mirroring the old create --reclaim's force_new=True (which never
    deferred to an existing session at all)."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if cmd[1:3] == ["--json", "resume"]:
            return _proc(cmd, 0, json.dumps({"session_id": "reused-1"}))
        if cmd[1] == "send" and calls.count(list(cmd)) == 1:
            # First send attempt: busy.
            return _proc(cmd, bridge_reclaim._SEND_BUSY_EXIT, "", "busy")
        if cmd[1] == "end":
            return _proc(cmd, 0, "ended")
        return _proc(cmd, 0, "ok")  # the retried send

    result = _resume(monkeypatch, fake_run)
    assert result.returncode == 0
    kinds = [c[1] for c in calls]
    assert kinds == ["--json", "send", "end", "send"]
    assert calls[2] == ["/usr/bin/agent-bridge", "end", "reused-1", "--force"]


def test_json_output_reshapes_send_result(monkeypatch):
    def fake_run(cmd, **kwargs):
        if cmd[1:3] == ["--json", "resume"]:
            return _proc(cmd, 0, json.dumps({"session_id": "resumed-9"}))
        return _proc(cmd, 0, "raw send output")

    result = _resume(monkeypatch, fake_run, json_output=True)
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"session_id": "resumed-9"}

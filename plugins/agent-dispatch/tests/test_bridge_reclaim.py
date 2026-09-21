"""Tests for agent_dispatch.bridge_reclaim (the reclaim path's two-call
equivalent of the removed ``create --reclaim``, agent-bridge-cold-resume
Phase 3, #6744)."""

from __future__ import annotations

import json
import subprocess

from agent_dispatch import bridge_reclaim


def _proc(cmd, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)


def test_resume_then_send_happy_path(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[1:3] == ["--json", "resume"]:
            return _proc(cmd, 0, json.dumps({"session_id": "resumed-9"}))
        return _proc(cmd, 0, "ok")

    monkeypatch.setattr(bridge_reclaim.subprocess, "run", fake_run)

    result = bridge_reclaim.resume_worktree_and_send(
        "wt-1", "the seed", exe=["/usr/bin/agent-bridge"],
        wait=False, json_output=False, timeout=None,
    )
    assert result.returncode == 0
    assert len(calls) == 2
    assert calls[0] == ["/usr/bin/agent-bridge", "--json", "resume", "wt-1"]
    assert calls[1] == [
        "/usr/bin/agent-bridge", "send", "resumed-9", "--prompt-file", "-", "--no-wait",
    ]


def test_resume_failure_short_circuits(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _proc(cmd, 1, "", "connect refused")

    monkeypatch.setattr(bridge_reclaim.subprocess, "run", fake_run)

    result = bridge_reclaim.resume_worktree_and_send(
        "wt-1", "seed", exe=["/usr/bin/agent-bridge"],
        wait=True, json_output=False, timeout=None,
    )
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
        return _proc(cmd, 1, '{"error": "a live interactive CLI (live-7) still holds worktree wt-1", "reason": "live_cli_holds_worktree", "session_id": "live-7"}', "")

    monkeypatch.setattr(bridge_reclaim.subprocess, "run", fake_run)

    result = bridge_reclaim.resume_worktree_and_send(
        "wt-1", "seed", exe=["/usr/bin/agent-bridge"],
        wait=True, json_output=False, timeout=None,
    )
    assert result.returncode == 1
    assert "live_cli_holds_worktree" in result.stdout
    assert len(calls) == 1  # send is never attempted
    assert "--force" not in calls[0]


def test_missing_session_id_fails_without_send(monkeypatch):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _proc(cmd, 0, "{}")  # no session_id

    monkeypatch.setattr(bridge_reclaim.subprocess, "run", fake_run)

    result = bridge_reclaim.resume_worktree_and_send(
        "wt-1", "seed", exe=["/usr/bin/agent-bridge"],
        wait=True, json_output=False, timeout=None,
    )
    assert result.returncode == 1
    assert "no session_id" in result.stderr
    assert len(calls) == 1


def test_busy_reused_session_is_ended_and_retried(monkeypatch):
    """resume --force *reuses* an existing live session; if it is mid-turn,
    plain send refuses busy (exit 75) -- this path ends it and retries once,
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

    monkeypatch.setattr(bridge_reclaim.subprocess, "run", fake_run)

    result = bridge_reclaim.resume_worktree_and_send(
        "wt-1", "seed", exe=["/usr/bin/agent-bridge"],
        wait=True, json_output=False, timeout=None,
    )
    assert result.returncode == 0
    kinds = [c[1] for c in calls]
    assert kinds == ["--json", "send", "end", "send"]
    assert calls[2] == ["/usr/bin/agent-bridge", "end", "reused-1", "--force"]


def test_json_output_reshapes_send_result(monkeypatch):
    def fake_run(cmd, **kwargs):
        if cmd[1:3] == ["--json", "resume"]:
            return _proc(cmd, 0, json.dumps({"session_id": "resumed-9"}))
        return _proc(cmd, 0, "raw send output")

    monkeypatch.setattr(bridge_reclaim.subprocess, "run", fake_run)

    result = bridge_reclaim.resume_worktree_and_send(
        "wt-1", "seed", exe=["/usr/bin/agent-bridge"],
        wait=True, json_output=True, timeout=None,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"session_id": "resumed-9"}

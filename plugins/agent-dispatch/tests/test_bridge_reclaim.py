"""Tests for agent_dispatch.bridge_reclaim -- the reclaim path's replacement
for the removed ``create --reclaim`` (agent-bridge-cold-resume Phase 3,
#6744): resume, and if a live interactive CLI holds the worktree, actually
stop it (via ``agent-bridge restart-worktree``), revalidate it's gone, and
only then force the take-over."""

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


def _live_cli_refusal(cmd, holder="live-7"):
    return _proc(
        cmd, 1,
        json.dumps({
            "error": f"a live interactive CLI ({holder}) still holds worktree wt-1",
            "reason": "live_cli_holds_worktree", "session_id": holder,
        }),
        "",
    )


def _is_restart(cmd):
    return cmd[:2] == ["/usr/bin/agent-bridge", "restart-worktree"]


def _is_resume(cmd):
    return cmd[1:3] == ["--json", "resume"]


def test_resume_then_send_happy_path_no_holder(monkeypatch):
    """The common case: no live interactive CLI holds the worktree at all --
    a plain (non-forcing) resume succeeds outright, no stop is attempted."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if _is_resume(cmd):
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


def test_live_cli_holder_is_stopped_revalidated_then_force_resumed(monkeypatch):
    """The actual 'reclaim' verb: kill the interactive CLI holding the
    worktree (agent-bridge restart-worktree), REVALIDATE it's actually gone
    (the revalidation resume still names the SAME holder -- a lingering
    stale registration, not a fresh claimant), THEN force-take-over."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if _is_restart(cmd):
            return _proc(cmd, 0, json.dumps(
                {"ok": True, "had_session": True, "method": "graceful"}
            ))
        if _is_resume(cmd):
            if "--force" in cmd:
                return _proc(cmd, 0, json.dumps({"session_id": "reclaimed-1"}))
            # 1st (pre-stop) and 2nd (revalidation) both still see 'live-7'.
            return _live_cli_refusal(cmd)
        return _proc(cmd, 0, "ok")

    result = _resume(monkeypatch, fake_run)
    assert result.returncode == 0
    kinds = [(c[0], c[1] if len(c) > 1 else None) for c in calls]
    assert kinds == [
        ("/usr/bin/agent-bridge", "--json"),          # 1st resume, no --force -> 409
        ("/usr/bin/agent-bridge", "restart-worktree"),  # stop the interactive CLI
        ("/usr/bin/agent-bridge", "--json"),          # revalidation resume -> still 409
        ("/usr/bin/agent-bridge", "--json"),          # force resume -> ok
        ("/usr/bin/agent-bridge", "send"),
    ]
    assert calls[1] == [
        "/usr/bin/agent-bridge", "restart-worktree", "wt-1", "--json",
        "--expected-holder", "live-7",
    ]
    assert "--force" not in calls[2]
    assert "--force" in calls[3]
    assert "reclaimed-1" in calls[4]


def test_revalidation_succeeding_needs_no_force_at_all(monkeypatch):
    """If the revalidation resume (right after the stop) succeeds outright,
    the stale registration already cleared on its own -- no --force needed."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if _is_restart(cmd):
            return _proc(cmd, 0, json.dumps({"ok": True, "had_session": True}))
        if _is_resume(cmd):
            if calls.count(list(cmd)) == 1:
                return _live_cli_refusal(cmd)
            return _proc(cmd, 0, json.dumps({"session_id": "cleared-1"}))
        return _proc(cmd, 0, "ok")

    result = _resume(monkeypatch, fake_run)
    assert result.returncode == 0
    assert not any("--force" in c for c in calls)
    assert "cleared-1" in calls[-1]


def test_different_holder_after_stop_refuses_to_force(monkeypatch):
    """A DIFFERENT live interactive CLI claimed the worktree in the race
    window between the stop returning and the revalidation check -- this
    must refuse to force through it, since it never confirmed THAT process
    dead."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if _is_restart(cmd):
            return _proc(cmd, 0, json.dumps({"ok": True, "had_session": True}))
        if _is_resume(cmd):
            if calls.count(list(cmd)) == 1:
                return _live_cli_refusal(cmd, holder="live-7")
            return _live_cli_refusal(cmd, holder="new-claimant-2")
        return _proc(cmd, 0, "ok")

    result = _resume(monkeypatch, fake_run)
    assert result.returncode == 1
    assert "new-claimant-2" in result.stderr
    assert "live-7" in result.stderr
    assert not any("--force" in c for c in calls)


def test_stop_failure_is_reported_without_forcing_through(monkeypatch):
    """If the stop can't confirm the interactive CLI actually stopped, this
    must NOT fall through to '--force' anyway -- that would risk the exact
    duplicate-controller race the guard exists to prevent."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if _is_restart(cmd):
            return _proc(cmd, 0, json.dumps({"ok": False, "error": "quit hung"}))
        if _is_resume(cmd):
            return _live_cli_refusal(cmd)
        return _proc(cmd, 0, "ok")

    result = _resume(monkeypatch, fake_run)
    assert result.returncode == 1
    assert "could not stop the interactive CLI" in result.stderr
    assert len(calls) == 2  # one resume attempt, one stop attempt -- never a 2nd resume
    assert not any("--force" in c for c in calls)


def test_no_mux_session_to_stop_refuses_to_force(monkeypatch):
    """``ok:true, had_session:false`` means restart found no mux session to
    stop -- never proof the live_cli_holds_worktree claimant (possibly a
    bare, un-muxed CLI) was actually terminated. Must refuse to force
    through it, the same as an outright stop failure."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if _is_restart(cmd):
            return _proc(cmd, 0, json.dumps(
                {"ok": True, "had_session": False, "method": "none"}
            ))
        if _is_resume(cmd):
            return _live_cli_refusal(cmd)
        return _proc(cmd, 0, "ok")

    result = _resume(monkeypatch, fake_run)
    assert result.returncode == 1
    assert "no mux session to stop" in result.stderr
    assert len(calls) == 2  # one resume attempt, one stop attempt -- never a 2nd resume
    assert not any("--force" in c for c in calls)


def test_missing_holder_session_id_refuses_before_attempting_a_stop(monkeypatch):
    """A ``live_cli_holds_worktree`` refusal with no usable (missing/non-
    string) ``session_id`` must be refused outright -- attempting the stop
    anyway would call restart-worktree with no --expected-holder (an
    unfenced restart that could invalidate a different claimant's
    registration)."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _proc(
            cmd, 1,
            json.dumps({
                "error": "a live interactive CLI still holds worktree wt-1",
                "reason": "live_cli_holds_worktree", "session_id": None,
            }),
        )

    result = _resume(monkeypatch, fake_run)
    assert result.returncode == 1
    assert "no usable holder session id" in result.stderr
    assert len(calls) == 1  # never attempts the stop at all
    assert not any(_is_restart(c) for c in calls)


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
        if _is_resume(cmd):
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


def test_failed_busy_session_deletion_is_reported_not_silently_reused(monkeypatch):
    """If 'end --force' fails, the busy session is still live -- resuming
    again and sending would just reuse and re-deliver to the EXACT session
    this take-over was supposed to replace. Must report the failure instead
    of proceeding as if the deletion had worked."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        if _is_resume(cmd):
            return _proc(cmd, 0, json.dumps({"session_id": "reused-1"}))
        if cmd[1] == "send":
            return _proc(cmd, bridge_reclaim._SEND_BUSY_EXIT, "", "busy")
        if cmd[1] == "end":
            return _proc(cmd, 1, "", "no such session")
        return _proc(cmd, 0, "ok")

    result = _resume(monkeypatch, fake_run)
    assert result.returncode == 1
    assert "could not end the busy session" in result.stderr
    assert "reused-1" in result.stderr
    kinds = [c[1] for c in calls]
    assert kinds == ["--json", "send", "end"]  # never a 2nd resume/send


def test_json_output_reshapes_send_result(monkeypatch):
    def fake_run(cmd, **kwargs):
        if _is_resume(cmd):
            return _proc(cmd, 0, json.dumps({"session_id": "resumed-9"}))
        return _proc(cmd, 0, "raw send output")

    result = _resume(monkeypatch, fake_run, json_output=True)
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"session_id": "resumed-9"}

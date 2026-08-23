"""Tests for the reap-audit runtime log (deliberate-reap-discipline foundation)."""

from __future__ import annotations

import json

import pytest

from agent_worktrees import reap_audit


@pytest.fixture()
def audit_home(tmp_path, monkeypatch):
    """Point the audit log at a temp HOME and force-enable it."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("AGENT_WORKTREES_REAP_AUDIT", "1")
    # Clear correlation env so tests assert on a clean record.
    for k in ("WORKTREE_LAUNCH_ID", "AGENT_WORKTREES_BIND_WORKTREE_ID",
              "AGENT_WORKTREES_BIND_SESSION_ID"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


def _read(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_records_reap_with_caller_and_argv(audit_home):
    def a_sweep_that_reaps():
        reap_audit.record("mux-session", "wt-demo", reason="kill_tmux_session",
                          killed=True, worktree_id="demo")

    a_sweep_that_reaps()

    recs = _read(reap_audit.log_path())
    assert len(recs) == 1
    rec = recs[0]
    assert rec["kind"] == "mux-session"
    assert rec["target"] == "wt-demo"
    assert rec["killed"] is True
    assert rec["reason"] == "kill_tmux_session"
    assert rec["worktree_id"] == "demo"
    assert "argv" in rec and isinstance(rec["argv"], list)
    assert "pid" in rec and "ppid" in rec
    # The caller chain must name the sweep that decided to reap, not the
    # reap plumbing (reap_audit/procs/sessions frames are trimmed).
    assert any("a_sweep_that_reaps" in frame for frame in rec["caller"])


def test_disabled_switch_writes_nothing(audit_home, monkeypatch):
    monkeypatch.setenv("AGENT_WORKTREES_REAP_AUDIT", "0")
    reap_audit.record("pid", 4321, reason="terminate_pid", killed=True)
    assert not reap_audit.log_path().exists()


def test_correlation_env_threaded_into_record(audit_home, monkeypatch):
    monkeypatch.setenv("AGENT_WORKTREES_BIND_WORKTREE_ID", "host-env-abc123")
    monkeypatch.setenv("WORKTREE_LAUNCH_ID", "abc123")
    reap_audit.record("pid", 999, reason="terminate_pid", killed=False)
    rec = _read(reap_audit.log_path())[0]
    assert rec["ctx_worktree_id"] == "host-env-abc123"
    assert rec["launch_id"] == "abc123"


def test_never_raises_on_bad_target(audit_home):
    # A non-JSON-serializable target must not raise (default=str) and must
    # still be swallowed if anything goes wrong.
    class Weird:
        def __repr__(self):
            return "weird-target"

    reap_audit.record("pid", Weird(), reason="terminate_pid", killed=True)
    rec = _read(reap_audit.log_path())[0]
    assert rec["target"] == "weird-target"

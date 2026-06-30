"""Tests for the worktree interactive-Copilot restart primitive.

``restart_worktree_copilot`` is the shared primitive behind the Picker "Restart"
maintenance action and Neuron-Forge "Take over": it stops a worktree's
interactive Copilot (graceful double-Ctrl-C, then hard mux kill-session) while
keeping the git worktree, so the caller can relaunch or ACP-resume.
"""

from __future__ import annotations

from unittest.mock import patch

from agent_worktrees import sessions


# -- restart_worktree_copilot -------------------------------------------------

def test_restart_no_session_is_noop():
    with patch.object(sessions, "has_mux_session", return_value=False):
        out = sessions.restart_worktree_copilot("wt-1")
    assert out == {
        "worktree_id": "wt-1", "had_session": False,
        "method": "none", "ok": True,
    }


def test_restart_graceful_success():
    with patch.object(sessions, "has_mux_session", return_value=True), \
         patch.object(sessions, "graceful_quit_mux_session", return_value=True), \
         patch.object(sessions, "kill_tmux_session") as kill:
        out = sessions.restart_worktree_copilot("wt-2")
    assert out["method"] == "graceful"
    assert out["ok"] is True
    kill.assert_not_called()  # graceful succeeded -> never hard-kill


def test_restart_graceful_falls_back_to_hard():
    with patch.object(sessions, "has_mux_session", return_value=True), \
         patch.object(sessions, "graceful_quit_mux_session", return_value=False), \
         patch.object(sessions, "kill_tmux_session", return_value=True) as kill:
        out = sessions.restart_worktree_copilot("wt-3")
    assert out["method"] == "hard"
    assert out["ok"] is True
    kill.assert_called_once_with("wt-3")


def test_restart_no_graceful_hard_kills_directly():
    with patch.object(sessions, "has_mux_session", return_value=True), \
         patch.object(sessions, "graceful_quit_mux_session") as graceful, \
         patch.object(sessions, "kill_tmux_session", return_value=True):
        out = sessions.restart_worktree_copilot("wt-4", graceful=False)
    assert out["method"] == "hard"
    graceful.assert_not_called()


def test_restart_hard_kill_failure_reports_failed():
    with patch.object(sessions, "has_mux_session", return_value=True), \
         patch.object(sessions, "graceful_quit_mux_session", return_value=False), \
         patch.object(sessions, "kill_tmux_session", return_value=False):
        out = sessions.restart_worktree_copilot("wt-5")
    assert out["method"] == "failed"
    assert out["ok"] is False


# -- graceful_quit_mux_session ------------------------------------------------

def test_graceful_quit_no_session_returns_true():
    with patch.object(sessions, "has_mux_session", return_value=False), \
         patch.object(sessions, "_mux_send_keys") as send:
        assert sessions.graceful_quit_mux_session("wt-6") is True
    send.assert_not_called()


def test_graceful_quit_double_ctrl_c_then_session_drops():
    # has_mux_session: True at start, then False after the double Ctrl-C.
    states = iter([True, False])
    with patch.object(sessions, "has_mux_session", side_effect=lambda _id: next(states)), \
         patch.object(sessions, "_mux_send_keys", return_value=True) as send, \
         patch("time.sleep"):
        ok = sessions.graceful_quit_mux_session("wt-7", settle_timeout=2.0)
    assert ok is True
    # Two Ctrl-C key sends (the double-Ctrl-C quit pattern).
    assert send.call_count == 2
    assert all(call.args[1] == "C-c" for call in send.call_args_list)


def test_graceful_quit_times_out_when_session_persists():
    # Session never drops: start True, and stays True through the poll loop.
    monotonic_vals = iter([0.0, 0.0, 0.5, 1.0, 1.5, 2.0, 2.5])
    with patch.object(sessions, "has_mux_session", return_value=True), \
         patch.object(sessions, "_mux_send_keys", return_value=True), \
         patch("time.sleep"), \
         patch("time.monotonic", side_effect=lambda: next(monotonic_vals)):
        ok = sessions.graceful_quit_mux_session("wt-8", settle_timeout=2.0)
    assert ok is False


def test_graceful_quit_send_fails_but_session_already_gone():
    # First send-keys fails (mux vanished); a re-check shows it's gone -> True.
    states = iter([True, False])
    with patch.object(sessions, "has_mux_session", side_effect=lambda _id: next(states)), \
         patch.object(sessions, "_mux_send_keys", return_value=False):
        assert sessions.graceful_quit_mux_session("wt-9") is True


# -- _mux_send_keys target form (regression: =wt-X is rejected by send-keys) ---

def test_mux_send_keys_tmux_target_is_exact_pane_form():
    # Guards the bug where `tmux send-keys -t =wt-<id>` failed with
    # "can't find pane": send-keys needs the `:`-suffixed pane target.
    with patch.object(sessions.platform, "system", return_value="Linux"), \
         patch("subprocess.run") as run:
        run.return_value.returncode = 0
        sessions._mux_send_keys("abc", "C-c")
    cmd = run.call_args.args[0]
    assert cmd[0] == "tmux"
    assert "send-keys" in cmd
    # exact-session match (=) AND a pane target (trailing :) -- not bare =wt-abc.
    assert "=wt-abc:" in cmd
    assert "C-c" in cmd


def test_mux_send_keys_windows_uses_psmux():
    with patch.object(sessions.platform, "system", return_value="Windows"), \
         patch("subprocess.run") as run:
        run.return_value.returncode = 0
        sessions._mux_send_keys("abc", "C-c")
    cmd = run.call_args.args[0]
    assert cmd[0] == "psmux"
    assert "wt-abc" in cmd

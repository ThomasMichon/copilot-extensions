"""Tests for the worktree interactive-Copilot restart primitive.

``restart_worktree_copilot`` is the shared primitive behind the Picker "Stop"
maintenance action and Neuron-Forge "Take over": it stops a worktree's
interactive Copilot (active-pane retire first, then whole-session hard kill
only when needed) while keeping the git worktree, so the caller can relaunch or
ACP-resume.
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
         patch.object(sessions, "mux_active_pane", return_value="%2"), \
         patch.object(sessions, "mux_session_name", return_value="wt-wt-2"), \
         patch("agent_worktrees.pane_lifecycle.pane_terminate", return_value={
             "ok": True, "pane": "%2", "gone": True,
             "method": "graceful-signature-confirmed",
         }) as terminate, \
         patch.object(sessions, "kill_tmux_session") as kill:
        out = sessions.restart_worktree_copilot("wt-2")
    assert out["method"] == "graceful"
    assert out["ok"] is True
    terminate.assert_called_once_with(
        "%2", mux_session="wt-wt-2", overall_budget=7.5,
    )
    kill.assert_not_called()  # graceful succeeded -> never hard-kill


def test_restart_graceful_falls_back_to_hard():
    with patch.object(sessions, "has_mux_session", return_value=True), \
         patch.object(sessions, "mux_active_pane", return_value="%3"), \
         patch.object(sessions, "mux_session_name", return_value="wt-wt-3"), \
         patch("agent_worktrees.pane_lifecycle.pane_terminate", return_value={
             "ok": False, "pane": "%3", "gone": False, "method": "failed",
         }), \
         patch.object(sessions, "kill_tmux_session", return_value=True) as kill:
        out = sessions.restart_worktree_copilot("wt-3")
    assert out["method"] == "hard"
    assert out["ok"] is True
    kill.assert_called_once_with("wt-3")


def test_restart_last_window_skip_falls_back_to_session_kill():
    with patch.object(sessions, "has_mux_session", return_value=True), \
         patch.object(sessions, "mux_active_pane", return_value="%4"), \
         patch.object(sessions, "mux_session_name", return_value="wt-wt-4"), \
         patch("agent_worktrees.pane_lifecycle.pane_terminate", return_value={
             "ok": True, "pane": "%4", "gone": False,
             "method": "last-window-skip",
         }), \
         patch.object(sessions, "kill_tmux_session", return_value=True) as kill:
        out = sessions.restart_worktree_copilot("wt-4")
    assert out["method"] == "hard"
    assert out["ok"] is True
    kill.assert_called_once_with("wt-4")


def test_restart_pane_hard_success_does_not_kill_session_again():
    with patch.object(sessions, "has_mux_session", return_value=True), \
         patch.object(sessions, "mux_active_pane", return_value="%5"), \
         patch.object(sessions, "mux_session_name", return_value="wt-wt-5"), \
         patch("agent_worktrees.pane_lifecycle.pane_terminate", return_value={
             "ok": True, "pane": "%5", "gone": True, "method": "hard",
         }), \
         patch.object(sessions, "kill_tmux_session") as kill:
        out = sessions.restart_worktree_copilot("wt-5")
    assert out["method"] == "hard"
    assert out["ok"] is True
    kill.assert_not_called()


def test_restart_no_graceful_hard_kills_directly():
    with patch.object(sessions, "has_mux_session", return_value=True), \
         patch("agent_worktrees.pane_lifecycle.pane_terminate") as terminate, \
         patch.object(sessions, "kill_tmux_session", return_value=True):
        out = sessions.restart_worktree_copilot("wt-4", graceful=False)
    assert out["method"] == "hard"
    terminate.assert_not_called()


def test_restart_hard_kill_failure_reports_failed():
    with patch.object(sessions, "has_mux_session", return_value=True), \
         patch.object(sessions, "mux_active_pane", return_value="%6"), \
         patch.object(sessions, "mux_session_name", return_value="wt-wt-6"), \
         patch("agent_worktrees.pane_lifecycle.pane_terminate", return_value={
             "ok": False, "pane": "%6", "gone": False, "method": "failed",
         }), \
         patch.object(sessions, "kill_tmux_session", return_value=False):
        out = sessions.restart_worktree_copilot("wt-6")
    assert out["method"] == "failed"
    assert out["ok"] is False


def test_restart_already_gone_without_live_session_maps_to_none():
    with patch.object(sessions, "has_mux_session", side_effect=[True, False]), \
         patch.object(sessions, "mux_active_pane", return_value="%7"), \
         patch.object(sessions, "mux_session_name", return_value="wt-wt-7"), \
         patch("agent_worktrees.pane_lifecycle.pane_terminate", return_value={
             "ok": True, "pane": "%7", "gone": True, "method": "already-gone",
         }), \
         patch.object(sessions, "kill_tmux_session") as kill:
        out = sessions.restart_worktree_copilot("wt-7")
    assert out == {
        "worktree_id": "wt-7", "had_session": False,
        "method": "none", "ok": True,
    }
    kill.assert_not_called()


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
    # Session never drops: stays alive through both poll windows. Uses a
    # monotonically advancing clock so both _dropped_within loops terminate.
    clock = {"t": 0.0}

    def _mono():
        clock["t"] += 0.4
        return clock["t"]

    with patch.object(sessions, "has_mux_session", return_value=True), \
         patch.object(sessions, "_mux_send_keys", return_value=True) as send, \
         patch("time.sleep"), \
         patch("time.monotonic", side_effect=_mono):
        ok = sessions.graceful_quit_mux_session("wt-8", settle_timeout=2.0)
    assert ok is False
    # Full escalation ladder fired: two, then the conditional third Ctrl-C.
    assert send.call_count == 3
    assert all(call.args[1] == "C-c" for call in send.call_args_list)


def test_graceful_quit_third_ctrl_c_when_two_dont_land():
    # Alive until the third Ctrl-C lands (the first two are swallowed), then
    # the session drops -> graceful success with the full three-interrupt burst.
    sent = {"n": 0}

    def _send(_id, _keys):
        sent["n"] += 1
        return True

    def _alive(_id):
        return sent["n"] < 3  # stays alive until the conditional third Ctrl-C

    clock = {"t": 0.0}

    def _mono():
        clock["t"] += 1.0  # advance a full second per poll step
        return clock["t"]

    with patch.object(sessions, "has_mux_session", side_effect=_alive), \
         patch.object(sessions, "_mux_send_keys", side_effect=_send) as send, \
         patch("time.sleep"), \
         patch("time.monotonic", side_effect=_mono):
        ok = sessions.graceful_quit_mux_session(
            "wt-esc", settle_timeout=6.0, escalate_after=1.5,
        )
    assert ok is True
    assert send.call_count == 3  # two, then the conditional third
    assert all(call.args[1] == "C-c" for call in send.call_args_list)


def test_graceful_quit_no_third_when_two_suffice():
    # Drops during the brief escalate_after window -> only two Ctrl-C, no
    # escalation to a third.
    states = iter([True, False])
    with patch.object(sessions, "has_mux_session", side_effect=lambda _id: next(states)), \
         patch.object(sessions, "_mux_send_keys", return_value=True) as send, \
         patch("time.sleep"):
        ok = sessions.graceful_quit_mux_session("wt-two", settle_timeout=4.0)
    assert ok is True
    assert send.call_count == 2  # second interrupt sufficed; no third sent


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

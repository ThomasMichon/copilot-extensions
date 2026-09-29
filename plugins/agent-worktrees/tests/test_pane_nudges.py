"""Unit tests for :mod:`agent_worktrees.pane_nudges` in isolation from the
full :func:`agent_worktrees.sessions.mux_seed_pane` polling loop (covered
separately in ``test_sessions.py``)."""

from unittest.mock import patch

from agent_worktrees import pane_nudges

_DESKTOP_APP_NUDGE = (
    "the CLI, in a GitHub-native desktop app built for managing parallel\n"
    "agents. Install it now?\n"
    "❯ Yes, install      No, thanks\n"
    "←/→ to choose · Enter to select · Y / N\n"
)


def test_is_desktop_app_nudge_matches_known_dialog():
    assert pane_nudges.is_desktop_app_nudge(_DESKTOP_APP_NUDGE) is True


def test_is_desktop_app_nudge_ignores_unrelated_capture():
    assert pane_nudges.is_desktop_app_nudge("❯ ") is False
    # A real, unrelated selection dialog (e.g. an extension-permission
    # prompt) must never be mistaken for this specific nudge.
    assert pane_nudges.is_desktop_app_nudge(
        "Extension wants elevated permissions\n"
        "❯ 1. Yes\n"
        "  2. No\n"
        "enter to select\n"
    ) is False


def test_dismiss_sends_escape_to_the_pane():
    with patch("subprocess.run") as run:
        pane_nudges.dismiss("tmux", "%3")
    run.assert_called_once_with(
        ["tmux", "send-keys", "-t", "%3", "Escape"],
        capture_output=True, timeout=5,
    )


def test_dismiss_swallows_a_failed_send():
    import subprocess

    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("tmux", 5)):
        pane_nudges.dismiss("tmux", "%3")  # must not raise

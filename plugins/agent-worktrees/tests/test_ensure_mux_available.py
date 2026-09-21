"""Tests for agent_worktrees.sessions.ensure_mux_available -- the best-effort
self-heal of a missing tmux binary (agent-bridge-cli-mode-sessions Phase 4
prep: a freshly-provisioned remote venue, e.g. a CodeSpace devcontainer,
confirmed to have Copilot but no tmux preinstalled).
"""

from __future__ import annotations

from unittest.mock import patch

from agent_worktrees import sessions


def _which_only(*present: str):
    def _fake_which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name in present else None

    return _fake_which


def test_returns_true_immediately_when_mux_already_available() -> None:
    with patch("agent_worktrees.sessions.mux_available", return_value=True):
        with patch("shutil.which") as which:
            assert sessions.ensure_mux_available() is True
            which.assert_not_called()


def test_returns_false_on_windows_without_attempting_install() -> None:
    with (
        patch("agent_worktrees.sessions.mux_available", return_value=False),
        patch("platform.system", return_value="Windows"),
        patch("subprocess.run") as run,
    ):
        assert sessions.ensure_mux_available() is False
        run.assert_not_called()


def test_returns_false_when_no_package_manager_or_sudo_present() -> None:
    with (
        patch("agent_worktrees.sessions.mux_available", return_value=False),
        patch("platform.system", return_value="Linux"),
        patch("os.geteuid", return_value=1000, create=True),
        patch("shutil.which", side_effect=_which_only()),
        patch("subprocess.run") as run,
    ):
        assert sessions.ensure_mux_available() is False
        run.assert_not_called()


def test_installs_via_apt_get_with_sudo_when_not_root() -> None:
    calls: list[list[str]] = []

    def _fake_run(argv, **kwargs):
        calls.append(list(argv))
        return None

    # First mux_available() (pre-check) -> False; after the install attempt,
    # a second mux_available() call (post-install verification) -> True.
    with (
        patch(
            "agent_worktrees.sessions.mux_available", side_effect=[False, True],
        ),
        patch("platform.system", return_value="Linux"),
        patch("os.geteuid", return_value=1000, create=True),
        patch("shutil.which", side_effect=_which_only("sudo", "apt-get")),
        patch("subprocess.run", side_effect=_fake_run),
    ):
        assert sessions.ensure_mux_available() is True
    assert calls == [["/usr/bin/sudo", "-n", "apt-get", "install", "-y", "tmux"]]


def test_installs_directly_when_already_root() -> None:
    calls: list[list[str]] = []

    def _fake_run(argv, **kwargs):
        calls.append(list(argv))
        return None

    with (
        patch(
            "agent_worktrees.sessions.mux_available", side_effect=[False, True],
        ),
        patch("platform.system", return_value="Linux"),
        patch("os.geteuid", return_value=0, create=True),
        patch("shutil.which", side_effect=_which_only("apt-get")),
        patch("subprocess.run", side_effect=_fake_run),
    ):
        assert sessions.ensure_mux_available() is True
    assert calls == [["apt-get", "install", "-y", "tmux"]]


def test_falls_through_remaining_candidates_after_a_failed_install() -> None:
    calls: list[list[str]] = []

    def _fake_run(argv, **kwargs):
        calls.append(list(argv))
        return None

    # apt-get "succeeds" (no exception) but tmux still isn't there afterward
    # -- must try dnf next rather than stopping after the first attempt.
    with (
        patch(
            "agent_worktrees.sessions.mux_available",
            side_effect=[False, False, True],
        ),
        patch("platform.system", return_value="Linux"),
        patch("os.geteuid", return_value=0, create=True),
        patch("shutil.which", side_effect=_which_only("apt-get", "dnf")),
        patch("subprocess.run", side_effect=_fake_run),
    ):
        assert sessions.ensure_mux_available() is True
    assert calls == [
        ["apt-get", "install", "-y", "tmux"],
        ["dnf", "install", "-y", "tmux"],
    ]


def test_returns_false_when_every_candidate_fails() -> None:
    with (
        patch("agent_worktrees.sessions.mux_available", return_value=False),
        patch("platform.system", return_value="Linux"),
        patch("os.geteuid", return_value=0, create=True),
        patch("shutil.which", side_effect=_which_only("apt-get", "dnf")),
        patch("subprocess.run", return_value=None),
    ):
        assert sessions.ensure_mux_available() is False


def test_never_raises_on_a_subprocess_error() -> None:
    with (
        patch(
            "agent_worktrees.sessions.mux_available", side_effect=[False, False],
        ),
        patch("platform.system", return_value="Linux"),
        patch("os.geteuid", return_value=0, create=True),
        patch("shutil.which", side_effect=_which_only("apt-get")),
        patch("subprocess.run", side_effect=OSError("no such program")),
    ):
        assert sessions.ensure_mux_available() is False

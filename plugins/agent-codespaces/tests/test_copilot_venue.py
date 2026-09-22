"""Tests for `agent-codespaces copilot <name>` (agent-bridge-cli-mode-sessions
Phase 4: the venue counterpart to `agent-worktrees copilot`, PR #3126).

Drives ``copilot_venue.cmd_copilot`` with the external seams mocked -- the
Connection Owner hold/heartbeat/release, ``venue_copilot``'s reserve/connect/
release orchestration, and the interactive SSH connect itself -- so the wiring
between them is exercised end-to-end without a real CodeSpace.
"""
from __future__ import annotations

import argparse
from unittest.mock import patch

import pytest
from agent_codespaces import config as config_mod
from agent_codespaces import connection_owner as owner
from agent_codespaces import copilot_venue
from agent_codespaces.__main__ import _interactive_ssh


class TestInteractiveSshReverseForwardsAndRemoteCommand:
    """Regression coverage for a real bug found live (agent-bridge-cli-mode-
    sessions Phase 4 validation): a bare reverse-forward spec with no ``-R``
    flag was appended after its own ``--`` separator, so `gh codespace ssh`
    handed ssh a bare ``port:127.0.0.1:port`` string with nothing to mark it
    as an option -- ssh then treated it as the REMOTE COMMAND to run
    (`bash: line 1: 51234:127.0.0.1:51234: command not found`) instead of an
    actual reverse forward.
    """

    def test_forwards_use_proper_dash_r_flag_pairs(self) -> None:
        with (
            patch("agent_codespaces.lifecycle.account_for_codespace", return_value=None),
            patch("subprocess.call", return_value=0) as call,
        ):
            assert _interactive_ssh(
                "cs-example", ["51234:127.0.0.1:51234", "9280:127.0.0.1:9280"],
            ) == 0
        call.assert_called_once_with(
            [
                "gh", "codespace", "ssh", "-c", "cs-example", "--",
                "-R", "51234:127.0.0.1:51234",
                "-R", "9280:127.0.0.1:9280",
            ],
            env=None,
        )

    def test_remote_command_appended_after_forwards(self) -> None:
        with (
            patch("agent_codespaces.lifecycle.account_for_codespace", return_value=None),
            patch("subprocess.call", return_value=0) as call,
        ):
            assert _interactive_ssh(
                "cs-example", ["51234:127.0.0.1:51234"],
                remote_command="agent-worktrees copilot --worktree-id wt-A",
            ) == 0
        call.assert_called_once_with(
            [
                "gh", "codespace", "ssh", "-c", "cs-example", "--", "-t",
                "-R", "51234:127.0.0.1:51234",
                "agent-worktrees copilot --worktree-id wt-A",
            ],
            env=None,
        )

    def test_remote_command_alone_still_gets_a_single_separator(self) -> None:
        with (
            patch("agent_codespaces.lifecycle.account_for_codespace", return_value=None),
            patch("subprocess.call", return_value=0) as call,
        ):
            assert _interactive_ssh(
                "cs-example", [], remote_command="echo hi",
            ) == 0
        call.assert_called_once_with(
            ["gh", "codespace", "ssh", "-c", "cs-example", "--", "-t", "echo hi"],
            env=None,
        )

    def test_remote_command_requests_a_pty(self) -> None:
        """Regression coverage for a second real bug found live (this
        session, agent-bridge-cli-mode-sessions Phase 4 live validation
        against a real odsp-web CodeSpace): the venue `copilot` verb's own
        docstring and CLI help text document "SSHes -t in", but no `-t`
        flag was ever actually added -- without one, ssh never allocates a
        remote pty for a command invocation, so the remote
        `agent-worktrees copilot` immediately refused with "needs a
        controlling terminal to attach to", 100% reproducible. An ordinary
        interactive connect (no remote command) is unaffected -- `gh
        codespace ssh` already allocates a pty for that case on its own.
        """
        with (
            patch("agent_codespaces.lifecycle.account_for_codespace", return_value=None),
            patch("subprocess.call", return_value=0) as call,
        ):
            _interactive_ssh(
                "cs-example", [], remote_command="agent-worktrees copilot --worktree-id wt-A",
            )
        argv = call.call_args.args[0]
        assert "-t" in argv
        assert argv.index("-t") > argv.index("--")

    def test_no_forwards_or_command_keeps_prior_bare_argv(self) -> None:
        """Existing behavior for a plain interactive connect is unchanged."""
        with (
            patch("agent_codespaces.lifecycle.account_for_codespace", return_value=None),
            patch("subprocess.call", return_value=0) as call,
        ):
            assert _interactive_ssh("cs-example", []) == 0
        call.assert_called_once_with(
            ["gh", "codespace", "ssh", "-c", "cs-example"], env=None,
        )


@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(owner, "OWNER_FILE", tmp_path / "connection-owner.json")
    monkeypatch.setattr(owner, "_LOCK_FILE", tmp_path / "connection-owner.lock")
    monkeypatch.setattr(owner, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(owner, "ensure_runtime_dir", lambda: None)
    return tmp_path


def _ns(**kw):
    defaults = dict(
        name="cs-1",
        worktree_id="wt-A",
        driver="cli-mode",
        seed=None,
        ttl_seconds=300.0,
        ensure_mux=True,
        no_relay=False,
    )
    defaults.update(kw)
    return argparse.Namespace(**defaults)


class TestCmdCopilotAnchorDefault:
    """Omitting --worktree-id defaults to anchor mode: a CodeSpace is
    conventionally anchor-only (the devcontainer already clones the repo
    directly), matching headless ACP dispatch's own existing behavior of
    running straight in the workspace_folder -- so this should be the
    zero-ceremony default, not something an operator has to opt into."""

    def test_no_worktree_id_resolves_anchor_identity_and_forwards_anchor(
        self, store, monkeypatch,
    ) -> None:
        monkeypatch.setattr(owner, "ensure_owner_running", lambda config: True)

        class _Config:
            def resolved_workspace_folder_for(self, repo):
                assert repo == "octo/example-web-codespaces"
                return "/workspaces/example-web"

        monkeypatch.setattr(config_mod, "load_merged_config", lambda: _Config())
        monkeypatch.setattr(
            "agent_codespaces.lifecycle.list_codespaces",
            lambda: [
                argparse.Namespace(name="cs-1", repository="octo/example-web-codespaces"),
            ],
        )

        seen = {}

        def fake_run_venue_copilot(identity, *, connect, anchor=False, **kwargs):
            seen["identity"] = identity
            seen["anchor"] = anchor
            return connect("agent-worktrees copilot --anchor")

        monkeypatch.setattr("venue_copilot.run_venue_copilot", fake_run_venue_copilot)
        monkeypatch.setattr("venue_copilot.resolve_daemon_port", lambda: None)
        monkeypatch.setattr(
            "agent_codespaces.relay_launch.effective_relay_port", lambda config: 9857,
        )
        monkeypatch.setattr(
            "agent_codespaces.relay_token.token_for", lambda name: "tok-1",
        )

        rc = copilot_venue.cmd_copilot(
            _ns(worktree_id=None), interactive_ssh=lambda *a, **kw: 0,
        )

        assert rc == 0
        assert seen == {"identity": "anchor-example-web", "anchor": True}

    def test_explicit_worktree_id_still_wins_over_anchor_default(
        self, store, monkeypatch,
    ) -> None:
        monkeypatch.setattr(owner, "ensure_owner_running", lambda config: True)
        monkeypatch.setattr(config_mod, "load_merged_config", lambda: object())
        monkeypatch.setattr(
            "agent_codespaces.lifecycle.list_codespaces",
            lambda: (_ for _ in ()).throw(
                AssertionError("must not resolve anchor identity when --worktree-id is given")
            ),
        )

        seen = {}

        def fake_run_venue_copilot(identity, *, connect, anchor=False, **kwargs):
            seen["identity"] = identity
            seen["anchor"] = anchor
            return connect("agent-worktrees copilot --worktree-id wt-A")

        monkeypatch.setattr("venue_copilot.run_venue_copilot", fake_run_venue_copilot)
        monkeypatch.setattr("venue_copilot.resolve_daemon_port", lambda: None)
        monkeypatch.setattr(
            "agent_codespaces.relay_launch.effective_relay_port", lambda config: 9857,
        )
        monkeypatch.setattr(
            "agent_codespaces.relay_token.token_for", lambda name: "tok-1",
        )

        rc = copilot_venue.cmd_copilot(_ns(), interactive_ssh=lambda *a, **kw: 0)

        assert rc == 0
        assert seen == {"identity": "wt-A", "anchor": False}

    def test_places_and_releases_owner_hold_around_a_successful_run(
        self, store, monkeypatch,
    ) -> None:
        monkeypatch.setattr(owner, "ensure_owner_running", lambda config: True)
        monkeypatch.setattr(config_mod, "load_merged_config", lambda: object())

        seen_reverse_forwards: list[str] = []
        connect_calls: list[str] = []

        def fake_run_venue_copilot(worktree_id, *, connect, **kwargs):
            assert worktree_id == "wt-A"
            connect_calls.append("called")
            return connect("agent-worktrees copilot --worktree-id wt-A")

        def fake_interactive_ssh(name, forwards, relay_port=None, relay_token=None,
                                  remote_command=None):
            seen_reverse_forwards.extend(forwards)
            assert remote_command == "agent-worktrees copilot --worktree-id wt-A"
            return 0

        monkeypatch.setattr(
            "venue_copilot.run_venue_copilot", fake_run_venue_copilot,
        )
        monkeypatch.setattr(
            "venue_copilot.resolve_daemon_port", lambda: 9280,
        )
        monkeypatch.setattr(
            "agent_codespaces.relay_launch.effective_relay_port", lambda config: 9857,
        )
        monkeypatch.setattr(
            "agent_codespaces.relay_token.token_for", lambda name: "tok-1",
        )

        holds_before = owner._read_holds()
        assert "cs-1" not in holds_before

        rc = copilot_venue.cmd_copilot(_ns(), interactive_ssh=fake_interactive_ssh)

        assert rc == 0
        assert connect_calls == ["called"]
        assert "9857:127.0.0.1:9857" in seen_reverse_forwards
        assert "9280:127.0.0.1:9280" in seen_reverse_forwards
        # The hold is released once the interactive session ends.
        assert "cs-1" not in owner._read_holds()

    def test_releases_hold_even_when_connect_raises(self, store, monkeypatch) -> None:
        monkeypatch.setattr(owner, "ensure_owner_running", lambda config: True)
        monkeypatch.setattr(config_mod, "load_merged_config", lambda: object())

        def fake_run_venue_copilot(worktree_id, *, connect, **kwargs):
            return connect("agent-worktrees copilot --worktree-id wt-A")

        def fake_interactive_ssh(*a, **kw):
            raise RuntimeError("ssh dropped")

        monkeypatch.setattr("venue_copilot.run_venue_copilot", fake_run_venue_copilot)
        monkeypatch.setattr("venue_copilot.resolve_daemon_port", lambda: None)
        monkeypatch.setattr(
            "agent_codespaces.relay_launch.effective_relay_port", lambda config: 9857,
        )
        monkeypatch.setattr(
            "agent_codespaces.relay_token.token_for", lambda name: "tok-1",
        )

        with pytest.raises(RuntimeError, match="ssh dropped"):
            copilot_venue.cmd_copilot(_ns(), interactive_ssh=fake_interactive_ssh)

        assert "cs-1" not in owner._read_holds()

    def test_no_relay_skips_relay_forward_but_keeps_daemon_forward(
        self, store, monkeypatch,
    ) -> None:
        monkeypatch.setattr(owner, "ensure_owner_running", lambda config: True)
        monkeypatch.setattr(config_mod, "load_merged_config", lambda: object())

        seen_reverse_forwards: list[str] = []

        def fake_run_venue_copilot(worktree_id, *, connect, **kwargs):
            return connect("agent-worktrees copilot --worktree-id wt-A")

        def fake_interactive_ssh(name, forwards, relay_port=None, relay_token=None,
                                  remote_command=None):
            seen_reverse_forwards.extend(forwards)
            assert relay_port is None
            return 0

        monkeypatch.setattr("venue_copilot.run_venue_copilot", fake_run_venue_copilot)
        monkeypatch.setattr("venue_copilot.resolve_daemon_port", lambda: 9280)

        rc = copilot_venue.cmd_copilot(
            _ns(no_relay=True), interactive_ssh=fake_interactive_ssh,
        )

        assert rc == 0
        assert seen_reverse_forwards == ["9280:127.0.0.1:9280"]

    def test_venue_copilot_error_is_reported_and_not_raised(
        self, store, monkeypatch, capsys,
    ) -> None:
        from venue_copilot import VenueCopilotError

        monkeypatch.setattr(owner, "ensure_owner_running", lambda config: True)
        monkeypatch.setattr(config_mod, "load_merged_config", lambda: object())

        def fake_run_venue_copilot(worktree_id, *, connect, **kwargs):
            raise VenueCopilotError("already has an active reservation")

        monkeypatch.setattr("venue_copilot.run_venue_copilot", fake_run_venue_copilot)
        monkeypatch.setattr("venue_copilot.resolve_daemon_port", lambda: 9280)
        monkeypatch.setattr(
            "agent_codespaces.relay_launch.effective_relay_port", lambda config: 9857,
        )
        monkeypatch.setattr(
            "agent_codespaces.relay_token.token_for", lambda name: "tok-1",
        )

        rc = copilot_venue.cmd_copilot(
            _ns(), interactive_ssh=lambda *a, **kw: 0,
        )

        assert rc == 1
        assert "already has an active reservation" in capsys.readouterr().err
        assert "cs-1" not in owner._read_holds()

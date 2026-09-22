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


@pytest.fixture(autouse=True)
def _no_agent_bridge_preflight(request, monkeypatch):
    """The implicit agent-bridge-plugin preflight opens a real SSH connection
    -- irrelevant to every test below except its own dedicated coverage
    (``TestEnsureAgentBridgePlugin``/``TestProvisionRegistrationCredentials``,
    which must exercise the real thing), so it's a no-op everywhere else
    unless a test explicitly re-patches it."""
    exempt = {"TestEnsureAgentBridgePlugin", "TestProvisionRegistrationCredentials"}
    if request.cls is not None and request.cls.__name__ in exempt:
        return
    monkeypatch.setattr(copilot_venue, "_ensure_agent_bridge_plugin", lambda name: None)


@pytest.fixture(autouse=True)
def _no_claim_enforcement(request, monkeypatch):
    """The exclusive-claim check (`lease.claim_for_connect`) shells out to
    `agent-worktrees` -- irrelevant to every test below except its own
    dedicated coverage (`TestCmdCopilotClaimEnforcement`), so it's a no-op
    everywhere else unless a test explicitly re-patches it."""
    if request.cls is not None and request.cls.__name__ == "TestCmdCopilotClaimEnforcement":
        return
    from agent_codespaces import lease
    monkeypatch.setattr(lease, "claim_for_connect", lambda *a, **kw: None)


class _FakeTargetLock:
    """Fake ``ssh_manager.TargetLock`` -- mirrors agent-containers' own test
    double for the identical vendored primitive, so both venues' `copilot`
    tests exercise the same fake shape."""

    instances: list["_FakeTargetLock"] = []

    def __init__(self, target, *, op):
        self.target = target
        self.op = op
        self.released = False
        self.force = None
        _FakeTargetLock.instances.append(self)

    def acquire(self, *, force=False):
        self.force = force

    def release(self):
        self.released = True


@pytest.fixture(autouse=True)
def _no_target_lock_enforcement(request, monkeypatch):
    """The same-machine ``TargetLock`` acquisition touches
    ``~/.ssh-manager/locks/`` -- irrelevant to every test below except its
    own dedicated coverage (`TestCmdCopilotTargetLockEnforcement`), so it's
    faked out everywhere else unless a test explicitly re-patches it."""
    _FakeTargetLock.instances.clear()
    if request.cls is not None and request.cls.__name__ == "TestCmdCopilotTargetLockEnforcement":
        return
    import ssh_manager
    monkeypatch.setattr(ssh_manager, "TargetLock", _FakeTargetLock)


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


class TestCmdCopilotClaimEnforcement:
    """agent-bridge-cli-mode-sessions Phase 4 follow-up: `copilot` previously
    never enforced the exclusive, worktree-keyed CodeSpace claim
    `agent-codespaces ssh` already does -- a live claim held by one
    worktree/machine was silently bypassable simply by using `copilot`
    instead of `ssh` to drive the same CodeSpace. Confirmed live: a CLI-mode
    session was driven successfully on a CodeSpace a different, still-live
    worktree on another machine had legitimately claimed."""

    def _patch_common(self, monkeypatch, store):
        monkeypatch.setattr(owner, "ensure_owner_running", lambda config: True)
        monkeypatch.setattr(config_mod, "load_merged_config", lambda: object())
        monkeypatch.setattr("venue_copilot.resolve_daemon_port", lambda: None)
        monkeypatch.setattr(
            "agent_codespaces.relay_launch.effective_relay_port", lambda config: 9857,
        )
        monkeypatch.setattr(
            "agent_codespaces.relay_token.token_for", lambda name: "tok-1",
        )

    def test_claim_conflict_refuses_before_connecting(
        self, store, monkeypatch, capsys,
    ) -> None:
        from agent_codespaces import lease

        self._patch_common(monkeypatch, store)

        def fake_claim(name, *, force=False, effort=None, session_id=None):
            raise lease.ClaimConflict("cs-1", "other-worktree", "other-host", 4242)

        monkeypatch.setattr(lease, "claim_for_connect", fake_claim)
        connected = {"called": False}

        def fake_run_venue_copilot(*a, **kw):
            connected["called"] = True
            return 0

        monkeypatch.setattr("venue_copilot.run_venue_copilot", fake_run_venue_copilot)

        rc = copilot_venue.cmd_copilot(_ns(), interactive_ssh=lambda *a, **kw: 0)

        assert rc == 75  # _BUSY_EXIT
        assert connected == {"called": False}
        err = capsys.readouterr().err
        assert "[BUSY]" in err
        assert "--force-claim" in err

    def test_coordination_rejected_refuses_before_connecting(
        self, store, monkeypatch, capsys,
    ) -> None:
        from agent_codespaces import lease

        self._patch_common(monkeypatch, store)

        def fake_claim(name, *, force=False, effort=None, session_id=None):
            raise lease.CoordinationRejected("not-durable: no L2 lease backend")

        monkeypatch.setattr(lease, "claim_for_connect", fake_claim)
        connected = {"called": False}
        monkeypatch.setattr(
            "venue_copilot.run_venue_copilot",
            lambda *a, **kw: connected.__setitem__("called", True),
        )

        rc = copilot_venue.cmd_copilot(_ns(), interactive_ssh=lambda *a, **kw: 0)

        assert rc == 78  # _COORDINATION_EXIT
        assert connected == {"called": False}
        assert "[BLOCKED]" in capsys.readouterr().err

    def test_force_claim_and_effort_are_forwarded(self, store, monkeypatch) -> None:
        self._patch_common(monkeypatch, store)
        monkeypatch.setattr(
            "venue_copilot.run_venue_copilot",
            lambda worktree_id, *, connect, **kw: connect(
                "agent-worktrees copilot --worktree-id wt-A",
            ),
        )
        seen = {}

        def fake_claim(name, *, force=False, effort=None, session_id=None):
            seen.update(name=name, force=force, effort=effort)

        from agent_codespaces import lease
        monkeypatch.setattr(lease, "claim_for_connect", fake_claim)

        rc = copilot_venue.cmd_copilot(
            _ns(force_claim=True, effort="wt-explicit"),
            interactive_ssh=lambda *a, **kw: 0,
        )

        assert rc == 0
        assert seen == {"name": "cs-1", "force": True, "effort": "wt-explicit"}

    def test_a_claim_bookkeeping_error_never_blocks_the_connect(
        self, store, monkeypatch, capsys,
    ) -> None:
        self._patch_common(monkeypatch, store)
        monkeypatch.setattr(
            "venue_copilot.run_venue_copilot",
            lambda worktree_id, *, connect, **kw: connect(
                "agent-worktrees copilot --worktree-id wt-A",
            ),
        )

        def fake_claim(name, *, force=False, effort=None, session_id=None):
            raise RuntimeError("leases.json is corrupt")

        from agent_codespaces import lease
        monkeypatch.setattr(lease, "claim_for_connect", fake_claim)

        rc = copilot_venue.cmd_copilot(_ns(), interactive_ssh=lambda *a, **kw: 0)

        assert rc == 0
        assert "[WARN]" in capsys.readouterr().err


class TestCmdCopilotTargetLockEnforcement:
    """agent-bridge-cli-mode-sessions Phase 4 follow-up: `copilot` never
    acquired the same-machine, cross-process `ssh-manager` `TargetLock`
    `agent-codespaces ssh` already does, even though both ride the exact
    same shared credential-relay reverse-forward -- a second local
    `ssh`/`copilot` invocation against the same CodeSpace could collide on
    that relay port and collapse the first one's connection.
    `agent-containers`' own `copilot` verb already acquires this lock; this
    brings the two venues into consistent parity."""

    def _patch_common(self, monkeypatch, store):
        monkeypatch.setattr(owner, "ensure_owner_running", lambda config: True)
        monkeypatch.setattr(config_mod, "load_merged_config", lambda: object())
        monkeypatch.setattr("venue_copilot.resolve_daemon_port", lambda: None)
        monkeypatch.setattr(
            "agent_codespaces.relay_launch.effective_relay_port", lambda config: 9857,
        )
        monkeypatch.setattr(
            "agent_codespaces.relay_token.token_for", lambda name: "tok-1",
        )
        from agent_codespaces import lease
        monkeypatch.setattr(lease, "claim_for_connect", lambda *a, **kw: None)

    def test_acquires_and_releases_the_lock_around_a_successful_connect(
        self, store, monkeypatch,
    ) -> None:
        import ssh_manager

        self._patch_common(monkeypatch, store)
        monkeypatch.setattr(ssh_manager, "TargetLock", _FakeTargetLock)
        monkeypatch.setattr(
            "venue_copilot.run_venue_copilot",
            lambda worktree_id, *, connect, **kw: connect(
                "agent-worktrees copilot --worktree-id wt-A",
            ),
        )

        rc = copilot_venue.cmd_copilot(_ns(), interactive_ssh=lambda *a, **kw: 0)

        assert rc == 0
        assert len(_FakeTargetLock.instances) == 1
        lock = _FakeTargetLock.instances[0]
        assert lock.target == "cs-1"
        assert lock.op == "copilot"
        assert lock.force is False
        assert lock.released is True

    def test_force_flag_is_forwarded_to_the_lock(self, store, monkeypatch) -> None:
        import ssh_manager

        self._patch_common(monkeypatch, store)
        monkeypatch.setattr(ssh_manager, "TargetLock", _FakeTargetLock)
        monkeypatch.setattr(
            "venue_copilot.run_venue_copilot",
            lambda worktree_id, *, connect, **kw: connect(
                "agent-worktrees copilot --worktree-id wt-A",
            ),
        )

        rc = copilot_venue.cmd_copilot(
            _ns(force=True), interactive_ssh=lambda *a, **kw: 0,
        )

        assert rc == 0
        assert _FakeTargetLock.instances[0].force is True

    def test_busy_lock_refuses_before_connecting(
        self, store, monkeypatch, capsys,
    ) -> None:
        import ssh_manager

        self._patch_common(monkeypatch, store)

        class _BusyLock:
            def __init__(self, target, *, op):
                pass

            def acquire(self, *, force=False):
                holder = ssh_manager.LockHolder(
                    pid=4242, op="ssh", target="cs-1", started_at=0.0,
                )
                raise ssh_manager.TargetBusyError("cs-1", holder)

        monkeypatch.setattr(ssh_manager, "TargetLock", _BusyLock)
        connected = {"called": False}
        monkeypatch.setattr(
            "venue_copilot.run_venue_copilot",
            lambda *a, **kw: connected.__setitem__("called", True),
        )

        rc = copilot_venue.cmd_copilot(_ns(), interactive_ssh=lambda *a, **kw: 0)

        assert rc == 75  # _BUSY_EXIT
        assert connected == {"called": False}
        assert "[BUSY]" in capsys.readouterr().err

    def test_lock_is_released_even_when_connect_raises(
        self, store, monkeypatch,
    ) -> None:
        import ssh_manager

        self._patch_common(monkeypatch, store)
        monkeypatch.setattr(ssh_manager, "TargetLock", _FakeTargetLock)

        def fake_run_venue_copilot(worktree_id, *, connect, **kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(
            "venue_copilot.run_venue_copilot", fake_run_venue_copilot,
        )

        with pytest.raises(RuntimeError):
            copilot_venue.cmd_copilot(_ns(), interactive_ssh=lambda *a, **kw: 0)

        assert _FakeTargetLock.instances[0].released is True


class TestEnsureAgentBridgePlugin:
    """agent-bridge-cli-mode-sessions Phase 4 follow-up: without the
    ``agent-bridge`` Copilot plugin installed on the venue, the remote CLI
    process runs perfectly normally but its CLI-mode reservation is *never
    claimed* -- silently (confirmed live). `cmd_copilot` must implicitly
    close this gap itself, not merely document it as a manual `doctor --fix`
    prerequisite."""

    def test_cmd_copilot_invokes_the_preflight_with_the_venue_name(
        self, store, monkeypatch,
    ) -> None:
        monkeypatch.setattr(owner, "ensure_owner_running", lambda config: True)
        monkeypatch.setattr(config_mod, "load_merged_config", lambda: object())
        monkeypatch.setattr(
            "venue_copilot.run_venue_copilot",
            lambda worktree_id, *, connect, **kwargs: connect(
                "agent-worktrees copilot --worktree-id wt-A",
            ),
        )
        monkeypatch.setattr("venue_copilot.resolve_daemon_port", lambda: None)
        monkeypatch.setattr(
            "agent_codespaces.relay_launch.effective_relay_port", lambda config: 9857,
        )
        monkeypatch.setattr(
            "agent_codespaces.relay_token.token_for", lambda name: "tok-1",
        )

        seen = {}
        monkeypatch.setattr(
            copilot_venue, "_ensure_agent_bridge_plugin",
            lambda name: seen.setdefault("name", name),
        )

        rc = copilot_venue.cmd_copilot(_ns(), interactive_ssh=lambda *a, **kw: 0)

        assert rc == 0
        assert seen == {"name": "cs-1"}

    def test_installs_when_missing(self, monkeypatch) -> None:
        from agent_codespaces import venue_check

        readiness_before = venue_check.VenueReadiness(
            copilot_path="/usr/local/bin/copilot", tmux=True,
            agent_worktrees_state="full", agent_bridge_plugin=False,
        )

        class _FakeManager:
            async def ensure_connected(self, name, source, forwards):
                return None

            async def exec_command(self, host, script):
                return argparse.Namespace(stdout="", exit_code=0, stderr="")

            async def disconnect(self, name):
                return None

        monkeypatch.setattr(
            "ssh_manager.ConnectionManager", lambda: _FakeManager(),
        )
        monkeypatch.setattr(
            "agent_codespaces.lifecycle.account_for_codespace", lambda name: None,
        )
        monkeypatch.setattr(
            "agent_codespaces.codespace_config.CodespaceSource",
            lambda name, account=None: object(),
        )
        monkeypatch.setattr("venue_copilot.resolve_daemon_port", lambda: 58800)
        monkeypatch.setattr("venue_copilot.resolve_local_auth_token", lambda: "tok-xyz")

        calls = {"check": 0}

        async def fake_check(exec_command, host, **kwargs):
            calls["check"] += 1
            return readiness_before

        remediated = {}

        async def fake_remediate(exec_command, host, readiness, **kwargs):
            remediated["called"] = True
            result = venue_check.RemediationResult()
            result.succeeded.append("install agent-bridge plugin")
            return result

        monkeypatch.setattr(venue_check, "check_remote_venue", fake_check)
        monkeypatch.setattr(venue_check, "remediate_remote_venue", fake_remediate)

        copilot_venue._ensure_agent_bridge_plugin("cs-1")

        assert remediated == {"called": True}
        assert calls["check"] == 1

    def test_already_present_skips_remediation(self, monkeypatch) -> None:
        from agent_codespaces import venue_check

        readiness = venue_check.VenueReadiness(
            copilot_path="/usr/local/bin/copilot", tmux=True,
            agent_worktrees_state="full", agent_bridge_plugin=True,
        )

        class _FakeManager:
            async def ensure_connected(self, name, source, forwards):
                return None

            async def exec_command(self, host, script):
                return argparse.Namespace(stdout="", exit_code=0, stderr="")

            async def disconnect(self, name):
                return None

        monkeypatch.setattr(
            "ssh_manager.ConnectionManager", lambda: _FakeManager(),
        )
        monkeypatch.setattr(
            "agent_codespaces.lifecycle.account_for_codespace", lambda name: None,
        )
        monkeypatch.setattr(
            "agent_codespaces.codespace_config.CodespaceSource",
            lambda name, account=None: object(),
        )
        monkeypatch.setattr("venue_copilot.resolve_daemon_port", lambda: 58800)
        monkeypatch.setattr("venue_copilot.resolve_local_auth_token", lambda: "tok-xyz")

        async def fake_check(exec_command, host, **kwargs):
            return readiness

        called = {"remediate": False}

        async def fake_remediate(*a, **kw):
            called["remediate"] = True
            raise AssertionError("must not remediate when already present")

        monkeypatch.setattr(venue_check, "check_remote_venue", fake_check)
        monkeypatch.setattr(venue_check, "remediate_remote_venue", fake_remediate)

        copilot_venue._ensure_agent_bridge_plugin("cs-1")

        assert called == {"remediate": False}

    def test_a_probe_failure_never_raises(self, monkeypatch) -> None:
        """Best-effort: a broken connection during the implicit preflight
        must not prevent the operator from still attempting to connect."""
        monkeypatch.setattr(
            "ssh_manager.ConnectionManager",
            lambda: (_ for _ in ()).throw(RuntimeError("no route to host")),
        )

        copilot_venue._ensure_agent_bridge_plugin("cs-1")  # must not raise


class TestProvisionRegistrationCredentials:
    """agent-bridge-cli-mode-sessions Phase 4 follow-up, layer 2: even with
    the agent-bridge plugin installed, the interactive extension's own
    ``resolveToken()``/``resolveBaseUrl()`` read ``~/.agent-bridge/
    auth.yaml``/``active.json`` -- files that only ever exist on the machine
    running the daemon. Confirmed live: a real CodeSpace session loaded the
    extension, reached a ready prompt, and still never registered, purely
    because these files were never provisioned there. `cmd_copilot` must
    write them implicitly, every time, before connecting."""

    def _ready_manager(self, exec_calls):
        from agent_codespaces import venue_check

        class _FakeManager:
            async def ensure_connected(self, name, source, forwards):
                return None

            async def exec_command(self, host, script):
                exec_calls.append(script)
                return argparse.Namespace(stdout="", exit_code=0, stderr="")

            async def disconnect(self, name):
                return None

        return _FakeManager(), venue_check.VenueReadiness(
            copilot_path="/usr/local/bin/copilot", tmux=True,
            agent_worktrees_state="full", agent_bridge_plugin=True,
        )

    def test_writes_token_and_port_when_both_resolve(self, monkeypatch) -> None:
        from agent_codespaces import venue_check

        exec_calls: list[str] = []
        manager, readiness = self._ready_manager(exec_calls)
        monkeypatch.setattr("ssh_manager.ConnectionManager", lambda: manager)
        monkeypatch.setattr(
            "agent_codespaces.lifecycle.account_for_codespace", lambda name: None,
        )
        monkeypatch.setattr(
            "agent_codespaces.codespace_config.CodespaceSource",
            lambda name, account=None: object(),
        )
        monkeypatch.setattr(venue_check, "check_remote_venue", lambda *a, **kw: _async(readiness))
        monkeypatch.setattr("venue_copilot.resolve_daemon_port", lambda: 58800)
        monkeypatch.setattr("venue_copilot.resolve_local_auth_token", lambda: "tok-xyz")

        copilot_venue._ensure_agent_bridge_plugin("cs-1")

        assert any(
            "auth.yaml" in c and "tok-xyz" in c and "active.json" in c and "58800" in c
            for c in exec_calls
        )

    def test_skips_provisioning_without_a_local_token(self, monkeypatch) -> None:
        from agent_codespaces import venue_check

        exec_calls: list[str] = []
        manager, readiness = self._ready_manager(exec_calls)
        monkeypatch.setattr("ssh_manager.ConnectionManager", lambda: manager)
        monkeypatch.setattr(
            "agent_codespaces.lifecycle.account_for_codespace", lambda name: None,
        )
        monkeypatch.setattr(
            "agent_codespaces.codespace_config.CodespaceSource",
            lambda name, account=None: object(),
        )
        monkeypatch.setattr(venue_check, "check_remote_venue", lambda *a, **kw: _async(readiness))
        monkeypatch.setattr("venue_copilot.resolve_daemon_port", lambda: 58800)
        monkeypatch.setattr("venue_copilot.resolve_local_auth_token", lambda: None)

        copilot_venue._ensure_agent_bridge_plugin("cs-1")

        assert not any("auth.yaml" in c for c in exec_calls)

    def test_skips_provisioning_without_a_resolved_daemon_port(self, monkeypatch) -> None:
        from agent_codespaces import venue_check

        exec_calls: list[str] = []
        manager, readiness = self._ready_manager(exec_calls)
        monkeypatch.setattr("ssh_manager.ConnectionManager", lambda: manager)
        monkeypatch.setattr(
            "agent_codespaces.lifecycle.account_for_codespace", lambda name: None,
        )
        monkeypatch.setattr(
            "agent_codespaces.codespace_config.CodespaceSource",
            lambda name, account=None: object(),
        )
        monkeypatch.setattr(venue_check, "check_remote_venue", lambda *a, **kw: _async(readiness))
        monkeypatch.setattr("venue_copilot.resolve_daemon_port", lambda: None)
        monkeypatch.setattr("venue_copilot.resolve_local_auth_token", lambda: "tok-xyz")

        copilot_venue._ensure_agent_bridge_plugin("cs-1")

        assert not any("auth.yaml" in c for c in exec_calls)


async def _async(value):
    return value

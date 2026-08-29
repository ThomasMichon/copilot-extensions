"""Regression: `resolve --new` produces a MUXED session unless --no-mux is
passed, so the picker's cross-env "New worktree" handoff (e.g. Windows ->
Anomalous-Potato WSL) wraps in tmux/psmux like a local launch. agent-bridge still
gets no-mux because it passes --no-mux (and --json) explicitly.
"""

from __future__ import annotations

import argparse
from types import SimpleNamespace
from unittest.mock import patch

from agent_worktrees import __main__ as cli


def _args(**over):
    base = dict(
        json=False, base=False, new_worktree=False, auto=False,
        worktree_id=None, machine=None, environment=None,
        target_no_mux=False, no_mux=False, dry_run=False,
        recovery=False, no_resume=False, no_fast_forward=False,
        profile=None, copilot_args=[],
    )
    base.update(over)
    return argparse.Namespace(**base)


def _fake_config():
    # default_repo.base_repo must be falsy so resolve takes the worktree path.
    repo = SimpleNamespace(base_repo=False)
    return SimpleNamespace(default_repo=repo, machine="anomalous-potato")


def _run_new(args, *, tty=True):
    """Drive cmd_resolve down the --new branch, capturing args.no_mux at the
    point _resolve_new is invoked.

    ``tty`` simulates a controlling terminal (the cross-env "New worktree"
    handoff runs ``<project> --new`` over ``ssh -t``). A muxed ``--new`` with
    no TTY is refused by cmd_resolve, so the muxed-path assertions run with
    ``tty=True``.
    """
    captured = {}

    def _fake_resolve_new(config, a, profile=None):
        captured["no_mux"] = getattr(a, "no_mux", None)
        return 0

    with patch.object(cli.cfg, "load_config", return_value=_fake_config()), \
         patch.object(cli, "_resolve_profile", return_value=None), \
         patch.object(cli.sys.stdin, "isatty", return_value=tty), \
         patch.object(cli, "_resolve_new", side_effect=_fake_resolve_new):
        rc = cli.cmd_resolve(args)
    return rc, captured


def test_new_is_muxed_by_default():
    rc, captured = _run_new(_args(new_worktree=True))
    assert rc == 0
    assert captured["no_mux"] is False        # tmux/psmux wraps the session


def test_new_with_no_mux_is_honored():
    rc, captured = _run_new(_args(new_worktree=True, no_mux=True))
    assert rc == 0
    assert captured["no_mux"] is True          # explicit opt-out still works


def test_muxed_new_without_tty_is_refused():
    """An agent running ``<project> --new`` from a tool call (no TTY, no
    --no-mux, no --json) would spawn an un-attachable mux session. cmd_resolve
    refuses it and never reaches _resolve_new."""
    rc, captured = _run_new(_args(new_worktree=True), tty=False)
    assert rc == 2
    assert captured == {}                       # guarded before launch


def test_no_mux_new_without_tty_is_allowed():
    """``--no-mux`` (what agent-bridge passes) makes non-TTY ``--new`` fine --
    it produces clean stdio, not a mux session."""
    rc, captured = _run_new(_args(new_worktree=True, no_mux=True), tty=False)
    assert rc == 0
    assert captured["no_mux"] is True


def test_json_remote_resume_emits_environment_specific_handoff():
    args = _args(
        json=True,
        worktree_id="wt-1",
        machine="Example",
        environment="WSL",
        bare_resume=True,
        target_no_mux=True,
    )
    captured = {}

    def _emit(config, machine, environment, remote_args):
        captured.update(
            machine=machine,
            environment=environment,
            remote_args=remote_args,
        )
        return 0

    with patch.object(cli.cfg, "load_config", return_value=_fake_config()), \
         patch.object(cli, "_emit_remote_plan_for_env", side_effect=_emit):
        assert cli.cmd_resolve(args) == 0

    assert captured == {
        "machine": "Example",
        "environment": "WSL",
        "remote_args": [
            "--worktree-id",
            "wt-1",
            "--bare-resume",
            "--no-mux",
        ],
    }


def test_json_remote_base_emits_base_handoff():
    args = _args(
        json=True,
        base=True,
        machine="Example",
        environment="Win",
    )
    captured = {}

    def _emit(config, machine, environment, remote_args):
        captured["remote_args"] = remote_args
        return 0

    with patch.object(cli.cfg, "load_config", return_value=_fake_config()), \
         patch.object(cli, "_emit_remote_plan_for_env", side_effect=_emit):
        assert cli.cmd_resolve(args) == 0

    assert captured["remote_args"] == ["--base"]


def test_remote_plan_rejects_unknown_explicit_environment(tmp_path):
    entry = cli.cfg.MachineEntry(
        key="example",
        display_name="Example",
        environment="windows",
        ssh_environments=[
            cli.cfg.SSHEnvironment(name="windows", alias="example-win"),
            cli.cfg.SSHEnvironment(name="wsl", alias="example-wsl"),
        ],
    )
    config = SimpleNamespace(
        machine="local",
        default_repo=SimpleNamespace(anchor=str(tmp_path)),
    )

    with patch.object(cli.cfg, "load_machines_yaml", return_value={"example": entry}), \
         patch.object(cli, "_emit_plan") as emit:
        result = cli._emit_remote_plan_for_env(
            config,
            "Example",
            "Bogus",
            ["--new"],
        )

    assert result is None
    emit.assert_not_called()

"""Regression: `resolve --new` produces a MUXED session unless --no-mux is
passed, so the picker's cross-env "New worktree" handoff (e.g. Windows ->
Lambda-Core WSL) wraps in tmux/psmux like a local launch. agent-bridge still
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
        worktree_id=None, machine=None, no_mux=False, dry_run=False,
        recovery=False, no_resume=False, no_fast_forward=False,
        profile=None, copilot_args=[],
    )
    base.update(over)
    return argparse.Namespace(**base)


def _fake_config():
    # default_repo.base_repo must be falsy so resolve takes the worktree path.
    repo = SimpleNamespace(base_repo=False)
    return SimpleNamespace(default_repo=repo, machine="lambda-core")


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

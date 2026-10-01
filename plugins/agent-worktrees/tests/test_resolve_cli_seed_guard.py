"""picker-new-session-prompt-and-composer: ``resolve --new --seed`` must be
rejected when combined with a remote ``--machine`` target, since the remote
command line is relayed as a naive space-joined string with no shell
quoting -- unsafe for a value that can contain arbitrary text.
"""
from __future__ import annotations

import argparse
import json

import pytest

from agent_worktrees import resolve_cli

pytestmark = pytest.mark.guard


def _args(**overrides):
    base = dict(
        json=True, base=False, new_worktree=True, auto=False,
        machine="example-host", environment=None, worktree_id=None,
        codename=None, seed=None, bare_resume=False, restore=False,
        target_no_mux=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_resolve_new_with_machine_and_seed_is_rejected(capfd):
    state = resolve_cli.ResolveCommandState.from_args(
        _args(seed="do the thing"))
    state.config = object()  # load_config() short-circuits on a truthy cache

    rc = resolve_cli._resolve_json_mode(state)

    assert rc != 0
    out = json.loads(capfd.readouterr().out)
    assert "seed" in out.get("error", "").lower()
    assert "machine" in out.get("error", "").lower()


def test_resolve_new_with_machine_and_no_seed_is_unaffected(monkeypatch, capfd):
    """The rejection is seed-specific -- an ordinary remote --new (no
    --seed) must still reach the existing remote-dispatch path untouched."""
    state = resolve_cli.ResolveCommandState.from_args(_args(seed=None))
    state.config = object()
    called = []
    monkeypatch.setattr(
        resolve_cli, "_emit_remote_plan_for_env",
        lambda *a, **k: called.append((a, k)) or 0,
    )

    rc = resolve_cli._resolve_json_mode(state)

    assert rc == 0
    assert called  # reached the remote-dispatch call, unaffected by the guard

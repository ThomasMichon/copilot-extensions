"""picker-new-session-prompt-and-composer /
resume-prompt-durable-seed-and-mux-fix: ``resolve --seed`` guard behavior.

``--seed`` is valid with either ``--new`` (persisted onto the new record) or
``--worktree-id`` (appended directly to the resume launch's own argv -- see
``resolve_launch_cli._resolve_resume_context``) -- but never with ``--base``
(no worktree record, no launch argv builder for the seed to reach), and never
combined with a remote ``--machine`` target in either mode: the non-JSON path
checks ``use_new``/``worktree_id`` before ``requested_machine`` and would
otherwise silently create/resume LOCALLY instead of honoring (or rejecting)
``--machine``; the JSON path's remote dispatch additionally relays a naively
space-joined command string with no shell quoting, unsafe for a value that
can contain arbitrary text.
"""
from __future__ import annotations

import argparse
import contextlib
import json
from types import SimpleNamespace

import pytest

from agent_worktrees import resolve_cli

pytestmark = pytest.mark.guard


def _args(**overrides):
    base = dict(
        json=True, base=False, new_worktree=True, auto=False,
        machine="example-host", environment=None, worktree_id=None,
        codename=None, seed=None, bare_resume=False, restore=False,
        target_no_mux=False, no_mux=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_resolve_new_with_machine_and_seed_preview_is_rejected_json(capfd):
    rc = resolve_cli.cmd_resolve(_args(seed="do the thing", dry_run=True))

    assert rc != 0
    out = json.loads(capfd.readouterr().out)
    assert "seed" in out.get("error", "").lower()
    assert "machine" in out.get("error", "").lower()


def test_resolve_new_with_machine_and_seed_is_rejected_non_json(capfd):
    """Regression for the non-JSON dispatcher, which checks ``use_new``
    before ``requested_machine`` and would otherwise never see --machine
    at all for this combination."""
    rc = resolve_cli.cmd_resolve(_args(json=False, seed="do the thing"))

    assert rc != 0
    out = capfd.readouterr().out
    assert "seed" in out.lower()
    assert "machine" in out.lower()


def test_resolve_new_with_machine_and_no_seed_is_unaffected(monkeypatch, capfd):
    """The rejection is seed-specific -- an ordinary remote --new (no
    --seed) must still reach the existing remote-dispatch path untouched."""
    state = resolve_cli.ResolveCommandState.from_args(_args(seed=None))
    state.config = object()  # load_config() short-circuits on a truthy cache
    called = []
    monkeypatch.setattr(
        resolve_cli, "_emit_remote_plan_for_env",
        lambda *a, **k: called.append((a, k)) or 0,
    )

    rc = resolve_cli._resolve_json_mode(state)

    assert rc == 0
    assert called  # reached the remote-dispatch call, unaffected by the guard


def test_resolve_machine_matching_the_local_machine_is_a_no_op_not_a_self_ssh_handoff(monkeypatch):
    """Regression: `--machine <this machine's own name>` previously skipped
    straight to `_emit_remote_plan_for_env` with no local-machine check at
    all (unlike the non-JSON/interactive path's own
    `requested_machine != config.machine` guard before its equivalent
    `_try_machine_handoff` call) -- so a caller already running on the named
    machine got handed back a real "ssh <this box>" plan instead of ordinary
    local resolution. Confirmed live via `resolve --machine <local-name>
    --base --json`."""
    state = resolve_cli.ResolveCommandState.from_args(_args(seed=None))
    state.config = SimpleNamespace(machine=state.requested_machine)  # self-match
    called = []
    monkeypatch.setattr(
        resolve_cli, "_emit_remote_plan_for_env",
        lambda *a, **k: called.append((a, k)) or 0,
    )

    with contextlib.suppress(Exception):  # fake config is too bare for the local path to finish
        resolve_cli._resolve_json_mode(state)

    assert not called  # must never dispatch to itself over SSH


def test_resolve_machine_matching_local_but_environment_cross_platform_still_dispatches(monkeypatch):
    """Regression for the Windows Worktree Picker launching a same-machine
    WSL worktree (e.g. `--machine some-host --environment WSL` run from
    that same host's own Windows side): the self-targeting no-op above must
    NOT swallow this case merely because `--machine` names the local
    machine -- that worktree lives in a different OS/filesystem with its
    own tracking directory, invisible to this process's local
    `cfg.tracking_dir()`. Before the fix this fell through to local
    resolution and failed closed with "Worktree not found" instead of
    dispatching the SSH handoff into the requested environment."""
    from agent_worktrees import resolve_cli as resolve_cli_mod

    monkeypatch.setattr(resolve_cli_mod.cfg, "detect_platform", lambda: "windows")
    state = resolve_cli.ResolveCommandState.from_args(
        _args(seed=None, environment="WSL")
    )
    state.config = SimpleNamespace(machine=state.requested_machine)  # self-match
    called = []
    monkeypatch.setattr(
        resolve_cli, "_emit_remote_plan_for_env",
        lambda *a, **k: called.append((a, k)) or 0,
    )

    rc = resolve_cli._resolve_json_mode(state)

    assert rc == 0
    assert called  # must still dispatch -- the environment differs from "windows"


def test_resolve_machine_matching_local_and_environment_matches_current_platform_is_a_no_op(monkeypatch):
    """Sibling of the above: when `--environment` is given but it names the
    SAME platform this process is already running on (e.g. `--environment
    Win` from Windows), the self-targeting no-op still applies -- this is
    genuinely local, not a cross-platform handoff."""
    from agent_worktrees import resolve_cli as resolve_cli_mod

    monkeypatch.setattr(resolve_cli_mod.cfg, "detect_platform", lambda: "windows")
    state = resolve_cli.ResolveCommandState.from_args(
        _args(seed=None, environment="Win")
    )
    state.config = SimpleNamespace(machine=state.requested_machine)  # self-match
    called = []
    monkeypatch.setattr(
        resolve_cli, "_emit_remote_plan_for_env",
        lambda *a, **k: called.append((a, k)) or 0,
    )

    with contextlib.suppress(Exception):  # fake config is too bare for the local path to finish
        resolve_cli._resolve_json_mode(state)

    assert not called  # same machine, same platform -- must stay local


def test_resolve_machine_differing_from_the_local_machine_still_dispatches_remotely(monkeypatch):
    """Sibling of the self-match regression above: a genuinely different
    `--machine` must still reach `_emit_remote_plan_for_env` -- the new guard
    is specific to the self-match case, not a blanket skip."""
    state = resolve_cli.ResolveCommandState.from_args(_args(seed=None))
    state.config = SimpleNamespace(machine="a-different-machine")
    called = []
    monkeypatch.setattr(
        resolve_cli, "_emit_remote_plan_for_env",
        lambda *a, **k: called.append((a, k)) or 0,
    )

    rc = resolve_cli._resolve_json_mode(state)

    assert rc == 0
    assert called


def test_resolve_worktree_id_with_machine_and_seed_preview_is_rejected_json(capfd):
    """The same remote-target rejection applies to a --worktree-id resume
    seed, not only --new."""
    rc = resolve_cli.cmd_resolve(
        _args(new_worktree=False, worktree_id="some-wt", seed="do the thing", dry_run=True)
    )

    assert rc != 0
    out = json.loads(capfd.readouterr().out)
    assert "seed" in out.get("error", "").lower()
    assert "machine" in out.get("error", "").lower()


@pytest.mark.parametrize("new", [False, True])
def test_remote_prompt_cli_routes_same_seed_selectors_and_no_mux(monkeypatch, new):
    config = SimpleNamespace(machine="local")
    monkeypatch.setattr(resolve_cli.cfg, "load_config", lambda *a, **k: config)
    called = []
    monkeypatch.setattr(
        resolve_cli, "_emit_remote_plan_for_env",
        lambda *a, **k: called.append((a, k)) or 0,
    )
    args = _args(new_worktree=new, worktree_id=None if new else "wt-a",
                 seed="line one\nquotes ' $()", environment="WSL", target_no_mux=True)
    assert resolve_cli.cmd_resolve(args) == 0
    route, keywords = called[0]
    assert route[:3] == (config, "example-host", "WSL")
    assert route[3] == (["--new", "--no-mux"] if new else ["--worktree-id", "wt-a", "--no-mux"])
    assert keywords == {"seed": args.seed}


def test_resolve_worktree_id_with_seed_and_no_machine_is_accepted(capfd):
    """resume-prompt-durable-seed-and-mux-fix: --seed is now valid alongside
    --worktree-id (not only --new) -- the guard must not reject this
    combination merely because --new is absent."""
    rc = resolve_cli.cmd_resolve(
        _args(
            new_worktree=False, worktree_id="some-wt", seed="do the thing",
            machine=None,
        )
    )

    # Past the seed guard entirely -- the call proceeds to resolve the
    # worktree (and fails for an unrelated reason: no such tracked worktree
    # in this test's environment), never the "--seed is only valid with"
    # rejection this guard owns.
    assert rc != 0
    out = json.loads(capfd.readouterr().out)
    assert "seed" not in out.get("error", "").lower()


def test_resolve_base_with_seed_is_still_rejected_json(capfd):
    """--base has no worktree record and no resume-context launch argv for
    a seed to reach -- the guard must keep rejecting this combination."""
    rc = resolve_cli.cmd_resolve(
        _args(new_worktree=False, base=True, seed="do the thing", machine=None)
    )

    assert rc != 0
    out = json.loads(capfd.readouterr().out)
    assert "seed" in out.get("error", "").lower()


def test_resolve_worktree_id_with_bare_resume_and_seed_is_rejected_json(capfd):
    """--bare-resume launches Copilot in HOME with no --resume at all
    (dodging a cwd-start bug) -- there is no resumed conversation, and
    arguably no well-defined worktree session, for a seed to join. Without
    this guard the combination used to exit 0 while silently discarding
    the prompt; it must now be rejected explicitly, the same as --base and
    a remote --machine target."""
    rc = resolve_cli.cmd_resolve(
        _args(
            new_worktree=False, worktree_id="some-wt", seed="do the thing",
            machine=None, bare_resume=True,
        )
    )

    assert rc != 0
    out = json.loads(capfd.readouterr().out)
    assert "seed" in out.get("error", "").lower()
    assert "bare" in out.get("error", "").lower()


def test_resolve_worktree_id_with_bare_resume_and_no_seed_is_unaffected(capfd):
    """The new bare-resume guard must only fire when --seed is actually
    requested -- an ordinary --bare-resume call (no --seed) must proceed
    past the guard untouched."""
    rc = resolve_cli.cmd_resolve(
        _args(
            new_worktree=False, worktree_id="some-wt", seed=None,
            machine=None, bare_resume=True,
        )
    )

    # Past the seed guard entirely -- fails for an unrelated reason (no
    # such tracked worktree in this test's environment), never the
    # "--bare-resume" rejection this guard owns.
    assert rc != 0
    out = json.loads(capfd.readouterr().out)
    assert "bare" not in out.get("error", "").lower()


def test_restore_with_prompt_is_rejected_before_remux(capfd):
    rc = resolve_cli.cmd_resolve(
        _args(
            new_worktree=False, worktree_id="some-wt", seed="must not run",
            machine=None, restore=True,
        )
    )
    assert rc != 0
    payload = json.loads(capfd.readouterr().out)
    assert "--restore" in payload["error"]

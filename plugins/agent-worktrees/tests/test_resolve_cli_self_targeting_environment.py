"""Regression test for `_resolve_json_mode`'s self-targeting guard
(copilot-extensions#5554).

The guard exists to make `--machine <this machine's own name>` behave like
`--machine` was never passed (fixing an earlier bug where a self-targeted
call round-tripped through SSH back to itself). But it must NOT swallow a
same-machine, cross-*environment* ask -- `--machine <self> --environment
WSL` from a native Windows process is a genuinely different target (the
WSL side's own worktree registry, reached over its SSH alias), exactly the
case `_load_remote_machines` carves out for the interactive picker's "Other
Machines" menu ("local machine: only include other-platform environments").

The "is this the local machine?" half of the guard is delegated to
`machine_identity.is_local_machine` (canonicalizes case variants, registry
keys, aliases, display names, and hostnames -- see that module's docstring)
rather than a raw `== config.machine` string compare, so these tests mock
that helper directly instead of re-deriving its internals.
"""

from __future__ import annotations

import argparse
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import machine_identity, resolve_cli


pytestmark = pytest.mark.guard


def _state(*, requested_machine, environment, worktree_id="wt-1"):
    args = argparse.Namespace(
        machine=requested_machine,
        environment=environment,
        worktree_id=worktree_id,
        bare_resume=False,
        restore=False,
        target_no_mux=False,
    )
    config = SimpleNamespace(machine="lambda-core")
    return resolve_cli.ResolveCommandState(
        args=args,
        use_json=True,
        use_base=False,
        use_new=False,
        requested_machine=requested_machine,
        worktree_id=worktree_id,
        config=config,
    )


def test_same_machine_cross_environment_still_dispatches_remote():
    """`--machine <self> --environment WSL` from a native Windows process
    must still reach `_emit_remote_plan_for_env` -- it is not a no-op."""
    state = _state(requested_machine="lambda-core", environment="WSL")
    with patch.object(machine_identity, "is_local_machine", return_value=True), \
         patch.object(cfg, "detect_platform", return_value="windows"), \
         patch.object(resolve_cli, "_emit_remote_plan_for_env", return_value=0) as emit:
        rc = resolve_cli._resolve_json_mode(state)

    assert rc == 0
    emit.assert_called_once()
    call_args = emit.call_args.args
    assert call_args[1] == "lambda-core"
    assert call_args[2] == "WSL"


def test_alias_spelling_of_self_with_cross_environment_still_dispatches_remote():
    """A registry-alias/case-variant spelling of this same machine (what
    `is_local_machine` canonicalizes, unlike a raw string compare) must be
    treated identically to the exact-name case above."""
    state = _state(requested_machine="LAMBDA-CORE-ALIAS", environment="WSL")
    with patch.object(machine_identity, "is_local_machine", return_value=True), \
         patch.object(cfg, "detect_platform", return_value="windows"), \
         patch.object(resolve_cli, "_emit_remote_plan_for_env", return_value=0) as emit:
        rc = resolve_cli._resolve_json_mode(state)

    assert rc == 0
    emit.assert_called_once()


def test_true_self_targeting_with_no_environment_skips_remote_dispatch():
    """A plain `--machine <self>` (no `--environment`, or one matching this
    process's own platform) must still behave as if `--machine` was never
    passed -- the original bug this guard fixes. With no real worktree
    registry behind this minimal fixture, falling through to *local*
    resolution surfaces as a local "Worktree not found" error (nonzero exit)
    -- the point under test is that it's a *local* lookup failure, never a
    remote dispatch.
    """
    state = _state(requested_machine="lambda-core", environment=None)
    with patch.object(machine_identity, "is_local_machine", return_value=True), \
         patch.object(cfg, "detect_platform", return_value="windows"), \
         patch.object(cfg, "project_name", return_value="example-project"), \
         patch.object(resolve_cli, "_emit_remote_plan_for_env") as emit:
        rc = resolve_cli._resolve_json_mode(state)

    emit.assert_not_called()
    assert rc != 0


def test_same_machine_same_environment_label_skips_remote_dispatch():
    """`--machine <self> --environment Win` on a native Windows process is
    still true self-targeting (the environment matches this platform)."""
    state = _state(requested_machine="lambda-core", environment="Win")
    with patch.object(machine_identity, "is_local_machine", return_value=True), \
         patch.object(cfg, "detect_platform", return_value="windows"), \
         patch.object(cfg, "project_name", return_value="example-project"), \
         patch.object(resolve_cli, "_emit_remote_plan_for_env") as emit:
        rc = resolve_cli._resolve_json_mode(state)

    emit.assert_not_called()
    assert rc != 0


def test_different_machine_always_dispatches_remote_regardless_of_environment():
    """The pre-existing cross-machine case (never self-targeting) must keep
    working unchanged."""
    state = _state(requested_machine="other-box", environment=None)
    with patch.object(machine_identity, "is_local_machine", return_value=False), \
         patch.object(cfg, "detect_platform", return_value="windows"), \
         patch.object(resolve_cli, "_emit_remote_plan_for_env", return_value=0) as emit:
        rc = resolve_cli._resolve_json_mode(state)

    assert rc == 0
    emit.assert_called_once()

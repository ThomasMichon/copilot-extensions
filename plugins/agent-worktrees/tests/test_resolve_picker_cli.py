"""Tests for `resolve_picker_cli._run_machine_menu`'s remote-command wrapping.

Covers the gap found in review of #5190: this picker path passed
`ssh_env.shell` straight through to `_wrap_remote_command` without defaulting
it first, so selecting a Linux/WSL environment with no explicit `shell:` in
`machines.yaml` still reproduced the non-login-shell PATH bug the rest of
that PR fixed everywhere else.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import resolve_picker_cli as rpc
from agent_worktrees.picker import PickResult

pytestmark = pytest.mark.guard


def _entry(key, *, envs=()):
    return cfg.MachineEntry(
        key=key,
        display_name=key,
        environment=envs[0][0] if envs else "",
        ssh_environments=[
            cfg.SSHEnvironment(name=name, alias=alias, shell=shell)
            for name, alias, shell in envs
        ],
    )


def _fake_config():
    return SimpleNamespace(repo_name="example-project")


def test_run_machine_menu_defaults_shell_before_wrapping(tmp_path):
    """A Linux environment with no explicit `shell:` must still wrap the
    remote command (defaulting to bash), not pass the empty value through."""
    entry = _entry("borealis", envs=[("linux", "borealis", "")])
    config = _fake_config()

    with patch.object(rpc, "_load_remote_machines", return_value=[(entry, entry.ssh_environments)]), \
         patch.object(rpc, "pick", return_value=PickResult(selected=0)), \
         patch.object(cfg, "project_name", return_value="example-project"), \
         patch.object(rpc, "_emit_plan") as emit_plan:
        rc = rpc._run_machine_menu(config)

    assert rc == 0
    (plan,) = emit_plan.call_args.args
    assert plan["remote_command"] == "bash -lc example-project"


def test_run_machine_menu_does_not_wrap_windows(tmp_path):
    entry = _entry("atlas-core", envs=[("windows", "atlas-core", "")])
    config = _fake_config()

    with patch.object(rpc, "_load_remote_machines", return_value=[(entry, entry.ssh_environments)]), \
         patch.object(rpc, "pick", return_value=PickResult(selected=0)), \
         patch.object(cfg, "project_name", return_value="example-project"), \
         patch.object(rpc, "_emit_plan") as emit_plan:
        rc = rpc._run_machine_menu(config)

    assert rc == 0
    (plan,) = emit_plan.call_args.args
    assert plan["remote_command"] == "example-project"

"""Tests for `resolve_machine_cli`'s machine-label matching.

Covers the `hostname:`-only identification case: a machine declared with no
top-level `alias`, addressed only by its raw COMPUTERNAME via `hostname:`,
must still resolve -- `resolve --machine <raw-hostname>` previously fell
through to "unknown or unreachable remote machine" because the lookup only
matched `key`/`display_name`/`alias`, never `hostname`.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import resolve_machine_cli as rmc

pytestmark = pytest.mark.guard


def _entry(key, *, hostname="", alias="", envs=()):
    return cfg.MachineEntry(
        key=key,
        display_name=key,
        environment=envs[0][0] if envs else "",
        hostname=hostname,
        alias=alias,
        ssh_environments=[
            cfg.SSHEnvironment(name=name, alias=alias_) for name, alias_ in envs
        ],
    )


def _fake_config(tmp_path):
    return SimpleNamespace(default_repo=SimpleNamespace(anchor=str(tmp_path)))


def test_machine_key_for_display_matches_hostname(tmp_path):
    entries = {
        "atlas-core": _entry(
            "atlas-core",
            hostname="CPC-FAKE-HOST1",
            envs=[("windows", "atlas-core")],
        ),
    }
    config = _fake_config(tmp_path)
    with patch.object(cfg, "load_machines_yaml", return_value=entries):
        assert rmc._machine_key_for_display(config, "CPC-FAKE-HOST1") == "atlas-core"
        # Case-insensitive, matching the existing key/alias/display_name checks.
        assert rmc._machine_key_for_display(config, "cpc-fake-host1") == "atlas-core"


def test_emit_remote_plan_for_env_resolves_by_hostname(tmp_path):
    """`_emit_remote_plan_for_env` loads `entries` once itself, then calls
    `_machine_key_for_display` which loads its *own* copy via a second
    `load_machines_yaml` call. A naive single `return_value` mock lets that
    inner call also resolve the hostname, so `entries.get(key)` would already
    succeed before this function's own fallback loop ever runs. Make the
    inner (second) `load_machines_yaml` call fail -- so `_machine_key_for_display`
    returns the name unchanged -- forcing this function's own hostname match
    to actually execute."""
    entries = {
        "atlas-core": _entry(
            "atlas-core",
            hostname="CPC-FAKE-HOST1",
            envs=[("windows", "atlas-core")],
        ),
    }
    config = _fake_config(tmp_path)
    with patch.object(
        cfg, "load_machines_yaml", side_effect=[entries, FileNotFoundError()]
    ), \
         patch.object(cfg, "project_name", return_value="example-project"), \
         patch.object(rmc, "_emit_plan") as emit_plan:
        rc = rmc._emit_remote_plan_for_env(config, "CPC-FAKE-HOST1", "Win", [])

    assert rc == 0
    emit_plan.assert_called_once()
    (plan,) = emit_plan.call_args.args
    assert plan["action"] == "remote"
    assert plan["ssh_alias"] == "atlas-core"
    assert plan["machine"] == "atlas-core"


def test_emit_remote_plan_for_env_still_unknown_for_unmatched_name(tmp_path):
    entries = {
        "atlas-core": _entry(
            "atlas-core",
            hostname="CPC-FAKE-HOST1",
            envs=[("windows", "atlas-core")],
        ),
    }
    config = _fake_config(tmp_path)
    with patch.object(cfg, "load_machines_yaml", return_value=entries):
        rc = rmc._emit_remote_plan_for_env(config, "some-other-box", "Win", [])

    assert rc is None


# -- login-shell wrapping for a bare non-interactive SSH command-exec --------
#
# A bare `ssh host "cmd"` is a non-login, non-interactive shell for the
# remote side: neither ~/.profile (login-only) nor a guarded ~/.bashrc entry
# ever runs for it, so a POSIX host's `uv`/`copilot`/`gh` (installed to
# ~/.local/bin, only ever on PATH via one of those) is unreachable. Hit live
# resuming a worktree over SSH to a machine with no ~/.bashrc at all.


def test_wrap_remote_command_wraps_explicit_posix_shells():
    assert rmc._wrap_remote_command("bash", "aperture-labs") == (
        "bash -lc aperture-labs"
    )
    assert rmc._wrap_remote_command("sh", "aperture-labs") == "bash -lc aperture-labs"


def test_wrap_remote_command_quotes_the_inner_command():
    wrapped = rmc._wrap_remote_command("bash", "aperture-labs list --json")
    assert wrapped == "bash -lc 'aperture-labs list --json'"


def test_wrap_remote_command_never_wraps_pwsh_or_unrecognized_shell():
    # Wrapping a non-POSIX target in `bash -lc` would break it outright --
    # never guess for pwsh, and never guess for an unrecognized/empty value
    # either (only _resolve_ssh_target's own defaulting decides that).
    assert rmc._wrap_remote_command("pwsh", "aperture-labs") == "aperture-labs"
    assert rmc._wrap_remote_command("", "aperture-labs") == "aperture-labs"
    assert rmc._wrap_remote_command("cmd", "aperture-labs") == "aperture-labs"


def test_resolve_ssh_target_defaults_shell_from_environment_name():
    # Real-world machines.yaml always sets `shell:` explicitly today, but a
    # config that doesn't must still default sensibly: bash for a POSIX
    # environment, pwsh for a windows one -- never the other way around.
    posix_entry = _entry(
        "borealis", envs=[("linux", "borealis")],
    )
    assert rmc._resolve_ssh_target(posix_entry) == ("borealis", "bash")

    windows_entry = _entry(
        "atlas-core", envs=[("windows", "atlas-core")],
    )
    assert rmc._resolve_ssh_target(windows_entry) == ("atlas-core", "pwsh")


def test_emit_remote_plan_for_env_wraps_remote_command_for_posix_target(tmp_path):
    entries = {
        "borealis": _entry(
            "borealis",
            hostname="borealis",
            envs=[("linux", "borealis")],
        ),
    }
    config = _fake_config(tmp_path)
    with patch.object(cfg, "load_machines_yaml", return_value=entries), \
         patch.object(cfg, "project_name", return_value="aperture-labs"), \
         patch.object(rmc, "_emit_plan") as emit_plan:
        rc = rmc._emit_remote_plan_for_env(config, "borealis", "Linux", [])

    assert rc == 0
    (plan,) = emit_plan.call_args.args
    assert plan["remote_command"] == "bash -lc aperture-labs"


def test_emit_remote_plan_for_env_does_not_wrap_for_windows_target(tmp_path):
    entries = {
        "atlas-core": _entry(
            "atlas-core",
            hostname="atlas-core",
            envs=[("windows", "atlas-core")],
        ),
    }
    config = _fake_config(tmp_path)
    with patch.object(cfg, "load_machines_yaml", return_value=entries), \
         patch.object(cfg, "project_name", return_value="aperture-labs"), \
         patch.object(rmc, "_emit_plan") as emit_plan:
        rc = rmc._emit_remote_plan_for_env(config, "atlas-core", "Win", [])

    assert rc == 0
    (plan,) = emit_plan.call_args.args
    assert plan["remote_command"] == "aperture-labs"

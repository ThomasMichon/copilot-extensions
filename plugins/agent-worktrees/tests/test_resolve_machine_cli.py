"""Tests for `resolve_machine_cli`'s machine-label matching.

Covers the `hostname:`-only identification case: a machine declared with no
top-level `alias`, addressed only by its raw COMPUTERNAME via `hostname:`,
must still resolve -- `resolve --machine <raw-hostname>` previously fell
through to "unknown or unreachable remote machine" because the lookup only
matched `key`/`display_name`/`alias`, never `hostname`.
"""

from __future__ import annotations

import json
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import resolve_machine_cli as rmc

pytestmark = pytest.mark.guard


def _entry(key, *, hostname="", alias="", display_name=None, envs=()):
    return cfg.MachineEntry(
        key=key,
        display_name=display_name or key,
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
    """Matching and environment selection use one registry snapshot."""
    entries = {
        "atlas-core": _entry(
            "atlas-core",
            hostname="CPC-FAKE-HOST1",
            envs=[("windows", "atlas-core")],
        ),
    }
    config = _fake_config(tmp_path)
    with patch.object(
        cfg, "load_machines_yaml", return_value=entries
    ) as load, \
         patch.object(cfg, "project_name", return_value="example-project"), \
         patch.object(rmc, "_emit_plan") as emit_plan:
        rc = rmc._emit_remote_plan_for_env(config, "CPC-FAKE-HOST1", "Win", [])

    assert rc == 0
    emit_plan.assert_called_once()
    (plan,) = emit_plan.call_args.args
    assert plan["action"] == "remote"
    assert plan["ssh_alias"] == "atlas-core"
    assert plan["machine"] == "atlas-core"
    load.assert_called_once_with(str(tmp_path))


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
    # Invokes the CONFIGURED shell itself -- never hardcodes bash for a
    # different configured one (sh/zsh are both documented supported
    # remote-shell values, machine-config.md).
    assert rmc._wrap_remote_command("sh", "aperture-labs") == "sh -lc aperture-labs"
    assert rmc._wrap_remote_command("zsh", "aperture-labs") == "zsh -lc aperture-labs"


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


@pytest.mark.parametrize("name", [
    "atlas-core", "ATLAS-CORE", "friendly", "FRIENDLY",
    "CPC-FAKE-HOST1", "cpc-fake-host1", "Build Box", "build box",
])
def test_all_picker_matching_paths_share_machine_identities(tmp_path, name):
    entry = _entry(
        "atlas-core", hostname="CPC-FAKE-HOST1", alias="friendly",
        display_name="Build Box", envs=[("linux", "atlas-linux")],
    )
    config = _fake_config(tmp_path)
    with patch.object(cfg, "load_machines_yaml", return_value={entry.key: entry}), \
         patch.object(cfg, "project_name", return_value="example-project"), \
         patch.object(rmc, "_load_remote_machines", return_value=[(entry, entry.ssh_environments)]), \
         patch.object(rmc, "_emit_plan") as emit:
        assert rmc._machine_key_for_display(config, name) == entry.key
        assert rmc._try_machine_handoff(config, name) == 0
        assert rmc._emit_remote_plan_for_env(config, name, "Linux") == 0
    handoff, environment = [call.args[0] for call in emit.call_args_list]
    assert handoff == {
        "action": "remote", "ssh_alias": "atlas-linux",
        "remote_command": "bash -lc example-project",
        "machine": "atlas-core", "display_name": "Build Box",
    }
    assert environment == {**handoff, "display_name": "Build Box Linux"}


def test_ambiguous_picker_identity_never_emits_a_plan(tmp_path, capsys):
    entries = {
        key: _entry(key, alias="shared", envs=[("linux", f"{key}-ssh")])
        for key in ("first", "second")
    }
    config = _fake_config(tmp_path)
    targets = [(entry, entry.ssh_environments) for entry in entries.values()]
    with patch.object(cfg, "load_machines_yaml", return_value=entries), \
         patch.object(rmc, "_load_remote_machines", return_value=targets), \
         patch.object(rmc, "_emit_plan") as emit:
        with pytest.raises(ValueError, match="ambiguous"):
            rmc._machine_key_for_display(config, "SHARED")
        assert rmc._try_machine_handoff(config, "SHARED") == 1
        assert rmc._emit_remote_plan_for_env(config, "SHARED", "Linux") == 1
        emit.assert_not_called()
    assert "ambiguous" in capsys.readouterr().out


@pytest.mark.parametrize("name", ["shared", "SHARED"])
def test_exact_key_precedes_another_machine_alias(tmp_path, name):
    entries = {
        "shared": _entry("shared", envs=[("linux", "right-ssh")]),
        "other": _entry("other", alias="shared", envs=[("linux", "wrong-ssh")]),
    }
    config = _fake_config(tmp_path)
    targets = [(entry, entry.ssh_environments) for entry in entries.values()]
    with patch.object(cfg, "load_machines_yaml", return_value=entries), \
         patch.object(cfg, "project_name", return_value="example-project"), \
         patch.object(rmc, "_load_remote_machines", return_value=targets), \
         patch.object(rmc, "_emit_plan") as emit:
        assert rmc._machine_key_for_display(config, name) == "shared"
        assert rmc._try_machine_handoff(config, name) == 0
        assert rmc._emit_remote_plan_for_env(config, name, "Linux") == 0
    assert all(call.args[0]["ssh_alias"] == "right-ssh" for call in emit.call_args_list)


def test_unknown_and_unreachable_machine_diagnostics_are_preserved(tmp_path, capsys):
    config = _fake_config(tmp_path)
    with patch.object(rmc, "_load_remote_machines", return_value=[]), \
         patch.object(rmc, "_load_all_machine_keys", return_value=["known"]), \
         patch.object(rmc, "_emit_plan") as emit:
        assert rmc._try_machine_handoff(config, "known") == 1
        emit.assert_not_called()
    error = capsys.readouterr().out
    assert "Unknown or unreachable remote machine: known" in error
    assert "Available: known" in error
    with patch.object(cfg, "load_machines_yaml", side_effect=FileNotFoundError()):
        assert rmc._machine_key_for_display(config, "missing") == "missing"
        assert rmc._emit_remote_plan_for_env(config, "missing", "Linux") is None


def test_shared_matching_preserves_environment_rejection(tmp_path):
    entry = _entry("atlas-core", alias="friendly", envs=[("linux", "atlas-linux")])
    config = _fake_config(tmp_path)
    with patch.object(cfg, "load_machines_yaml", return_value={entry.key: entry}), \
         patch.object(rmc, "_emit_plan") as emit:
        assert rmc._emit_remote_plan_for_env(config, "FRIENDLY", "Win") is None
        assert rmc._emit_remote_plan_for_env(config, "FRIENDLY", "unknown") is None
        emit.assert_not_called()


def test_real_subprocess_parses_and_serializes_alias_selected_plan(tmp_path):
    machines = tmp_path / "machines.yaml"
    machines.write_text(
        "machines:\n  atlas-core:\n    alias: friendly\n    display_name: Build Box\n"
        "    ssh:\n      environments:\n"
        "        - name: linux\n          alias: atlas-linux\n",
        encoding="utf-8",
    )
    script = """
import sys
from types import SimpleNamespace
from machine_transport import parse_machines_yaml_file
from agent_worktrees import config as cfg, resolve_machine_cli as rmc
cfg.load_machines_yaml = lambda _anchor: parse_machines_yaml_file(sys.argv[1])
cfg.project_name = lambda: "example-project"
config = SimpleNamespace(default_repo=SimpleNamespace(anchor="fixture"))
raise SystemExit(rmc._emit_remote_plan_for_env(config, "FRIENDLY", "Linux", ["list", "--json"]))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(machines)],
        capture_output=True, text=True, check=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    plan = json.loads(result.stdout)
    assert plan == {
        "action": "remote", "ssh_alias": "atlas-linux", "machine": "atlas-core",
        "display_name": "Build Box Linux",
        "remote_command": "bash -lc 'example-project list --json'",
    }


def test_real_subprocess_ambiguous_plan_returns_json_error(tmp_path):
    machines = tmp_path / "machines.yaml"
    machines.write_text(
        "machines:\n"
        + "".join(
            f"  {key}:\n    alias: shared\n    ssh:\n      environments:\n"
            f"        - name: linux\n          alias: {key}-ssh\n"
            for key in ("first", "second")
        ),
        encoding="utf-8",
    )
    script = """
import sys
from types import SimpleNamespace
from machine_transport import parse_machines_yaml_file
from agent_worktrees import config as cfg, output, resolve_machine_cli as rmc
cfg.load_machines_yaml = lambda _anchor: parse_machines_yaml_file(sys.argv[1])
config = SimpleNamespace(default_repo=SimpleNamespace(anchor="fixture"))
with output.stdout_to_stderr():
    result = rmc._emit_remote_plan_for_env(config, "SHARED", "Linux")
raise SystemExit(result)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(machines)],
        capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 1
    assert json.loads(result.stdout) == {
        "version": 1, "error": "Machine 'SHARED' is ambiguous in topology",
    }
    assert result.stderr == ""

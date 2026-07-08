"""Tests for the non-stdio --remote-cmd result emitter (#47).

A hanging remote command (no-PTY sudo prompt, or a backgrounded process
holding the stdout/stderr channel) used to time out, get killed, and surface
as a silent exit ``-1`` with swallowed output. The emitter must surface any
partial output and fail loudly (exit 124) with a cause hint.
"""

from __future__ import annotations

from types import SimpleNamespace

from agent_codespaces.__main__ import _build_launch_command, _emit_remote_cmd_result


def _result(stdout="", stderr="", exit_code=0, timed_out=False):
    return SimpleNamespace(
        stdout=stdout, stderr=stderr, exit_code=exit_code, timed_out=timed_out
    )


def test_success_passes_through_exit_code(capsys):
    rc = _emit_remote_cmd_result(_result(stdout="hello", exit_code=0), 60.0)
    assert rc == 0
    out = capsys.readouterr()
    assert "hello" in out.out


def test_nonzero_exit_code_preserved(capsys):
    rc = _emit_remote_cmd_result(_result(stderr="boom", exit_code=3), 60.0)
    assert rc == 3
    assert "boom" in capsys.readouterr().err


def test_timeout_fails_loudly_with_124_and_surfaces_output(capsys):
    rc = _emit_remote_cmd_result(
        _result(stdout="partial", exit_code=-1, timed_out=True), 30.0
    )
    assert rc == 124  # not a silent -1
    captured = capsys.readouterr()
    assert "partial" in captured.out  # partial output is not swallowed
    err = captured.err
    assert "[FAIL]" in err
    assert "30s" in err  # the timeout is named
    # cause hints for the common no-PTY hangs
    assert "sudo -n" in err
    assert "nohup" in err


# --- #152: --plugin-dir must fold in ONLY for the --stdio copilot launch ------

_PLUGIN_DIRS = [
    "/home/vscode/.copilot/installed-plugins/copilot-extensions/agent-worktrees",
    "/home/vscode/.copilot/installed-plugins/dev-tmichon/odsp-web-agent",
]


def test_plugin_dirs_folded_for_stdio_launch():
    cmd = _build_launch_command(
        "copilot --acp --stdio --allow-all-tools",
        _PLUGIN_DIRS,
        is_stdio=True,
        relay_env="",
        breadcrumb="true",
    )
    assert cmd is not None
    # both plugin dirs appended to the copilot launch
    for d in _PLUGIN_DIRS:
        assert f'--plugin-dir="{d}"' in cmd


def test_plugin_dirs_NOT_folded_for_plain_remote_cmd():
    """A diagnostic --remote-cmd (non-stdio) must run verbatim -- no
    --plugin-dir spliced onto its tail (#152)."""
    cmd = _build_launch_command(
        "ls ~/.copilot/skills/",
        _PLUGIN_DIRS,
        is_stdio=False,
        relay_env="",
        breadcrumb="true",
    )
    assert cmd is not None
    assert "--plugin-dir" not in cmd
    assert "ls ~/.copilot/skills/" in cmd


def test_no_remote_cmd_returns_none():
    assert _build_launch_command(None, _PLUGIN_DIRS, is_stdio=True,
                                 relay_env="", breadcrumb="true") is None


# --- #160/#77: injected static PATs are neutralized in the launch prelude -----

def test_launch_prelude_scrubs_injected_ms_ado_pat():
    """A dispatched agent must not rely on the expired injected MS_ADO_PAT: the
    launch prelude unsets it (and any _SCRUB_ENV_VARS) BEFORE the agent command
    so the relay path is used instead."""
    from agent_codespaces.__main__ import _SCRUB_ENV_VARS

    assert "MS_ADO_PAT" in _SCRUB_ENV_VARS
    scrub = "".join(f"unset {v}; " for v in _SCRUB_ENV_VARS)
    cmd = _build_launch_command(
        "copilot --acp --stdio --allow-all-tools", [],
        is_stdio=True, relay_env=scrub, breadcrumb="true",
    )
    assert cmd is not None
    assert "unset MS_ADO_PAT" in cmd
    # The unset must precede the copilot launch.
    assert cmd.index("unset MS_ADO_PAT") < cmd.index("copilot --acp")


def test_build_relay_env_scrub_survives_relay_exports():
    """Regression guard (dev46 bug): the PAT scrub must survive whether or not
    the relay is used -- the relay exports must be APPENDED, never replace it."""
    from agent_codespaces.__main__ import _SCRUB_ENV_VARS, _build_relay_env

    assert "MS_ADO_PAT" in _SCRUB_ENV_VARS

    # With relay: scrub AND exports present, scrub first.
    with_relay = _build_relay_env(9857, "tok", use_relay=True)
    assert "unset MS_ADO_PAT" in with_relay
    assert "LC_GIT_CREDENTIAL_RELAY=9857" in with_relay
    assert with_relay.index("unset MS_ADO_PAT") < with_relay.index(
        "export LC_GIT_CREDENTIAL_RELAY"
    )

    # Without relay: scrub still present, no relay exports.
    without = _build_relay_env(9857, None, use_relay=False)
    assert "unset MS_ADO_PAT" in without
    assert "LC_GIT_CREDENTIAL_RELAY" not in without

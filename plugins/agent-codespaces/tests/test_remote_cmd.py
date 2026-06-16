"""Tests for the non-stdio --remote-cmd result emitter (#47).

A hanging remote command (no-PTY sudo prompt, or a backgrounded process
holding the stdout/stderr channel) used to time out, get killed, and surface
as a silent exit ``-1`` with swallowed output. The emitter must surface any
partial output and fail loudly (exit 124) with a cause hint.
"""

from __future__ import annotations

from types import SimpleNamespace

from agent_codespaces.__main__ import _emit_remote_cmd_result


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

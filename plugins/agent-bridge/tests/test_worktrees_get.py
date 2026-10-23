"""The optional caller lookup owns its finite subprocess tree."""

import subprocess

import pytest

from agent_bridge import __main__ as cli
from agent_bridge import provider_process


@pytest.mark.parametrize("stdout,expected", [
    ("  workspace\n", "workspace"),
    ("", None),
    (" \n", None),
    ("workspace-\u03b1\n", "workspace-\u03b1"),
])
def test_lookup_keeps_exact_command_deadline_and_output(monkeypatch, stdout, expected):
    calls = []
    monkeypatch.setattr(cli.shutil, "which", lambda _: "selected-worktrees")

    def capture(argv, *, timeout):
        calls.append((argv, timeout))
        return subprocess.CompletedProcess(argv, 0, stdout, "")

    monkeypatch.setattr(provider_process, "run_captured", capture)
    assert cli._worktrees_get("worktree-dir") == expected
    assert calls == [(["selected-worktrees", "get", "worktree-dir"], 8)]


def test_absent_optional_lookup_does_not_launch(monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda _: None)
    monkeypatch.setattr(provider_process, "run_captured", lambda *a, **k: pytest.fail("absent"))
    assert cli._worktrees_get("worktree-dir") is None


@pytest.mark.parametrize("error", [
    OSError(2, "private command error"),
    subprocess.TimeoutExpired("private command", 8, stderr="private diagnostic"),
    UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid"),
    RuntimeError("private ownership detail"),
])
def test_lookup_failure_is_bounded_metadata_not_child_output(monkeypatch, caplog, error):
    monkeypatch.setattr(cli.shutil, "which", lambda _: "selected-worktrees")

    def capture(*args, **kwargs):
        raise error

    monkeypatch.setattr(provider_process, "run_captured", capture)
    assert cli._worktrees_get("worktree-dir") is None
    assert f"Worktree lookup failed (worktree-dir): {type(error).__name__}" in caplog.text
    assert "private" not in caplog.text


def test_nonzero_lookup_logs_exit_without_opaque_streams(monkeypatch, caplog):
    monkeypatch.setattr(cli.shutil, "which", lambda _: "selected-worktrees")
    monkeypatch.setattr(provider_process, "run_captured", lambda *a, **k:
                        subprocess.CompletedProcess(a[0], 7, "private stdout", "private stderr"))
    assert cli._worktrees_get("worktree-dir") is None
    assert "Worktree lookup failed (worktree-dir): exit 7" in caplog.text
    assert "private" not in caplog.text

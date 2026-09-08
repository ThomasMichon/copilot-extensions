"""Guard passive-daemon flags for recurring console descendants."""

from __future__ import annotations

import subprocess

import pytest

from agent_bridge import __main__ as main

# Win32 process-creation constants, resolved with a getattr fallback so this
# test runs on non-Windows CI (where subprocess lacks these attributes) while
# still asserting against the real Windows values.
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def test_win32_uses_hidden_console_and_job_breakaway(monkeypatch):
    monkeypatch.setattr(main.sys, "platform", "win32")
    monkeypatch.setattr(
        main,
        "windowless_daemon_kwargs",
        lambda **_kwargs: {
            "creationflags": CREATE_NO_WINDOW | CREATE_BREAKAWAY_FROM_JOB
        },
    )
    flags = main._passive_daemon_creationflags()
    assert flags & CREATE_NO_WINDOW
    assert flags & CREATE_BREAKAWAY_FROM_JOB


def test_non_windows_no_flags(monkeypatch):
    monkeypatch.setattr(main.sys, "platform", "linux")
    assert main._passive_daemon_creationflags() == 0


def test_passive_daemon_stdio_kwargs_redirects_to_log_files(tmp_path, monkeypatch):
    """Regression test: a passive daemon spawned without an explicit stdio
    redirect inherits whatever fds the launcher held open at spawn time (on
    POSIX, `start_new_session=True` alone does not close/redirect fds). A
    long-running daemon holding onto an inherited pipe write-end (e.g. a
    deploy script's own stdout piped through `| tail`) means that pipe never
    sees EOF, even long after the deploy's own work finished. Every platform
    must get real stdout/stderr/stdin redirects, not just Windows."""
    import agent_bridge.config as config_module
    monkeypatch.setattr(config_module, "config_dir", lambda: tmp_path)

    kwargs, opened_streams = main._passive_daemon_stdio_kwargs()
    try:
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["stdout"] is not None
        assert kwargs["stderr"] is not None
        # Real, distinct file objects -- not None (which would mean "inherit
        # the caller's fd", the exact bug this guards against) and not the
        # same stream reused for both.
        assert kwargs["stdout"] is not kwargs["stderr"]
        assert kwargs["stdout"].name == str(tmp_path / "agent-bridge.log")
        assert kwargs["stderr"].name == str(tmp_path / "agent-bridge-err.log")
        assert set(opened_streams) == {kwargs["stdout"], kwargs["stderr"]}
    finally:
        for stream in opened_streams:
            stream.close()


def test_passive_daemon_stdio_kwargs_closes_first_handle_if_second_open_fails(
    tmp_path, monkeypatch,
):
    """If opening the second log file fails (e.g. a permission/IO error), the
    first handle must not leak -- important because this helper runs during
    deploy/cutover, where a leak would accumulate across retries."""
    import agent_bridge.config as config_module
    monkeypatch.setattr(config_module, "config_dir", lambda: tmp_path)

    import builtins
    real_open = builtins.open
    opened = []

    def fake_open(path, mode="r", *a, **kw):
        f = real_open(path, mode, *a, **kw)
        opened.append(f)
        if str(path).endswith("agent-bridge-err.log"):
            # A real filesystem open() failure would raise before ever
            # returning a handle; close this test double's own handle
            # immediately so the simulation doesn't itself leak an fd.
            f.close()
            raise OSError("permission denied (simulated)")
        return f

    monkeypatch.setattr(main, "open", fake_open, raising=False)
    with pytest.raises(OSError, match="permission denied"):
        main._passive_daemon_stdio_kwargs()

    # Both handles must be closed: the first by the production close-on-
    # failure path, the second by this fake_open's own cleanup above.
    assert opened[0].closed
    assert opened[1].closed


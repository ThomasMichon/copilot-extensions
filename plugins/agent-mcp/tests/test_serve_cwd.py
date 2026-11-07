"""Regression tests for the resident `serve` daemon's pinned CWD.

Confirmed live (private-downstream-repo): a long-running `agent-mcp serve` daemon
inherited whatever directory happened to be current when it was launched (a
worktree, an install-time backup dir, ...). Once that directory was later
removed, every relative-path subprocess the daemon spawned on a caller's
behalf (a bridge's `auth.command`, e.g. a vault-token helper) failed with
ENOENT for the rest of the daemon's (long) life -- silently, since the daemon
itself never crashed, only its subprocess spawns did. `_cmd_serve` now pins
the daemon's own CWD to the stable `AGENT_MCP_HOME` directory before doing
anything else, so its own working directory can never be an ephemeral one
that later vanishes out from under it.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_mcp import ipc
from agent_mcp.__main__ import _cmd_serve


def test_default_home_dir_respects_env_override(tmp_path, monkeypatch):
    custom = tmp_path / "custom-agent-mcp-home"
    monkeypatch.setenv("AGENT_MCP_HOME", str(custom))
    assert ipc.default_home_dir() == custom


def test_default_home_dir_falls_back_to_dot_agent_mcp(monkeypatch):
    monkeypatch.delenv("AGENT_MCP_HOME", raising=False)
    assert ipc.default_home_dir() == Path.home() / ".agent-mcp"


def test_default_socket_path_still_lives_under_home_dir(tmp_path, monkeypatch):
    custom = tmp_path / "custom-agent-mcp-home"
    monkeypatch.setenv("AGENT_MCP_HOME", str(custom))
    assert ipc.default_socket_path() == custom / "serve.sock"


def test_cmd_serve_pins_cwd_to_agent_mcp_home_before_serving(tmp_path, monkeypatch):
    """The core regression: an ephemeral launch-time cwd must never leak into
    the long-lived daemon's own working directory.
    """
    home_dir = tmp_path / "agent-mcp-home"
    ephemeral_launch_dir = tmp_path / "ephemeral-worktree"
    ephemeral_launch_dir.mkdir()
    monkeypatch.setenv("AGENT_MCP_HOME", str(home_dir))

    original_cwd = Path.cwd()
    monkeypatch.chdir(ephemeral_launch_dir)
    try:
        assert not home_dir.exists(), "must not pre-exist -- proves mkdir(parents=True) runs"

        observed_cwd_at_serve_start: list[Path] = []

        class _StubServer:
            def __init__(self, *args, **kwargs):
                observed_cwd_at_serve_start.append(Path.cwd())

            async def serve_forever(self):
                return None

        with patch("agent_mcp.serve.Server", _StubServer), \
             patch("asyncio.run", lambda coro: coro.close()):
            args = argparse.Namespace(
                passive=False, socket=None, idle_timeout=300.0, control_port=None,
            )
            rc = _cmd_serve(args)

        assert rc == 0
        assert home_dir.is_dir(), "AGENT_MCP_HOME must have been created"
        assert observed_cwd_at_serve_start == [home_dir.resolve()]
        assert Path.cwd() == home_dir.resolve(), (
            "the daemon's own cwd must now be the stable home dir, not the "
            "ephemeral directory that happened to be current at launch"
        )
    finally:
        os.chdir(original_cwd)


def test_cmd_serve_resolves_relative_socket_against_launch_cwd_not_home(
    tmp_path, monkeypatch
):
    """A relative --socket must keep meaning "relative to the caller's cwd"
    even though the daemon's own cwd changes out from under it immediately
    after resolution.
    """
    home_dir = tmp_path / "agent-mcp-home"
    launch_dir = tmp_path / "launch-dir"
    launch_dir.mkdir()
    monkeypatch.setenv("AGENT_MCP_HOME", str(home_dir))

    original_cwd = Path.cwd()
    monkeypatch.chdir(launch_dir)
    try:
        observed_socket_paths: list[str] = []

        class _StubServer:
            def __init__(self, socket_path, *args, **kwargs):
                observed_socket_paths.append(socket_path)

            async def serve_forever(self):
                return None

        with patch("agent_mcp.serve.Server", _StubServer), \
             patch("asyncio.run", lambda coro: coro.close()):
            args = argparse.Namespace(
                passive=False, socket="relative.sock", idle_timeout=300.0,
                control_port=None,
            )
            rc = _cmd_serve(args)

        assert rc == 0
        assert observed_socket_paths == [str((launch_dir / "relative.sock").resolve())]
    finally:
        os.chdir(original_cwd)


def test_cmd_serve_passive_without_socket_still_fails_fast_before_chdir(
    tmp_path, monkeypatch, capsys
):
    """The pre-existing --passive-requires-explicit-socket guard must still
    run (and still refuse) before any cwd is touched.
    """
    home_dir = tmp_path / "agent-mcp-home"
    monkeypatch.setenv("AGENT_MCP_HOME", str(home_dir))
    original_cwd = Path.cwd()

    args = argparse.Namespace(
        passive=True, socket=None, idle_timeout=300.0, control_port=None,
    )
    rc = _cmd_serve(args)

    assert rc == 2
    assert "requires an explicit --socket" in capsys.readouterr().err
    assert not home_dir.exists(), "must not have chdir'd/created anything on the refused path"
    assert Path.cwd() == original_cwd


@pytest.mark.parametrize("selection", ("socket", "home"))
def test_actual_cli_parent_relative_paths_reach_admitted_server(tmp_path, monkeypatch, selection):
    from agent_mcp import serve
    from agent_mcp.__main__ import main

    launch = tmp_path / "launch"
    home = tmp_path / "state"
    launch.mkdir()
    home.mkdir()
    monkeypatch.chdir(launch)
    monkeypatch.setenv("AGENT_MCP_HOME", "../state" if selection == "home" else str(home))
    observed = []

    class ObservedServer(serve.Server):
        async def _start_control_listener(self):
            await super()._start_control_listener()
            assert self.socket_path == home / "serve.sock"
            assert Path.cwd() == home
            assert os.environ["AGENT_MCP_HOME"] == str(home)
            observed.append(True)
            self.stop()

    command = ["serve"]
    if selection == "socket":
        command += ["--socket", "../state/serve.sock"]
    with patch.object(serve, "Server", ObservedServer):
        assert main(command) == 0
    assert observed == [True]


def test_actual_server_linked_runtime_parent_refuses_before_lease(tmp_path, monkeypatch):
    import asyncio
    from agent_mcp import serve

    target = tmp_path / "target"
    target.mkdir()
    alias = tmp_path / "linked"
    try:
        alias.symlink_to(target, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"native directory symlink unavailable: {error}")
    server = serve.Server(alias / "serve.sock")
    monkeypatch.setattr(server, "_acquire_lease", lambda: pytest.fail("linked root reached lease"))
    with pytest.raises((OSError, ValueError, RuntimeError)):
        asyncio.run(server.serve_forever())
    assert list(target.iterdir()) == []


def test_cli_record_uses_selected_socket_root_before_cwd_and_server(tmp_path, monkeypatch):
    from agent_procutil import daemon_instances as instances
    from agent_mcp import serve

    home = tmp_path / "home"
    socket_root = tmp_path / "socket-state"
    socket_root.mkdir()
    monkeypatch.setenv("AGENT_MCP_HOME", str(home))
    acquired = []
    real = instances.admit_daemon_instance

    def admit(*args, **kwargs):
        guard = real(*args, **kwargs)
        acquired.append(guard)
        return guard

    class ObservedServer:
        def __init__(self, *args, **kwargs):
            assert len(acquired) == 1
            row = next(row for row in instances.inspect_instances(acquired[0].receipt.registry_root)
                       if row["instance_id"] == acquired[0].receipt.instance_id)
            assert row["runtime_root"] == str(socket_root)

        async def serve_forever(self):
            pass

    monkeypatch.setattr(instances, "admit_daemon_instance", admit)
    monkeypatch.setattr(serve, "Server", ObservedServer)
    original = Path.cwd()
    try:
        assert _cmd_serve(argparse.Namespace(
            passive=False, socket=str(socket_root / "serve.sock"),
            idle_timeout=300.0, control_port=None,
        )) == 0
    finally:
        os.chdir(original)
    assert acquired[0]._closed


def test_cli_linked_socket_root_refuses_before_home_or_server(tmp_path, monkeypatch):
    from agent_mcp import serve

    socket_root = tmp_path / "socket-state"
    socket_root.mkdir()
    alias = tmp_path / "linked-socket-state"
    try:
        alias.symlink_to(socket_root, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"native directory symlink unavailable: {error}")
    home = tmp_path / "home"
    monkeypatch.setenv("AGENT_MCP_HOME", str(home))
    server = []
    monkeypatch.setattr(serve, "Server", lambda *args, **kwargs: server.append(True))
    original = Path.cwd()
    try:
        with pytest.raises((OSError, ValueError)):
            _cmd_serve(argparse.Namespace(
                passive=False, socket=str(alias / "serve.sock"),
                idle_timeout=300.0, control_port=None,
            ))
    finally:
        os.chdir(original)
    assert server == [] and not home.exists()

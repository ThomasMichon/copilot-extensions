"""Tests for ConnectionManager."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ssh_manager.config_sources import SSHConfig, SSHProfileSource
from ssh_manager.manager import (
    CommandResult,
    ConnectionInfo,
    ConnectionManager,
    get_default_manager,
)
from ssh_manager.platform import MultiplexMode, PlatformInfo


@pytest.fixture
def unix_platform(tmp_path):
    """A Unix-like platform with ControlMaster support."""
    return PlatformInfo(
        mode=MultiplexMode.CONTROL_MASTER,
        socket_dir=tmp_path / "sockets",
        max_socket_path=108,
    )


@pytest.fixture
def win_platform(tmp_path):
    """A Windows platform without ControlMaster."""
    return PlatformInfo(
        mode=MultiplexMode.DIRECT,
        socket_dir=tmp_path / "sockets",
        max_socket_path=260,
    )


@pytest.fixture
def source():
    """A basic SSH profile source."""
    return SSHProfileSource(host_alias="test-host")


class TestCommandResult:
    """CommandResult dataclass tests."""

    def test_ok_on_zero_exit(self):
        r = CommandResult(stdout="hello", stderr="", exit_code=0)
        assert r.ok is True

    def test_not_ok_on_nonzero_exit(self):
        r = CommandResult(stdout="", stderr="error", exit_code=1)
        assert r.ok is False

    def test_not_ok_on_timeout(self):
        r = CommandResult(stdout="", stderr="", exit_code=0, timed_out=True)
        assert r.ok is False

    def test_check_raises_on_failure(self):
        r = CommandResult(stdout="", stderr="bad", exit_code=1)
        with pytest.raises(Exception):
            r.check()

    def test_check_raises_on_timeout(self):
        r = CommandResult(stdout="", stderr="", exit_code=0, timed_out=True)
        with pytest.raises(TimeoutError):
            r.check()


class TestConnectionManagerDirect:
    """ConnectionManager tests for direct (Windows) mode."""

    @pytest.mark.asyncio
    async def test_ensure_connected_direct_mode(self, win_platform, source):
        """Direct mode creates a connection entry without a master process."""
        manager = ConnectionManager(platform=win_platform)
        info = await manager.ensure_connected("test-host", source)
        assert info.host == "test-host"
        assert info.master_process is None
        assert info.multiplexed is False

    @pytest.mark.asyncio
    async def test_ensure_connected_is_idempotent(self, win_platform, source):
        """Second call returns same connection."""
        manager = ConnectionManager(platform=win_platform)
        info1 = await manager.ensure_connected("test-host", source)
        info2 = await manager.ensure_connected("test-host", source)
        assert info1 is info2

    @pytest.mark.asyncio
    async def test_disconnect_removes_connection(self, win_platform, source):
        """After disconnect, the host is removed from connections."""
        manager = ConnectionManager(platform=win_platform)
        await manager.ensure_connected("test-host", source)
        assert len(manager.list_connections()) == 1
        await manager.disconnect("test-host")
        assert len(manager.list_connections()) == 0

    @pytest.mark.asyncio
    async def test_disconnect_nonexistent_is_noop(self, win_platform):
        """Disconnecting a host that isn't connected should not raise."""
        manager = ConnectionManager(platform=win_platform)
        await manager.disconnect("no-such-host")

    @pytest.mark.asyncio
    async def test_list_connections(self, win_platform):
        """list_connections returns all active connections."""
        manager = ConnectionManager(platform=win_platform)
        s1 = SSHProfileSource(host_alias="host-a")
        s2 = SSHProfileSource(host_alias="host-b")
        await manager.ensure_connected("host-a", s1)
        await manager.ensure_connected("host-b", s2)
        conns = manager.list_connections()
        assert len(conns) == 2
        hosts = {c.host for c in conns}
        assert hosts == {"host-a", "host-b"}

    @pytest.mark.asyncio
    async def test_disconnect_all(self, win_platform):
        """disconnect_all removes everything."""
        manager = ConnectionManager(platform=win_platform)
        s1 = SSHProfileSource(host_alias="host-a")
        s2 = SSHProfileSource(host_alias="host-b")
        await manager.ensure_connected("host-a", s1)
        await manager.ensure_connected("host-b", s2)
        await manager.disconnect_all()
        assert len(manager.list_connections()) == 0


class TestConnectionManagerIdentity:
    """Tests for connection identity matching."""

    @pytest.mark.asyncio
    async def test_reconnects_on_identity_change(self, win_platform):
        """Changing user triggers reconnect."""
        manager = ConnectionManager(platform=win_platform)
        s1 = SSHProfileSource(host_alias="server", user="alice")
        s2 = SSHProfileSource(host_alias="server", user="bob")

        info1 = await manager.ensure_connected("server", s1)
        info2 = await manager.ensure_connected("server", s2)
        # Should be a different connection
        assert info1.connection_identity != info2.connection_identity

    @pytest.mark.asyncio
    async def test_reconnects_on_port_forward_change(self, win_platform, source):
        """Changing port forwards triggers reconnect."""
        manager = ConnectionManager(platform=win_platform)
        info1 = await manager.ensure_connected("test-host", source, ["-R 9847:localhost:9847"])
        info2 = await manager.ensure_connected("test-host", source, ["-R 9999:localhost:9999"])
        assert info2.port_forwards == ["-R 9999:localhost:9999"]

    @pytest.mark.asyncio
    async def test_direct_mode_splits_port_forward_into_tokens(
        self, win_platform, source
    ):
        """Direct-mode forwards must be split into separate argv tokens.

        ``-R 9857:127.0.0.1:9857`` has to reach ssh as two args (``-R`` and the
        spec), not a single ``"-R 9857:127.0.0.1:9857"`` token -- otherwise the
        reverse forward (e.g. the credential relay) is malformed and silently
        does not bind. Regression guard for the agent-codespaces relay forward.
        """
        manager = ConnectionManager(platform=win_platform)
        info = await manager.ensure_connected(
            "test-host", source, ["-R 9857:127.0.0.1:9857"]
        )
        args = manager._mux_ssh_args(info)
        assert "-R" in args
        assert "9857:127.0.0.1:9857" in args
        # The unsplit single-token form must NOT be present.
        assert "-R 9857:127.0.0.1:9857" not in args
        # -R is immediately followed by its spec.
        assert args[args.index("-R") + 1] == "9857:127.0.0.1:9857"


class TestConnectionManagerExec:
    """Tests for exec_command and open_stdio_channel."""

    @pytest.mark.asyncio
    async def test_exec_command_requires_connection(self, win_platform):
        """exec_command raises if host is not connected."""
        manager = ConnectionManager(platform=win_platform)
        with pytest.raises(RuntimeError, match="No connection"):
            await manager.exec_command("no-host", "echo hello")

    @pytest.mark.asyncio
    async def test_open_stdio_requires_connection(self, win_platform):
        """open_stdio_channel raises if host is not connected."""
        manager = ConnectionManager(platform=win_platform)
        with pytest.raises(RuntimeError, match="No connection"):
            await manager.open_stdio_channel("no-host", "bash")

    @pytest.mark.asyncio
    async def test_exec_command_builds_correct_args(self, win_platform, source):
        """Verify SSH args are constructed correctly for exec_command."""
        manager = ConnectionManager(platform=win_platform)
        await manager.ensure_connected("test-host", source)

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"output\n", b""))
        mock_proc.returncode = 0

        with patch("ssh_manager.manager.asyncio.create_subprocess_exec",
                    return_value=mock_proc) as mock_exec:
            result = await manager.exec_command("test-host", "uname -a")

        assert result.ok
        assert result.stdout == "output"
        assert result.exit_code == 0

        # Verify SSH was called with expected args
        call_args = mock_exec.call_args[0]
        assert call_args[0] == "ssh"
        assert "test-host" in call_args
        assert "uname -a" in call_args
        assert "BatchMode=yes" in call_args


class TestGetDefaultManager:
    """Tests for the convenience singleton."""

    def test_returns_same_instance(self):
        # Reset for test isolation
        import ssh_manager.manager as mod
        mod._default_manager = None

        m1 = get_default_manager()
        m2 = get_default_manager()
        assert m1 is m2

        # Cleanup
        mod._default_manager = None

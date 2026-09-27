"""Tests for ConnectionManager."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

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
    async def test_ensure_connected_offloads_blocking_config_fetch(
        self, win_platform
    ):
        """A blocking get_ssh_config() must not stall the event loop (#166).

        A CodespaceConfigSource's get_ssh_config() runs a synchronous
        ``gh codespace ssh --config`` that cold-starts a Shutdown CodeSpace and
        can block 60-120s. If ensure_connected() called it directly on the loop
        the daemon would stop serving /health and the watchdog would force-exit
        it mid-connect. This proves the call is off-loaded: a concurrent coroutine
        keeps making progress while get_ssh_config() blocks.
        """
        inner = SSHProfileSource(host_alias="test-host")

        class _BlockingSource:
            def get_ssh_config(self):
                time.sleep(0.3)  # synchronous stall, like a cold-boot config fetch
                return inner.get_ssh_config()

        manager = ConnectionManager(platform=win_platform)
        ticks = 0

        async def _ticker():
            nonlocal ticks
            while True:
                ticks += 1
                await asyncio.sleep(0.01)

        ticker = asyncio.create_task(_ticker())
        try:
            info = await manager.ensure_connected("test-host", _BlockingSource())
        finally:
            ticker.cancel()

        assert info.host == "test-host"
        # On-loop the 0.3s block would let the 10ms ticker fire at most once;
        # off-loaded it keeps ticking (~25-30 times). A margin guards flake.
        assert ticks >= 5

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
        await manager.ensure_connected("test-host", source, ["-R 9847:localhost:9847"])
        info2 = await manager.ensure_connected("test-host", source, ["-R 9999:localhost:9999"])
        assert info2.port_forwards == ["-R 9999:localhost:9999"]

    @pytest.mark.asyncio
    async def test_preserves_existing_forwards_for_shared_carrier(
        self, win_platform, source
    ):
        manager = ConnectionManager(platform=win_platform)
        info1 = await manager.ensure_connected(
            "test-host", source, ["-R 9847:localhost:9847"]
        )

        info2 = await manager.ensure_connected(
            "test-host",
            source,
            preserve_existing_forwards=True,
        )

        assert info2 is info1
        assert info2.port_forwards == ["-R 9847:localhost:9847"]

    @pytest.mark.asyncio
    async def test_separate_carrier_namespace_survives_forwarded_connection(
        self, unix_platform, source
    ):
        manager = ConnectionManager(platform=unix_platform)
        master = AsyncMock()
        master.returncode = None
        with patch.object(
            manager,
            "_start_control_master",
            new=AsyncMock(return_value=master),
        ):
            carrier = await manager.ensure_connected(
                "carrier:test-host", source
            )
            forwarded = await manager.ensure_connected(
                "test-host", source, ["-R 9847:localhost:9847"]
            )

        connections = {
            connection.host: connection for connection in manager.list_connections()
        }
        assert connections["carrier:test-host"] is carrier
        assert connections["test-host"] is forwarded
        assert carrier.port_forwards == []
        assert forwarded.port_forwards == ["-R 9847:localhost:9847"]
        assert carrier.socket_path != forwarded.socket_path

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
    async def test_open_stdio_uses_large_frame_limit(self, win_platform, source):
        """open_stdio_channel must raise the StreamReader frame limit.

        ACP session/update frames carrying a large tool result can exceed
        asyncio's 64 KiB default per-line limit, overflowing readline() and
        tearing down the channel ("Connection closed"). The channel must use
        the large limit so remote ACP sessions behave like local ones.
        """
        from ssh_manager.manager import _STDIO_CHANNEL_LIMIT_BYTES

        manager = ConnectionManager(platform=win_platform)
        await manager.ensure_connected("test-host", source)

        mock_proc = AsyncMock()
        with patch("ssh_manager.manager.asyncio.create_subprocess_exec",
                    return_value=mock_proc) as mock_exec:
            result = await manager.open_stdio_channel("test-host", "copilot --acp --stdio")

        assert result is mock_proc
        assert mock_exec.call_args[1]["limit"] == _STDIO_CHANNEL_LIMIT_BYTES
        assert _STDIO_CHANNEL_LIMIT_BYTES > 65536

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

    @pytest.mark.asyncio
    async def test_disconnect_terminates_active_child_tree(self, win_platform, source):
        """Disconnect must reap in-flight direct SSH children."""
        manager = ConnectionManager(platform=win_platform)
        info = await manager.ensure_connected("test-host", source)
        child = AsyncMock()
        child.returncode = None
        child.pid = 12345
        info.child_processes.append(child)

        with patch(
            "ssh_manager.manager._terminate_process_tree",
            new=AsyncMock(),
        ) as terminate:
            await manager.disconnect("test-host")

        terminate.assert_awaited_once_with(child)
        assert child not in info.child_processes

    @pytest.mark.asyncio
    async def test_disconnect_reaps_timed_out_graceful_exit(
        self, unix_platform, tmp_path
    ):
        manager = ConnectionManager(platform=unix_platform)
        socket_path = tmp_path / "master.sock"
        socket_path.touch()
        master = AsyncMock()
        master.returncode = 0
        info = ConnectionInfo(
            host="test-host",
            config=SSHConfig(host_alias="test-host"),
            socket_path=socket_path,
            master_process=master,
            platform=unix_platform,
        )
        manager._connections["test-host"] = info
        graceful = AsyncMock()
        graceful.returncode = None
        graceful.wait = AsyncMock(side_effect=asyncio.TimeoutError)

        with (
            patch(
                "ssh_manager.manager.asyncio.create_subprocess_exec",
                return_value=graceful,
            ),
            patch(
                "ssh_manager.manager._terminate_process_tree",
                new=AsyncMock(),
            ) as terminate,
        ):
            await manager.disconnect("test-host")

        terminate.assert_awaited_once_with(graceful)


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

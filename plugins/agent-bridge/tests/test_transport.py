"""Tests for transport.py -- SSH spawn and SpawnTarget serialization."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_bridge.transport import (
    AgentProcess,
    SpawnTarget,
    spawn,
    spawn_local,
    spawn_ssh,
)


class TestSpawnTargetSerialization:

    def test_roundtrip_local(self):
        target = SpawnTarget(type="local", cwd="/tmp/test")
        restored = SpawnTarget.from_json(target.to_json())
        assert restored.type == "local"
        assert restored.cwd == "/tmp/test"
        assert restored.host is None

    def test_roundtrip_ssh(self):
        target = SpawnTarget(
            type="ssh",
            cwd="/home/user/src",
            host="server-a",
            user="deploy",
            copilot_path="/usr/local/bin/copilot",
            copilot_args=["--extensions-dir", "/opt/ext"],
            env={"MY_VAR": "hello"},
            project="my-project",
        )
        restored = SpawnTarget.from_json(target.to_json())
        assert restored.type == "ssh"
        assert restored.host == "server-a"
        assert restored.user == "deploy"
        assert restored.copilot_path == "/usr/local/bin/copilot"
        assert restored.copilot_args == ["--extensions-dir", "/opt/ext"]
        assert restored.env == {"MY_VAR": "hello"}
        assert restored.project == "my-project"

    def test_to_json_produces_valid_json(self):
        target = SpawnTarget(type="ssh", host="test", cwd=".")
        data = json.loads(target.to_json())
        assert data["type"] == "ssh"
        assert data["host"] == "test"


class TestSpawnSsh:

    @pytest.mark.asyncio
    async def test_ssh_command_structure(self):
        """Verify SSH command includes all hardening flags."""
        target = SpawnTarget(
            type="ssh",
            cwd="/home/deploy/src",
            host="server-a",
            user="deploy",
        )

        mock_proc = MagicMock()
        mock_proc.pid = 99999
        mock_proc.returncode = None

        with patch("agent_bridge.transport.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=mock_proc)
            mock_asyncio.subprocess = asyncio.subprocess

            result = await spawn_ssh(target)
            assert result.proc == mock_proc

            call_args = mock_asyncio.create_subprocess_exec.call_args
            args = call_args[0]

            # Verify SSH command structure
            assert args[0] == "ssh"
            assert "-T" in args
            assert "BatchMode=yes" in " ".join(args)
            assert "ConnectTimeout=15" in " ".join(args)
            assert "ServerAliveInterval=30" in " ".join(args)
            assert "deploy@server-a" in args

            # Verify remote command includes cd and exec
            remote_cmd = args[-1]
            assert "cd " in remote_cmd
            assert "exec " in remote_cmd
            assert "copilot" in remote_cmd
            assert "--acp" in remote_cmd
            assert "--stdio" in remote_cmd

    @pytest.mark.asyncio
    async def test_ssh_without_user(self):
        """SSH target without user should omit user@ prefix."""
        target = SpawnTarget(type="ssh", cwd=".", host="myhost")

        mock_proc = MagicMock()
        mock_proc.pid = 99999
        mock_proc.returncode = None

        with patch("agent_bridge.transport.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=mock_proc)
            mock_asyncio.subprocess = asyncio.subprocess

            await spawn_ssh(target)

            call_args = mock_asyncio.create_subprocess_exec.call_args
            args = call_args[0]
            # Should be just "myhost", not "None@myhost"
            assert "myhost" in args
            assert "None@myhost" not in args

    @pytest.mark.asyncio
    async def test_ssh_with_env_vars(self):
        """SSH command should export env vars on the remote side."""
        target = SpawnTarget(
            type="ssh", cwd=".", host="testhost", user="user",
            env={"FOO": "bar", "BAZ": "qux with spaces"},
        )

        mock_proc = MagicMock()
        mock_proc.pid = 99999
        mock_proc.returncode = None

        with patch("agent_bridge.transport.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=mock_proc)
            mock_asyncio.subprocess = asyncio.subprocess

            await spawn_ssh(target)

            remote_cmd = mock_asyncio.create_subprocess_exec.call_args[0][-1]
            assert "export FOO=" in remote_cmd
            assert "export BAZ=" in remote_cmd

    @pytest.mark.asyncio
    async def test_ssh_with_extra_args(self):
        """SSH command should pass extra copilot args."""
        target = SpawnTarget(
            type="ssh", cwd=".", host="testhost",
            copilot_args=["--extensions-dir", "/opt/ext"],
        )

        mock_proc = MagicMock()
        mock_proc.pid = 99999
        mock_proc.returncode = None

        with patch("agent_bridge.transport.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=mock_proc)
            mock_asyncio.subprocess = asyncio.subprocess

            await spawn_ssh(target)

            remote_cmd = mock_asyncio.create_subprocess_exec.call_args[0][-1]
            assert "--extensions-dir" in remote_cmd

    @pytest.mark.asyncio
    async def test_ssh_requires_host(self):
        """SSH spawn without host should raise ValueError."""
        target = SpawnTarget(type="ssh", cwd=".")
        with pytest.raises(ValueError, match="host"):
            await spawn_ssh(target)

    @pytest.mark.asyncio
    async def test_ssh_with_project_uses_binstub(self):
        """SSH with project should use the binstub instead of copilot."""
        target = SpawnTarget(
            type="ssh", cwd="/home/user/src", host="server-a", user="deploy",
            project="my-project",
            copilot_args=["--allow-all"],
        )

        mock_proc = MagicMock()
        mock_proc.pid = 99999
        mock_proc.returncode = None

        with patch("agent_bridge.transport.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=mock_proc)
            mock_asyncio.subprocess = asyncio.subprocess

            await spawn_ssh(target)

            remote_cmd = mock_asyncio.create_subprocess_exec.call_args[0][-1]
            assert "my-project" in remote_cmd
            assert "--base" in remote_cmd
            assert "--no-mux" in remote_cmd
            assert "--acp" in remote_cmd
            assert "--stdio" in remote_cmd
            assert "--allow-all" in remote_cmd
            # Should NOT contain cd or export (binstub handles setup)
            assert "cd " not in remote_cmd

    @pytest.mark.asyncio
    async def test_ssh_without_project_uses_direct_copilot(self):
        """SSH without project should use cd + copilot (legacy behavior)."""
        target = SpawnTarget(
            type="ssh", cwd="/home/user/src", host="server-a",
        )

        mock_proc = MagicMock()
        mock_proc.pid = 99999
        mock_proc.returncode = None

        with patch("agent_bridge.transport.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=mock_proc)
            mock_asyncio.subprocess = asyncio.subprocess

            await spawn_ssh(target)

            remote_cmd = mock_asyncio.create_subprocess_exec.call_args[0][-1]
            assert "cd " in remote_cmd
            assert "exec " in remote_cmd
            assert "copilot" in remote_cmd


class TestSpawnLocal:

    @pytest.mark.asyncio
    async def test_local_with_project_uses_binstub(self):
        """Local spawn with project should use the binstub."""
        target = SpawnTarget(
            type="local", cwd="/tmp/test",
            project="my-project",
            copilot_args=["--allow-all"],
        )

        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.returncode = None

        with patch("agent_bridge.transport.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=mock_proc)
            mock_asyncio.subprocess = asyncio.subprocess

            await spawn_local(target)

            call_args = mock_asyncio.create_subprocess_exec.call_args
            args = call_args[0]
            assert args[0] == "my-project"
            assert "--base" in args
            assert "--no-mux" in args
            assert "--acp" in args
            assert "--stdio" in args
            assert "--allow-all" in args

    @pytest.mark.asyncio
    async def test_local_without_project_uses_copilot_directly(self):
        """Local spawn without project should call copilot directly."""
        target = SpawnTarget(type="local", cwd="/tmp/test")

        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.returncode = None

        with patch("agent_bridge.transport.asyncio") as mock_asyncio, \
             patch("agent_bridge.transport._find_copilot", return_value="copilot"):
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=mock_proc)
            mock_asyncio.subprocess = asyncio.subprocess

            await spawn_local(target)

            call_args = mock_asyncio.create_subprocess_exec.call_args
            args = call_args[0]
            assert args[0] == "copilot"


class TestSpawnDispatcher:

    @pytest.mark.asyncio
    async def test_dispatch_local(self):
        """spawn() dispatches to spawn_local for local targets."""
        target = SpawnTarget(type="local", cwd=".")

        with patch("agent_bridge.transport.spawn_local", new_callable=AsyncMock) as mock_local:
            mock_proc = MagicMock()
            mock_local.return_value = mock_proc

            result = await spawn(target)
            mock_local.assert_called_once_with(target)
            assert result == mock_proc

    @pytest.mark.asyncio
    async def test_dispatch_ssh(self):
        """spawn() dispatches to spawn_ssh for SSH targets."""
        target = SpawnTarget(type="ssh", cwd=".", host="testhost")

        with patch("agent_bridge.transport.spawn_ssh", new_callable=AsyncMock) as mock_ssh:
            mock_proc = MagicMock()
            mock_ssh.return_value = mock_proc

            result = await spawn(target)
            mock_ssh.assert_called_once_with(target)
            assert result == mock_proc

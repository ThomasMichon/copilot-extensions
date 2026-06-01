"""Tests for transport.py -- SSH spawn and SpawnTarget serialization."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_bridge.transport import (
    AgentProcess,
    SpawnTarget,
    _wrap_batch_for_windows,
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
            type="ssh", host="server-a", user="deploy",
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
            assert "--base" not in remote_cmd
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
            type="local",
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
            assert "--base" not in args
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
             patch("agent_bridge.transport._find_copilot", return_value="copilot"), \
             patch("agent_bridge.transport.shutil") as mock_shutil:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=mock_proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_shutil.which.return_value = None

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


class TestCwdValidation:

    @pytest.mark.asyncio
    async def test_local_without_project_requires_cwd(self):
        """Local spawn without project and without cwd should raise."""
        target = SpawnTarget(type="local")
        with pytest.raises(ValueError, match="requires 'cwd'"):
            await spawn_local(target)

    @pytest.mark.asyncio
    async def test_ssh_without_project_requires_cwd(self):
        """SSH spawn without project and without cwd should raise."""
        target = SpawnTarget(type="ssh", host="testhost")
        with pytest.raises(ValueError, match="requires 'cwd'"):
            await spawn_ssh(target)


class TestWrapBatchForWindows:
    """Tests for _wrap_batch_for_windows -- .cmd/.bat wrapping on Windows."""

    def test_wraps_cmd_file_on_windows(self):
        """A resolved .cmd executable should be wrapped with cmd.exe."""
        args = ["my-project.cmd", "--no-mux", "--acp", "--stdio"]
        env = {"PATH": "C:\\Users\\test\\.local\\bin"}

        with patch("agent_bridge.transport.sys") as mock_sys, \
             patch("agent_bridge.transport.shutil") as mock_shutil:
            mock_sys.platform = "win32"
            mock_shutil.which.return_value = "C:\\Users\\test\\.local\\bin\\my-project.cmd"

            result = _wrap_batch_for_windows(args, env)

        assert result[0].endswith("cmd.exe")
        assert result[1:4] == ["/d", "/s", "/c"]
        assert result[4] == "C:\\Users\\test\\.local\\bin\\my-project.cmd"
        assert "--no-mux" in result
        assert "--acp" in result

    def test_wraps_bat_file_on_windows(self):
        """.bat files should also be wrapped."""
        args = ["launcher.bat", "--stdio"]
        env = {}

        with patch("agent_bridge.transport.sys") as mock_sys, \
             patch("agent_bridge.transport.shutil") as mock_shutil:
            mock_sys.platform = "win32"
            mock_shutil.which.return_value = "C:\\tools\\launcher.bat"

            result = _wrap_batch_for_windows(args, env)

        assert result[0].endswith("cmd.exe")
        assert result[4] == "C:\\tools\\launcher.bat"

    def test_does_not_wrap_exe_on_windows(self):
        """.exe files should not be wrapped."""
        args = ["copilot.exe", "--acp", "--stdio"]
        env = {}

        with patch("agent_bridge.transport.sys") as mock_sys, \
             patch("agent_bridge.transport.shutil") as mock_shutil:
            mock_sys.platform = "win32"
            mock_shutil.which.return_value = "C:\\tools\\copilot.exe"

            result = _wrap_batch_for_windows(args, env)

        assert result[0] == "C:\\tools\\copilot.exe"
        assert "/d" not in result

    def test_noop_on_non_windows(self):
        """Non-Windows platforms should return args unchanged."""
        args = ["my-project", "--acp", "--stdio"]
        env = {}

        with patch("agent_bridge.transport.sys") as mock_sys:
            mock_sys.platform = "linux"

            result = _wrap_batch_for_windows(args, env)

        assert result == args

    def test_which_returns_none_but_literal_is_cmd(self):
        """If which() fails but the literal arg ends in .cmd, still wrap."""
        args = ["my-project.cmd", "--stdio"]
        env = {}

        with patch("agent_bridge.transport.sys") as mock_sys, \
             patch("agent_bridge.transport.shutil") as mock_shutil:
            mock_sys.platform = "win32"
            mock_shutil.which.return_value = None

            result = _wrap_batch_for_windows(args, env)

        assert result[0].endswith("cmd.exe")
        assert result[4] == "my-project.cmd"

    def test_which_resolves_bare_name_to_cmd(self):
        """A bare project name that resolves to .cmd should be wrapped."""
        args = ["aperture-labs", "--no-mux", "--acp", "--stdio"]
        env = {"PATH": "C:\\Users\\test\\.local\\bin"}

        with patch("agent_bridge.transport.sys") as mock_sys, \
             patch("agent_bridge.transport.shutil") as mock_shutil:
            mock_sys.platform = "win32"
            mock_shutil.which.return_value = "C:\\Users\\test\\.local\\bin\\aperture-labs.cmd"

            result = _wrap_batch_for_windows(args, env)

        assert result[0].endswith("cmd.exe")
        assert result[4] == "C:\\Users\\test\\.local\\bin\\aperture-labs.cmd"
        assert "--no-mux" in result

    def test_uses_comspec_env_var(self):
        """Should use COMSPEC if set, not hardcoded cmd.exe."""
        args = ["test.cmd"]
        env = {}

        with patch("agent_bridge.transport.sys") as mock_sys, \
             patch("agent_bridge.transport.shutil") as mock_shutil, \
             patch.dict("os.environ", {"COMSPEC": "C:\\Windows\\System32\\cmd.exe"}):
            mock_sys.platform = "win32"
            mock_shutil.which.return_value = "test.cmd"

            result = _wrap_batch_for_windows(args, env)

        assert result[0] == "C:\\Windows\\System32\\cmd.exe"

    def test_uses_effective_path_for_resolution(self):
        """shutil.which should be called with the env's PATH."""
        args = ["my-project"]
        custom_path = "C:\\custom\\bin"
        env = {"PATH": custom_path}

        with patch("agent_bridge.transport.sys") as mock_sys, \
             patch("agent_bridge.transport.shutil") as mock_shutil:
            mock_sys.platform = "win32"
            mock_shutil.which.return_value = None

            _wrap_batch_for_windows(args, env)

        mock_shutil.which.assert_called_once_with("my-project", path=custom_path)

"""Tests for transport.py -- SSH spawn and SpawnTarget serialization."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent_bridge.transport import (
    _ACP_STDIO_LIMIT_BYTES,
    AgentProcess,
    SpawnTarget,
    _build_remote_cmd,
    _extract_json_object,
    _resolve_worktree_remote,
    _wrap_batch_for_windows,
    spawn,
    spawn_local,
    spawn_raw,
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


class TestBuildRemoteCmd:
    """Tests for _build_remote_cmd -- remote command string construction."""

    def test_project_uses_binstub(self):
        """With project, should use binstub with --new --no-mux --acp --stdio."""
        target = SpawnTarget(
            type="ssh", host="server-a", user="deploy",
            project="my-project",
            copilot_args=["--allow-all"],
        )
        cmd = _build_remote_cmd(target)
        assert "my-project" in cmd
        assert "--new" in cmd
        assert "--no-mux" in cmd
        assert "--acp" in cmd
        assert "--stdio" in cmd
        assert "--allow-all" in cmd
        # The ACP passthrough separator must be *quoted* so PowerShell (the
        # default OpenSSH shell on native Windows targets) does not strip the
        # bare ``--`` end-of-parameters token (#985).
        assert "'--'" in cmd
        # Should NOT contain cd or export (binstub handles setup)
        assert "cd " not in cmd

    def test_no_project_uses_cd_exec(self):
        """Without project, should use cd + export + exec copilot."""
        target = SpawnTarget(
            type="ssh", cwd="/home/user/src", host="server-a",
        )
        cmd = _build_remote_cmd(target)
        assert "cd " in cmd
        assert "exec " in cmd
        assert "copilot" in cmd
        assert "--acp" in cmd
        assert "--stdio" in cmd

    def test_env_vars_exported(self):
        """Without project, should export env vars."""
        target = SpawnTarget(
            type="ssh", cwd=".", host="testhost", user="user",
            env={"FOO": "bar", "BAZ": "qux with spaces"},
        )
        cmd = _build_remote_cmd(target)
        assert "export FOO=" in cmd
        assert "export BAZ=" in cmd

    def test_extra_copilot_args(self):
        """Extra copilot args should be included in the command."""
        target = SpawnTarget(
            type="ssh", cwd=".", host="testhost",
            copilot_args=["--extensions-dir", "/opt/ext"],
        )
        cmd = _build_remote_cmd(target)
        assert "--extensions-dir" in cmd

    def test_no_project_requires_cwd(self):
        """Without project and without cwd should raise ValueError."""
        target = SpawnTarget(type="ssh", host="testhost")
        with pytest.raises(ValueError, match="requires 'cwd'"):
            _build_remote_cmd(target)

    def test_custom_copilot_path(self):
        """Custom copilot_path should be used in the command."""
        target = SpawnTarget(
            type="ssh", cwd=".", host="testhost",
            copilot_path="/usr/local/bin/copilot-beta",
        )
        cmd = _build_remote_cmd(target)
        assert "copilot-beta" in cmd

    def test_project_with_worktree_id_uses_resume(self):
        """SSH session roll: --worktree-id replaces --new."""
        target = SpawnTarget(
            type="ssh", host="server-a", user="deploy",
            project="my-project",
            worktree_id="lambda-core-wsl-20250101-120000-abc1",
        )
        cmd = _build_remote_cmd(target)
        assert "my-project" in cmd
        assert "--worktree-id" in cmd
        assert "lambda-core-wsl-20250101-120000-abc1" in cmd
        assert "--no-resume" in cmd
        assert "--new" not in cmd
        assert "--acp" in cmd
        assert "--stdio" in cmd

    def test_project_pwsh_skips_bash_breadcrumb(self):
        """PowerShell targets must not get the bash breadcrumb -- it
        ParserErrors in pwsh and aborts the whole launch command (#985)."""
        target = SpawnTarget(
            type="ssh", host="lambda-core", user="tmichon",
            project="aperture-labs", ssh_shell="pwsh",
            copilot_args=["--allow-all"],
        )
        cmd = _build_remote_cmd(target, session_id="s")
        assert "reached-device" not in cmd  # no breadcrumb prelude
        assert not cmd.startswith("(")      # no bash subshell
        assert cmd.startswith("aperture-labs")
        assert "'--'" in cmd and "--acp" in cmd

    def test_project_posix_keeps_breadcrumb(self):
        """POSIX targets still get the device-arrival breadcrumb."""
        target = SpawnTarget(
            type="ssh", host="h", project="p", ssh_shell="bash",
        )
        cmd = _build_remote_cmd(target, session_id="s")
        assert "reached-device" in cmd


class TestSpawnSsh:
    """Tests for spawn_ssh using ssh-manager's ConnectionManager."""

    @pytest.fixture
    def mock_manager(self):
        """Create a mock ConnectionManager."""
        mgr = MagicMock()
        mgr.ensure_connected = AsyncMock()
        mock_proc = MagicMock()
        mock_proc.pid = 99999
        mock_proc.returncode = None
        mgr.open_stdio_channel = AsyncMock(return_value=mock_proc)
        # Remote worktree resolve returns a launch plan with a worktree id.
        plan = {
            "launch": {
                "worktree_id": "server-a-20260101-000000-abcd",
                "work_dir": "/home/deploy/src.worktrees/server-a-20260101-000000-abcd",
            }
        }
        result = MagicMock()
        result.timed_out = False
        result.exit_code = 0
        result.stdout = json.dumps(plan)
        result.stderr = ""
        mgr.exec_command = AsyncMock(return_value=result)
        return mgr

    @pytest.mark.asyncio
    async def test_ssh_uses_connection_manager(self, mock_manager):
        """spawn_ssh should use ssh-manager's ConnectionManager."""
        target = SpawnTarget(
            type="ssh",
            cwd="/home/deploy/src",
            host="server-a",
            user="deploy",
        )

        with patch("agent_bridge.transport.get_default_manager", return_value=mock_manager):
            result = await spawn_ssh(target)

        # Verify ensure_connected was called with correct host and source
        mock_manager.ensure_connected.assert_called_once()
        call_args = mock_manager.ensure_connected.call_args
        assert call_args[0][0] == "server-a"  # host
        source = call_args[0][1]
        config = source.get_ssh_config()
        assert config.host_alias == "server-a"
        assert config.user == "deploy"

        # Verify open_stdio_channel was called
        mock_manager.open_stdio_channel.assert_called_once()
        channel_args = mock_manager.open_stdio_channel.call_args
        assert channel_args[0][0] == "server-a"  # host
        remote_cmd = channel_args[0][1]
        assert "cd " in remote_cmd
        assert "--acp" in remote_cmd
        assert "--stdio" in remote_cmd

        # Result should be an AgentProcess
        assert isinstance(result, AgentProcess)
        assert result.target == target

    @pytest.mark.asyncio
    async def test_ssh_without_user(self, mock_manager):
        """SSH target without user should pass None to SSHProfileSource."""
        target = SpawnTarget(type="ssh", cwd=".", host="myhost")

        with patch("agent_bridge.transport.get_default_manager", return_value=mock_manager):
            await spawn_ssh(target)

        source = mock_manager.ensure_connected.call_args[0][1]
        config = source.get_ssh_config()
        assert config.host_alias == "myhost"
        assert config.user is None

    @pytest.mark.asyncio
    async def test_ssh_with_project(self, mock_manager):
        """SSH with project should resolve the worktree then resume into it.

        The remote resolve binds worktree_id onto the target, so the launch
        takes _build_remote_cmd's resume branch (--worktree-id) rather than a
        second --new (which would create a duplicate worktree).
        """
        target = SpawnTarget(
            type="ssh", host="server-a", user="deploy",
            project="my-project",
            copilot_args=["--allow-all"],
        )

        with patch("agent_bridge.transport.get_default_manager", return_value=mock_manager):
            await spawn_ssh(target)

        # Resolve was run over the multiplexed connection.
        mock_manager.exec_command.assert_called_once()
        resolve_cmd = mock_manager.exec_command.call_args[0][1]
        assert "my-project" in resolve_cmd
        assert "resolve" in resolve_cmd
        assert "--json" in resolve_cmd
        assert "--new" in resolve_cmd

        # worktree_id + cwd were bound onto the target for DB persistence.
        assert target.worktree_id == "server-a-20260101-000000-abcd"
        assert target.cwd == (
            "/home/deploy/src.worktrees/server-a-20260101-000000-abcd"
        )

        # The launch resumes the resolved worktree (no second --new create).
        remote_cmd = mock_manager.open_stdio_channel.call_args[0][1]
        assert "my-project" in remote_cmd
        assert "--worktree-id" in remote_cmd
        assert "server-a-20260101-000000-abcd" in remote_cmd
        assert "--no-mux" in remote_cmd
        assert "--acp" in remote_cmd
        assert "--allow-all" in remote_cmd
        # Not the no-project cd-exec fallback (which ends in `&& exec copilot`).
        assert "&& exec " not in remote_cmd

    @pytest.mark.asyncio
    async def test_ssh_project_resolve_failure_falls_back_to_new(self, mock_manager):
        """If remote resolve fails, launch falls back to a direct --new (no crash)."""
        target = SpawnTarget(
            type="ssh", host="server-a", user="deploy", project="my-project",
        )
        failed = MagicMock()
        failed.timed_out = False
        failed.exit_code = 1
        failed.stdout = ""
        failed.stderr = "resolve blew up"
        mock_manager.exec_command = AsyncMock(return_value=failed)

        with patch("agent_bridge.transport.get_default_manager", return_value=mock_manager):
            await spawn_ssh(target)

        # No id bound; launch uses the legacy direct --new path.
        assert target.worktree_id is None
        remote_cmd = mock_manager.open_stdio_channel.call_args[0][1]
        assert "--new" in remote_cmd
        assert "--worktree-id" not in remote_cmd

    @pytest.mark.asyncio
    async def test_ssh_project_with_existing_worktree_id_skips_resolve(self, mock_manager):
        """A session roll (worktree_id already set) should not re-resolve --new."""
        target = SpawnTarget(
            type="ssh", host="server-a", user="deploy", project="my-project",
            worktree_id="server-a-existing-1234",
        )

        with patch("agent_bridge.transport.get_default_manager", return_value=mock_manager):
            await spawn_ssh(target)

        mock_manager.exec_command.assert_not_called()
        remote_cmd = mock_manager.open_stdio_channel.call_args[0][1]
        assert "--worktree-id" in remote_cmd
        assert "server-a-existing-1234" in remote_cmd

    @pytest.mark.asyncio
    async def test_ssh_requires_host(self):
        """SSH spawn without host should raise ValueError."""
        target = SpawnTarget(type="ssh", cwd=".")
        with pytest.raises(ValueError, match="host"):
            await spawn_ssh(target)

    @pytest.mark.asyncio
    async def test_ssh_connection_error_wrapped(self, mock_manager):
        """ConnectionError from ssh-manager should be wrapped in RuntimeError."""
        target = SpawnTarget(type="ssh", host="badhost", cwd=".")
        mock_manager.ensure_connected = AsyncMock(
            side_effect=ConnectionError("ControlMaster failed")
        )

        with patch("agent_bridge.transport.get_default_manager", return_value=mock_manager):
            with pytest.raises(RuntimeError, match="Failed to establish SSH"):
                await spawn_ssh(target)

    @pytest.mark.asyncio
    async def test_ssh_connection_reused(self, mock_manager):
        """Multiple spawns to the same host should call ensure_connected each time."""
        target = SpawnTarget(type="ssh", host="server-a", cwd="/tmp")

        with patch("agent_bridge.transport.get_default_manager", return_value=mock_manager):
            await spawn_ssh(target)
            await spawn_ssh(target)

        # ensure_connected is idempotent -- called twice but manager handles dedup
        assert mock_manager.ensure_connected.call_count == 2
        assert mock_manager.open_stdio_channel.call_count == 2


class TestExtractJsonObject:
    """Tests for _extract_json_object -- tolerant JSON parsing of remote stdout."""

    def test_clean_json(self):
        assert _extract_json_object('{"a": 1}') == {"a": 1}

    def test_json_with_banner_noise(self):
        noisy = "Welcome to Ubuntu\nLast login: today\n{\"worktree_id\": \"x\"}\n"
        assert _extract_json_object(noisy) == {"worktree_id": "x"}

    def test_empty_returns_none(self):
        assert _extract_json_object("") is None
        assert _extract_json_object("   ") is None

    def test_no_object_returns_none(self):
        assert _extract_json_object("no json here") is None

    def test_non_object_json_returns_none(self):
        # A bare array is valid JSON but not the object we want.
        assert _extract_json_object("[1, 2, 3]") is None


class TestResolveWorktreeRemote:
    """Tests for _resolve_worktree_remote -- the SSH worktree resolve round-trip."""

    def _ok_result(self, plan):
        r = MagicMock()
        r.timed_out = False
        r.exit_code = 0
        r.stdout = json.dumps(plan)
        r.stderr = ""
        return r

    @pytest.mark.asyncio
    async def test_resolve_uses_new_when_no_worktree_id(self):
        plan = {"launch": {"worktree_id": "wt-1", "work_dir": "/d"}}
        mgr = MagicMock()
        mgr.exec_command = AsyncMock(return_value=self._ok_result(plan))
        target = SpawnTarget(type="ssh", host="h", project="proj")

        out = await _resolve_worktree_remote(mgr, target)

        assert out == plan
        cmd = mgr.exec_command.call_args[0][1]
        assert "proj" in cmd and "resolve" in cmd and "--new" in cmd
        assert "--bridge" in cmd          # bridge-spawned new wt -> kind=bridge
        assert "--worktree-id" not in cmd

    @pytest.mark.asyncio
    async def test_remote_resolve_retries_without_bridge_on_old_remote(self):
        plan = {"launch": {"worktree_id": "wt-1", "work_dir": "/d"}}
        old = MagicMock()
        old.timed_out = False
        old.exit_code = 2
        old.stdout = ""
        old.stderr = "unrecognized arguments: --bridge"
        mgr = MagicMock()
        mgr.exec_command = AsyncMock(side_effect=[old, self._ok_result(plan)])
        target = SpawnTarget(type="ssh", host="h", project="proj")

        out = await _resolve_worktree_remote(mgr, target)

        assert out == plan
        assert mgr.exec_command.await_count == 2
        first = mgr.exec_command.call_args_list[0][0][1]
        second = mgr.exec_command.call_args_list[1][0][1]
        assert "--bridge" in first
        assert "--bridge" not in second   # retried without the unknown flag

    @pytest.mark.asyncio
    async def test_resolve_uses_worktree_id_when_set(self):
        plan = {"launch": {"worktree_id": "wt-9", "work_dir": "/d"}}
        mgr = MagicMock()
        mgr.exec_command = AsyncMock(return_value=self._ok_result(plan))
        target = SpawnTarget(type="ssh", host="h", project="proj", worktree_id="wt-9")

        await _resolve_worktree_remote(mgr, target)

        cmd = mgr.exec_command.call_args[0][1]
        assert "--worktree-id" in cmd and "wt-9" in cmd
        assert "--new" not in cmd

    @pytest.mark.asyncio
    async def test_resolve_raises_without_project(self):
        target = SpawnTarget(type="ssh", host="h")
        with pytest.raises(RuntimeError, match="requires target.project"):
            await _resolve_worktree_remote(MagicMock(), target)

    @pytest.mark.asyncio
    async def test_resolve_raises_on_nonzero_exit(self):
        r = MagicMock()
        r.timed_out = False
        r.exit_code = 2
        r.stdout = ""
        r.stderr = "boom"
        mgr = MagicMock()
        mgr.exec_command = AsyncMock(return_value=r)
        target = SpawnTarget(type="ssh", host="h", project="proj")
        with pytest.raises(RuntimeError, match="exit 2"):
            await _resolve_worktree_remote(mgr, target)

    @pytest.mark.asyncio
    async def test_resolve_raises_on_timeout(self):
        r = MagicMock()
        r.timed_out = True
        r.exit_code = -1
        r.stdout = ""
        r.stderr = ""
        mgr = MagicMock()
        mgr.exec_command = AsyncMock(return_value=r)
        target = SpawnTarget(type="ssh", host="h", project="proj")
        with pytest.raises(RuntimeError, match="timed out"):
            await _resolve_worktree_remote(mgr, target)

    @pytest.mark.asyncio
    async def test_resolve_raises_on_unparseable_stdout(self):
        r = MagicMock()
        r.timed_out = False
        r.exit_code = 0
        r.stdout = "not json at all"
        r.stderr = ""
        mgr = MagicMock()
        mgr.exec_command = AsyncMock(return_value=r)
        target = SpawnTarget(type="ssh", host="h", project="proj")
        with pytest.raises(RuntimeError, match="no JSON object"):
            await _resolve_worktree_remote(mgr, target)


class TestSpawnLocal:

    @pytest.mark.asyncio
    async def test_local_with_project_resolves_then_execs(self):
        """Local spawn with project should resolve via --json --new, then exec copilot."""
        target = SpawnTarget(
            type="local",
            project="my-project",
            copilot_args=["--allow-all"],
        )

        # Mock the resolve subprocess (returns JSON plan)
        resolve_proc = MagicMock()
        resolve_proc.returncode = 0
        resolve_plan = {
            "version": 1,
            "worktree": {"id": "test-wt-1234"},
            "launch": {
                "work_dir": "/tmp/worktree",
                "cmd": ["/usr/bin/copilot"],
                "env": {"MY_VAR": "val"},
                "worktree_id": "test-wt-1234",
            },
        }
        resolve_proc.communicate = AsyncMock(
            return_value=(json.dumps(resolve_plan).encode(), b"")
        )

        # Mock the copilot subprocess
        copilot_proc = MagicMock()
        copilot_proc.pid = 12345
        copilot_proc.returncode = None

        with patch("agent_bridge.transport.asyncio") as mock_asyncio, \
             patch("agent_bridge.transport.os.path.exists", return_value=True):
            mock_asyncio.create_subprocess_exec = AsyncMock(
                side_effect=[resolve_proc, copilot_proc]
            )
            mock_asyncio.subprocess = asyncio.subprocess

            result = await spawn_local(target)

            # First call: resolve (calls python directly, not binstub)
            resolve_call = mock_asyncio.create_subprocess_exec.call_args_list[0]
            resolve_args = resolve_call[0]
            assert resolve_args[0].endswith("python.exe") or resolve_args[0].endswith("python")
            assert "-m" in resolve_args
            assert "agent_worktrees" in resolve_args
            assert "resolve" in resolve_args
            assert "--json" in resolve_args
            assert "--new" in resolve_args
            assert "--no-resume" in resolve_args

            # Second call: copilot exec
            exec_call = mock_asyncio.create_subprocess_exec.call_args_list[1]
            exec_args = exec_call[0]
            assert exec_args[0] == "/usr/bin/copilot"
            assert "--acp" in exec_args
            assert "--stdio" in exec_args
            assert "--allow-all" in exec_args
            assert exec_call[1]["cwd"] == "/tmp/worktree"
            # The ACP stdout reader must use a large frame limit, not asyncio's
            # 64 KiB default, so large tool results don't drop the connection.
            assert exec_call[1]["limit"] == _ACP_STDIO_LIMIT_BYTES

            assert result.proc == copilot_proc

            # Resolved values should be stored back into target
            assert target.worktree_id == "test-wt-1234"
            assert target.cwd == "/tmp/worktree"

    @pytest.mark.asyncio
    async def test_local_with_project_resume_worktree(self):
        """Local spawn with worktree_id should resolve with --worktree-id."""
        target = SpawnTarget(
            type="local",
            project="my-project",
            worktree_id="existing-wt-5678",
        )

        resolve_proc = MagicMock()
        resolve_proc.returncode = 0
        resolve_plan = {
            "version": 1,
            "launch": {
                "work_dir": "/tmp/existing",
                "cmd": ["/usr/bin/copilot", "--resume", "sess-abc"],
                "env": {},
                "worktree_id": "existing-wt-5678",
            },
        }
        resolve_proc.communicate = AsyncMock(
            return_value=(json.dumps(resolve_plan).encode(), b"")
        )

        copilot_proc = MagicMock()
        copilot_proc.pid = 12345
        copilot_proc.returncode = None

        with patch("agent_bridge.transport.asyncio") as mock_asyncio, \
             patch("agent_bridge.transport.os.path.exists", return_value=True):
            mock_asyncio.create_subprocess_exec = AsyncMock(
                side_effect=[resolve_proc, copilot_proc]
            )
            mock_asyncio.subprocess = asyncio.subprocess

            await spawn_local(target)

            resolve_args = mock_asyncio.create_subprocess_exec.call_args_list[0][0]
            assert "--worktree-id" in resolve_args
            assert "existing-wt-5678" in resolve_args
            assert "--new" not in resolve_args
            assert "--no-resume" in resolve_args

    @pytest.mark.asyncio
    async def test_local_resolve_failure_raises(self):
        """Resolve failure should raise RuntimeError."""
        target = SpawnTarget(type="local", project="my-project")

        resolve_proc = MagicMock()
        resolve_proc.returncode = 1
        resolve_proc.communicate = AsyncMock(
            return_value=(b"", b"resolve error details")
        )

        with patch("agent_bridge.transport.asyncio") as mock_asyncio, \
             patch("agent_bridge.transport.os.path.exists", return_value=True):
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=resolve_proc)
            mock_asyncio.subprocess = asyncio.subprocess

            with pytest.raises(RuntimeError, match="Worktree resolve failed"):
                await spawn_local(target)

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
            mock_local.assert_called_once()
            assert mock_local.call_args[0][0] == target
            assert result == mock_proc

    @pytest.mark.asyncio
    async def test_dispatch_ssh(self):
        """spawn() dispatches to spawn_ssh for SSH targets."""
        target = SpawnTarget(type="ssh", cwd=".", host="testhost")

        with patch("agent_bridge.transport.spawn_ssh", new_callable=AsyncMock) as mock_ssh:
            mock_proc = MagicMock()
            mock_ssh.return_value = mock_proc

            result = await spawn(target)
            mock_ssh.assert_called_once()
            assert mock_ssh.call_args[0][0] == target
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

        mock_manager = MagicMock()
        mock_manager.ensure_connected = AsyncMock()
        mock_manager.open_stdio_channel = AsyncMock()

        with patch("agent_bridge.transport.get_default_manager", return_value=mock_manager):
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
        args = ["my-control-harness", "--no-mux", "--acp", "--stdio"]
        env = {"PATH": "C:\\Users\\test\\.local\\bin"}

        with patch("agent_bridge.transport.sys") as mock_sys, \
             patch("agent_bridge.transport.shutil") as mock_shutil:
            mock_sys.platform = "win32"
            mock_shutil.which.return_value = "C:\\Users\\test\\.local\\bin\\my-control-harness.cmd"

            result = _wrap_batch_for_windows(args, env)

        assert result[0].endswith("cmd.exe")
        assert result[4] == "C:\\Users\\test\\.local\\bin\\my-control-harness.cmd"
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


class TestSpawnTargetCommandSerialization:
    """Tests for SpawnTarget with spawn_command field."""

    def test_roundtrip_command(self):
        target = SpawnTarget(
            type="command",
            spawn_command=["agent-codespaces", "ssh", "--stdio", "my-cs"],
        )
        restored = SpawnTarget.from_json(target.to_json())
        assert restored.type == "command"
        assert restored.spawn_command == [
            "agent-codespaces", "ssh", "--stdio", "my-cs",
        ]

    def test_spawn_command_none_by_default(self):
        target = SpawnTarget(type="local", cwd="/tmp")
        assert target.spawn_command is None
        data = json.loads(target.to_json())
        assert data["spawn_command"] is None

    def test_roundtrip_preserves_env(self):
        target = SpawnTarget(
            type="command",
            spawn_command=["echo", "hello"],
            env={"KEY": "value"},
        )
        restored = SpawnTarget.from_json(target.to_json())
        assert restored.env == {"KEY": "value"}


class TestSpawnRaw:
    """Tests for spawn_raw -- raw command spawning."""

    @pytest.mark.asyncio
    async def test_spawn_raw_runs_command(self):
        target = SpawnTarget(
            type="command",
            spawn_command=["echo", "hello"],
        )
        with patch("agent_bridge.transport.asyncio") as mock_asyncio, \
             patch("agent_bridge.transport._wrap_batch_for_windows") as mock_wrap, \
             patch("agent_bridge.transport._creation_flags", return_value=0):
            mock_proc = MagicMock()
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=mock_proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_wrap.return_value = ["echo", "hello"]

            result = await spawn_raw(target)

            assert result.proc is mock_proc
            mock_asyncio.create_subprocess_exec.assert_called_once()
            call_args = mock_asyncio.create_subprocess_exec.call_args
            assert call_args[0] == ("echo", "hello")
            # ACP stdout reader must use the large frame limit (see spawn_local).
            assert call_args[1]["limit"] == _ACP_STDIO_LIMIT_BYTES

    @pytest.mark.asyncio
    async def test_spawn_raw_requires_spawn_command(self):
        target = SpawnTarget(type="command")
        with pytest.raises(ValueError, match="spawn_command"):
            await spawn_raw(target)


class TestSpawnDispatchCommand:
    """Tests for spawn() dispatching to spawn_raw for command targets."""

    @pytest.mark.asyncio
    async def test_spawn_dispatches_command_type(self):
        target = SpawnTarget(
            type="command",
            spawn_command=["agent-codespaces", "ssh", "--stdio", "my-cs"],
        )
        with patch("agent_bridge.transport.spawn_raw", new_callable=AsyncMock) as mock_raw:
            mock_raw.return_value = MagicMock(spec=AgentProcess)
            await spawn(target)
            mock_raw.assert_called_once()
            assert mock_raw.call_args[0][0] == target

    @pytest.mark.asyncio
    async def test_spawn_dispatches_spawn_command_field(self):
        """spawn_command field triggers spawn_raw even without type=command."""
        target = SpawnTarget(
            type="local",
            spawn_command=["echo", "hello"],
        )
        with patch("agent_bridge.transport.spawn_raw", new_callable=AsyncMock) as mock_raw:
            mock_raw.return_value = MagicMock(spec=AgentProcess)
            await spawn(target)
            mock_raw.assert_called_once()
            assert mock_raw.call_args[0][0] == target


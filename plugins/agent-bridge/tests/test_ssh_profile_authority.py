"""Profile-backed Bridge connections leave connection identity to OpenSSH."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from agent_procutil import no_window_flags
from ssh_manager import SSHProfileSource

from agent_bridge import carrier, remote_operations, transport, worktree_owner_operation
from agent_bridge.routes import worktrees
from agent_bridge.topology import SshEnvironment

pytestmark = pytest.mark.skipif(shutil.which("ssh") is None, reason="OpenSSH required")


@pytest.fixture
def ssh_profile(tmp_path):
    path = tmp_path / "ssh_config"
    path.write_text(
        "Host profile-host\n"
        "    HostName configured.example.invalid\n"
        "    User profile-user\n"
        "    Port 40222\n"
        "    IdentityFile profile-identity\n"
        "    IdentityFile profile-identity-2\n"
        "    HostKeyAlias profile-host-key\n"
        "    ProxyCommand profile-proxy %h %p\n",
        encoding="utf-8",
    )
    return path


def _profile_source(monkeypatch, module, path):
    def source(*args, **kwargs):
        return SSHProfileSource(*args, config_file=str(path), **kwargs)

    monkeypatch.setattr(module, "SSHProfileSource", source)


def _assert_profile(items):
    values = {}
    for key, value in items:
        values.setdefault(key, []).append(value)
    assert values["hostname"] == ["configured.example.invalid"]
    assert values["user"] == ["profile-user"]
    assert values["port"] == ["40222"]
    identity_names = {Path(value).name for value in values["identityfile"]}
    assert {"profile-identity", "profile-identity-2"}.issubset(identity_names)
    assert values["hostkeyalias"] == ["profile-host-key"]
    assert any(value.startswith("profile-proxy ") for value in values["proxycommand"])


@pytest.mark.asyncio
@pytest.mark.parametrize("topology_port", [22, 30222])
async def test_remote_carrier_preserves_profile_identity(
    monkeypatch, ssh_profile, topology_port,
):
    before = ssh_profile.read_bytes()
    environment = SshEnvironment(
        name="windows", alias="profile-host", port=topology_port,
        user="topology-user", shell="pwsh",
    )
    resolver = MagicMock()
    resolver.resolve_ssh_environment.return_value = (
        SimpleNamespace(ssh_environments=[environment]), environment,
    )
    acquired = []

    async def acquire(_key, _platform, *, config_source, manager):
        acquired.append(config_source.get_ssh_config())
        return object()

    _profile_source(monkeypatch, remote_operations, ssh_profile)
    monkeypatch.setattr(carrier, "acquire_remote_carrier", acquire)
    await remote_operations.RemoteOperationService(resolver)._lease("profile-host")

    assert len(acquired) == 1
    _assert_profile(acquired[0].effective_config)
    assert acquired[0].port is None
    assert acquired[0].user is None
    assert acquired[0].identity_file is None
    assert ssh_profile.read_bytes() == before


@pytest.mark.asyncio
async def test_acp_connection_preserves_profile_user(monkeypatch, ssh_profile):
    before = ssh_profile.read_bytes()
    manager = MagicMock()
    manager.ensure_connected = AsyncMock()
    manager.open_stdio_channel = AsyncMock()
    _profile_source(monkeypatch, transport, ssh_profile)
    monkeypatch.setattr(transport, "get_default_manager", lambda: manager)

    await transport.spawn_ssh(
        transport.SpawnTarget(
            type="ssh", cwd=".", host="profile-host", user="agent-user",
        )
    )

    source = manager.ensure_connected.await_args.args[1]
    config = source.get_ssh_config()
    _assert_profile(config.effective_config)
    assert config.user is None
    assert config.identity_file is None
    assert ssh_profile.read_bytes() == before


@pytest.mark.asyncio
async def test_discovery_and_session_reads_preserve_profile_user(
    monkeypatch, ssh_profile,
):
    before = ssh_profile.read_bytes()
    execute = AsyncMock(return_value=("{}", ""))
    monkeypatch.setattr(worktrees, "_exec_ex", execute)
    monkeypatch.setattr(worktrees, "_is_local_target", lambda *_args: False)
    resolver = MagicMock()
    resolver.resolve.return_value = transport.SpawnTarget(
        type="ssh", host="profile-host", user="topology-user",
    )

    await worktrees._run_for_agent(
        "remote",
        SimpleNamespace(project="example", host="remote", ssh_user="agent-user"),
        resolver,
        ["list"],
    )

    command = execute.await_args.args[0]
    result = subprocess.run(
        ["ssh", "-G", "-F", str(ssh_profile), *command[1:-1]],
        capture_output=True, text=True, check=True, timeout=10,
        creationflags=no_window_flags(),
    )
    _assert_profile(line.split(" ", 1) for line in result.stdout.splitlines() if " " in line)
    assert command[-2] == "profile-host"
    assert ssh_profile.read_bytes() == before


@pytest.mark.asyncio
async def test_native_owner_launch_preserves_profile_user(monkeypatch, ssh_profile):
    before = ssh_profile.read_bytes()
    process = SimpleNamespace(
        communicate=AsyncMock(return_value=(b'{"ok":true}', b"")), returncode=0,
    )
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(worktree_owner_operation.asyncio, "create_subprocess_exec", spawn)
    resolver = SimpleNamespace(resolve=lambda _: transport.SpawnTarget(
        type="ssh", host="profile-host", user="legacy-user", ssh_shell="pwsh",
    ))
    await worktree_owner_operation.run_owner_operation(
        "remote", SimpleNamespace(project="example"), resolver,
        ["embody", "--worktree-id", "example-worktree", "--json"],
    )
    command = spawn.await_args.args
    result = subprocess.run(
        ["ssh", "-G", "-F", str(ssh_profile), *command[1:-1]],
        capture_output=True, text=True, check=True, timeout=10,
        creationflags=no_window_flags(),
    )
    _assert_profile(line.split(" ", 1) for line in result.stdout.splitlines() if " " in line)
    assert command[-2] == "profile-host"
    assert "-EncodedCommand" in command[-1]
    assert ssh_profile.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["", None])
async def test_missing_profile_alias_refuses_before_execution(monkeypatch, host):
    execute = AsyncMock()
    monkeypatch.setattr(worktrees, "_exec_ex", execute)

    with pytest.raises(ValueError, match="SSH alias"):
        await worktrees._run_ssh_ex(host=host, user="agent-user", project="example")

    execute.assert_not_awaited()

"""Env threading through ConnectionManager -- the actual SSH subprocess spawn
must reuse a multi-account ConfigSource's pinned ``gh`` environment, not just
its own internal config-fetch call.

Root cause (agent-bridge-cli-mode-sessions Phase 4 live validation, a real
CodeSpace 404): ``CodespaceSource``/``CodespaceConfigSource`` already pinned
``gh codespace ssh --config`` to the CodeSpace's owning account via an
internal ``gh_env``, but nothing threaded that env onward to the connection
itself -- every subsequent SSH subprocess (the ControlMaster, exec_command,
open_stdio_channel, the graceful ``-O exit`` disconnect, and -- on Windows --
the embedded ProxyCommand's ``gh cs ssh --stdio`` child spawned by the proxy
broker) silently fell back to the ambient process environment's active ``gh``
account. When that ambient account differs from the CodeSpace's owner, `gh`
returns a 404 ("getting full codespace details") that looks like a
CodeSpace-availability problem but is actually an identity mismatch.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from ssh_manager.config_sources import SSHConfig
from ssh_manager.manager import ConnectionManager
from ssh_manager.platform import MultiplexMode, PlatformInfo


def _direct_platform(tmp_path: Path) -> PlatformInfo:
    """A non-multiplexed platform so ``_connect`` skips the ControlMaster
    spawn entirely -- exercises only the env-storage half here; the
    ControlMaster spawn itself is covered by
    ``TestControlMasterEnvThreading`` below."""
    return PlatformInfo(
        mode=MultiplexMode.DIRECT, socket_dir=tmp_path, max_socket_path=260,
    )


class _FakeConfigSource:
    """A minimal multi-account ``ConfigSource`` exposing ``gh_env``."""

    def __init__(self, config: SSHConfig, gh_env: dict[str, str] | None) -> None:
        self._config = config
        self.gh_env = gh_env

    def get_ssh_config(self) -> SSHConfig:
        return self._config

    def refresh(self) -> SSHConfig:
        return self._config


def _fake_config(host: str = "cs-target") -> SSHConfig:
    return SSHConfig(host_alias=host, user="vscode")


class _FakeProcess:
    returncode: int | None = 0

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        return b"", b""

    async def wait(self) -> int:
        return 0


class TestExecCommandEnvThreading:
    @pytest.mark.asyncio
    async def test_pinned_env_reaches_the_actual_ssh_spawn(
        self, tmp_path, monkeypatch,
    ):
        pinned_env = {"GH_TOKEN": "token-for-the-owning-account", "PATH": "/usr/bin"}
        source = _FakeConfigSource(_fake_config(), gh_env=pinned_env)
        manager = ConnectionManager(platform=_direct_platform(tmp_path))

        seen: dict[str, Any] = {}

        async def fake_create_ssh_subprocess(*args: str, **kwargs: Any) -> _FakeProcess:
            seen["env"] = kwargs.get("env")
            return _FakeProcess()

        monkeypatch.setattr(
            "ssh_manager.manager.create_ssh_subprocess", fake_create_ssh_subprocess,
        )

        await manager.ensure_connected("cs-target", source, [])
        await manager.exec_command("cs-target", "echo hi")

        assert seen["env"] == pinned_env

    @pytest.mark.asyncio
    async def test_no_gh_env_preserves_ambient_inherit(self, tmp_path, monkeypatch):
        """A ``ConfigSource`` without ``gh_env`` (e.g. ``SSHProfileSource``)
        must not force an explicit env -- ``None`` preserves the historical
        ambient-inherit ``subprocess`` default."""
        source = _FakeConfigSource(_fake_config(), gh_env=None)
        manager = ConnectionManager(platform=_direct_platform(tmp_path))

        seen: dict[str, Any] = {}

        async def fake_create_ssh_subprocess(*args: str, **kwargs: Any) -> _FakeProcess:
            seen["env"] = kwargs.get("env")
            return _FakeProcess()

        monkeypatch.setattr(
            "ssh_manager.manager.create_ssh_subprocess", fake_create_ssh_subprocess,
        )

        await manager.ensure_connected("cs-target", source, [])
        await manager.exec_command("cs-target", "echo hi")

        assert seen["env"] is None

    @pytest.mark.asyncio
    async def test_duck_typed_source_without_gh_env_attribute_is_unaffected(
        self, tmp_path, monkeypatch,
    ):
        """A plain object with no ``gh_env`` attribute at all (not just one
        set to ``None``) must be handled the same way -- ``getattr`` with a
        default, never a hard attribute-error."""

        class _PlainSource:
            def get_ssh_config(self) -> SSHConfig:
                return _fake_config()

            def refresh(self) -> SSHConfig:
                return _fake_config()

        manager = ConnectionManager(platform=_direct_platform(tmp_path))
        seen: dict[str, Any] = {}

        async def fake_create_ssh_subprocess(*args: str, **kwargs: Any) -> _FakeProcess:
            seen["env"] = kwargs.get("env")
            return _FakeProcess()

        monkeypatch.setattr(
            "ssh_manager.manager.create_ssh_subprocess", fake_create_ssh_subprocess,
        )

        await manager.ensure_connected("cs-target", _PlainSource(), [])
        await manager.exec_command("cs-target", "echo hi")

        assert seen["env"] is None


class TestControlMasterEnvThreading:
    @pytest.mark.asyncio
    async def test_control_master_spawn_receives_pinned_env(
        self, tmp_path, monkeypatch,
    ):
        """The ControlMaster establishment itself (not just exec_command)
        must carry the pinned env -- this is what actually invokes the
        ProxyCommand (``gh cs ssh --stdio``) for the first time."""
        pinned_env = {"GH_TOKEN": "token-for-the-owning-account"}
        source = _FakeConfigSource(_fake_config(), gh_env=pinned_env)
        platform = PlatformInfo(
            mode=MultiplexMode.CONTROL_MASTER,
            socket_dir=tmp_path,
            max_socket_path=200,
        )
        manager = ConnectionManager(platform=platform)

        seen: dict[str, Any] = {}

        async def fake_create_ssh_subprocess(*args: str, **kwargs: Any) -> _FakeProcess:
            seen["env"] = kwargs.get("env")
            return _FakeProcess()

        async def fake_wait_for_socket(self, socket) -> None:
            return None

        monkeypatch.setattr(
            "ssh_manager.manager.create_ssh_subprocess", fake_create_ssh_subprocess,
        )
        monkeypatch.setattr(ConnectionManager, "_wait_for_socket", fake_wait_for_socket)

        await manager.ensure_connected("cs-target", source, [])

        assert seen["env"] == pinned_env


class TestOpenStdioChannelEnvThreading:
    @pytest.mark.asyncio
    async def test_stdio_channel_receives_pinned_env(self, tmp_path, monkeypatch):
        pinned_env = {"GH_TOKEN": "token-for-the-owning-account"}
        source = _FakeConfigSource(_fake_config(), gh_env=pinned_env)
        manager = ConnectionManager(platform=_direct_platform(tmp_path))

        seen: dict[str, Any] = {}

        async def fake_create_ssh_subprocess(*args: str, **kwargs: Any) -> _FakeProcess:
            seen["env"] = kwargs.get("env")
            return _FakeProcess()

        monkeypatch.setattr(
            "ssh_manager.manager.create_ssh_subprocess", fake_create_ssh_subprocess,
        )

        await manager.ensure_connected("cs-target", source, [])
        await manager.open_stdio_channel("cs-target", "copilot --acp --stdio")

        assert seen["env"] == pinned_env

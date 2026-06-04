"""Tests for health monitoring."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from ssh_manager.config_sources import SSHProfileSource
from ssh_manager.health import HealthStatus, check_health, ensure_healthy
from ssh_manager.manager import ConnectionInfo, ConnectionManager
from ssh_manager.platform import MultiplexMode, PlatformInfo


@pytest.fixture
def unix_platform(tmp_path):
    return PlatformInfo(
        mode=MultiplexMode.CONTROL_MASTER,
        socket_dir=tmp_path / "sockets",
        max_socket_path=108,
    )


@pytest.fixture
def win_platform(tmp_path):
    return PlatformInfo(
        mode=MultiplexMode.DIRECT,
        socket_dir=tmp_path / "sockets",
        max_socket_path=260,
    )


@pytest.fixture
def source():
    return SSHProfileSource(host_alias="test-host")


class TestHealthStatus:
    """HealthStatus dataclass tests."""

    def test_ok_status(self):
        s = HealthStatus(ok=True, reason="ok")
        assert s.ok
        assert not s.needs_reconnect

    def test_stale_socket_needs_reconnect(self):
        s = HealthStatus(ok=False, reason="stale_socket")
        assert s.needs_reconnect

    def test_process_dead_needs_reconnect(self):
        s = HealthStatus(ok=False, reason="process_dead")
        assert s.needs_reconnect

    def test_not_connected_no_reconnect(self):
        s = HealthStatus(ok=False, reason="not_connected")
        assert not s.needs_reconnect


class TestCheckHealth:
    """check_health function tests."""

    @pytest.mark.asyncio
    async def test_not_connected(self, win_platform):
        manager = ConnectionManager(platform=win_platform)
        status = await check_health(manager, "no-host")
        assert not status.ok
        assert status.reason == "not_connected"

    @pytest.mark.asyncio
    async def test_direct_mode_always_ok(self, win_platform, source):
        manager = ConnectionManager(platform=win_platform)
        await manager.ensure_connected("test-host", source)
        status = await check_health(manager, "test-host")
        assert status.ok
        assert status.reason == "not_multiplexed"


class TestEnsureHealthy:
    """ensure_healthy reconnection tests."""

    @pytest.mark.asyncio
    async def test_already_healthy_no_reconnect(self, win_platform, source):
        manager = ConnectionManager(platform=win_platform)
        await manager.ensure_connected("test-host", source)
        status = await ensure_healthy(manager, "test-host", source)
        assert status.ok

    @pytest.mark.asyncio
    async def test_not_connected_no_reconnect(self, win_platform, source):
        manager = ConnectionManager(platform=win_platform)
        status = await ensure_healthy(manager, "no-host", source)
        assert not status.ok
        assert status.reason == "not_connected"

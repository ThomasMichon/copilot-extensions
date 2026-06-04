"""Tests for platform detection and socket path handling."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

from ssh_manager.platform import (
    MultiplexMode,
    PlatformInfo,
    detect_platform,
    ensure_socket_dir,
    socket_path_for_host,
)


class TestDetectPlatform:
    """Platform detection tests."""

    @patch("ssh_manager.platform.sys")
    @patch("ssh_manager.platform._is_wsl", return_value=False)
    def test_linux_uses_control_master(self, mock_wsl, mock_sys):
        mock_sys.platform = "linux"
        info = detect_platform()
        assert info.mode == MultiplexMode.CONTROL_MASTER
        assert info.supports_control_master is True
        assert info.max_socket_path == 108

    @patch("ssh_manager.platform.sys")
    @patch("ssh_manager.platform._is_wsl", return_value=False)
    def test_windows_uses_direct(self, mock_wsl, mock_sys):
        mock_sys.platform = "win32"
        info = detect_platform()
        assert info.mode == MultiplexMode.DIRECT
        assert info.supports_control_master is False

    @patch("ssh_manager.platform.sys")
    @patch("ssh_manager.platform._is_wsl", return_value=True)
    def test_wsl_uses_control_master(self, mock_wsl, mock_sys):
        mock_sys.platform = "win32"
        info = detect_platform()
        assert info.mode == MultiplexMode.CONTROL_MASTER


class TestSocketPath:
    """Socket path generation tests."""

    def test_includes_host_in_path(self):
        platform = PlatformInfo(
            mode=MultiplexMode.CONTROL_MASTER,
            socket_dir=Path("/tmp/sockets"),
            max_socket_path=108,
        )
        path = socket_path_for_host(platform, "borealis")
        assert "borealis" in path.name

    def test_different_users_get_different_paths(self):
        platform = PlatformInfo(
            mode=MultiplexMode.CONTROL_MASTER,
            socket_dir=Path("/tmp/sockets"),
            max_socket_path=108,
        )
        path1 = socket_path_for_host(platform, "server", user="alice")
        path2 = socket_path_for_host(platform, "server", user="bob")
        assert path1 != path2

    def test_different_ports_get_different_paths(self):
        platform = PlatformInfo(
            mode=MultiplexMode.CONTROL_MASTER,
            socket_dir=Path("/tmp/sockets"),
            max_socket_path=108,
        )
        path1 = socket_path_for_host(platform, "server", port=22)
        path2 = socket_path_for_host(platform, "server", port=2222)
        assert path1 != path2

    def test_same_identity_gets_same_path(self):
        platform = PlatformInfo(
            mode=MultiplexMode.CONTROL_MASTER,
            socket_dir=Path("/tmp/sockets"),
            max_socket_path=108,
        )
        path1 = socket_path_for_host(platform, "server", user="alice", port=22)
        path2 = socket_path_for_host(platform, "server", user="alice", port=22)
        assert path1 == path2

    def test_long_hostname_truncated(self):
        platform = PlatformInfo(
            mode=MultiplexMode.CONTROL_MASTER,
            socket_dir=Path("/tmp/sockets"),
            max_socket_path=108,
        )
        long_host = "a" * 100
        path = socket_path_for_host(platform, long_host)
        # host[:20] + "-" + hash[:12] = 33 chars max for the name
        assert len(path.name) <= 33


class TestEnsureSocketDir:
    """Socket directory creation tests."""

    def test_creates_directory(self, tmp_path):
        socket_dir = tmp_path / "sockets"
        platform = PlatformInfo(
            mode=MultiplexMode.CONTROL_MASTER,
            socket_dir=socket_dir,
            max_socket_path=108,
        )
        ensure_socket_dir(platform)
        assert socket_dir.exists()
        assert socket_dir.is_dir()

    def test_idempotent(self, tmp_path):
        socket_dir = tmp_path / "sockets"
        platform = PlatformInfo(
            mode=MultiplexMode.CONTROL_MASTER,
            socket_dir=socket_dir,
            max_socket_path=108,
        )
        ensure_socket_dir(platform)
        ensure_socket_dir(platform)  # should not raise
        assert socket_dir.exists()

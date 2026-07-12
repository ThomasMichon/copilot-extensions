"""Tests for the listen-port discriminator (`default_port` / `_is_wsl`).

The discriminator is "am I a WSL guest?" (a guest sharing the Windows host's TCP
port namespace), **not** "am I non-Windows?" -- so bare-metal Linux is 9280, and
only a WSL guest is 9281.
"""

from __future__ import annotations

import builtins
from io import StringIO

import agent_bridge.models as models


def _patch(monkeypatch, *, platform, wsl_env=None, osrelease=""):
    monkeypatch.setattr(models.sys, "platform", platform)
    if wsl_env is None:
        monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    else:
        monkeypatch.setenv("WSL_DISTRO_NAME", wsl_env)

    real_open = builtins.open

    def fake_open(path, *args, **kwargs):
        if str(path) == "/proc/sys/kernel/osrelease":
            return StringIO(osrelease)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fake_open)


def test_windows_is_9280(monkeypatch):
    _patch(monkeypatch, platform="win32")
    assert models._is_wsl() is False
    assert models.default_port() == 9280


def test_bare_metal_linux_is_9280(monkeypatch):
    # A real Linux host (e.g. a Debian appliance) is an ordinary host on 9280.
    _patch(monkeypatch, platform="linux", osrelease="5.15.0-25-generic\n")
    assert models._is_wsl() is False
    assert models.default_port() == 9280


def test_wsl_guest_via_env_is_9281(monkeypatch):
    _patch(monkeypatch, platform="linux", wsl_env="Ubuntu",
           osrelease="5.15.0-25-generic\n")
    assert models._is_wsl() is True
    assert models.default_port() == 9281


def test_wsl_guest_via_osrelease_is_9281(monkeypatch):
    # No env var (e.g. a systemd user service), but the kernel names WSL.
    _patch(monkeypatch, platform="linux",
           osrelease="5.15.153.1-microsoft-standard-WSL2\n")
    assert models._is_wsl() is True
    assert models.default_port() == 9281


def test_osrelease_unreadable_defaults_to_host(monkeypatch):
    _patch(monkeypatch, platform="linux")

    def boom(path, *args, **kwargs):
        if str(path) == "/proc/sys/kernel/osrelease":
            raise OSError("no /proc here")
        return builtins.open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", boom)
    assert models._is_wsl() is False
    assert models.default_port() == 9280

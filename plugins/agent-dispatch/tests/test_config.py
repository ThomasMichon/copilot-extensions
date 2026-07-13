"""Tests for coordinator configuration resolution."""

from __future__ import annotations

import agent_dispatch.config as config
from agent_dispatch.config import (
    DEFAULT_PORT,
    DEFAULT_SWEEP_INTERVAL,
    WSL_PORT,
    default_port,
    load_config,
)


def test_sweep_interval_default(monkeypatch):
    monkeypatch.delenv("AGENT_DISPATCH_SWEEP_INTERVAL", raising=False)
    assert load_config().sweep_interval == DEFAULT_SWEEP_INTERVAL


def test_sweep_interval_from_env(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_SWEEP_INTERVAL", "5")
    assert load_config().sweep_interval == 5.0


def test_sweep_interval_zero_disables(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_SWEEP_INTERVAL", "0")
    assert load_config().sweep_interval == 0.0


def test_default_port_host(monkeypatch):
    monkeypatch.setattr(config, "_is_wsl_guest", lambda: False)
    assert default_port() == DEFAULT_PORT == 9330


def test_default_port_wsl_guest(monkeypatch):
    # A WSL guest shares the Windows host loopback, so it uses preferred+1.
    monkeypatch.setattr(config, "_is_wsl_guest", lambda: True)
    assert default_port() == WSL_PORT == 9331


def test_wsl_guest_detected_via_env(monkeypatch):
    # WSL_DISTRO_NAME short-circuits detection (deterministic on any platform).
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    assert config._is_wsl_guest() is True


def test_port_env_overrides_default(monkeypatch):
    monkeypatch.delenv("WSL_DISTRO_NAME", raising=False)
    monkeypatch.setenv("AGENT_DISPATCH_PORT", "9999")
    assert load_config().port == 9999


def test_port_default_follows_wsl_guest(monkeypatch):
    monkeypatch.delenv("AGENT_DISPATCH_PORT", raising=False)
    monkeypatch.setattr(config, "_is_wsl_guest", lambda: True)
    assert load_config().port == 9331

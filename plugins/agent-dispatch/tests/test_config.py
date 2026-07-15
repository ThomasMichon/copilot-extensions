"""Tests for coordinator configuration resolution."""

from __future__ import annotations

from agent_dispatch import config as config_mod
from agent_dispatch.config import DEFAULT_SWEEP_INTERVAL, client_url, load_config


def test_sweep_interval_default(monkeypatch):
    monkeypatch.delenv("AGENT_DISPATCH_SWEEP_INTERVAL", raising=False)
    assert load_config().sweep_interval == DEFAULT_SWEEP_INTERVAL


def test_sweep_interval_from_env(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_SWEEP_INTERVAL", "5")
    assert load_config().sweep_interval == 5.0


def test_sweep_interval_zero_disables(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_SWEEP_INTERVAL", "0")
    assert load_config().sweep_interval == 0.0


# -- client_url resolution (coordinator inversion) --------------------------


def test_client_url_env_override_wins(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_URL", "http://coord.example:9847")
    # Even on a (mocked) WSL guest, the explicit override short-circuits.
    monkeypatch.setattr("agent_dispatch.netinfo.is_wsl", lambda: True)
    assert client_url() == "http://coord.example:9847"


def test_client_url_wsl_guest_resolves_windows(monkeypatch):
    monkeypatch.delenv("AGENT_DISPATCH_URL", raising=False)
    monkeypatch.setattr("agent_dispatch.netinfo.is_wsl", lambda: True)
    monkeypatch.setattr(
        "agent_dispatch.netinfo.resolve_wsl_client_url",
        lambda port: f"http://172.19.240.1:{port}",
    )
    assert client_url() == "http://172.19.240.1:9847"


def test_client_url_standalone_uses_local_default(monkeypatch):
    monkeypatch.delenv("AGENT_DISPATCH_URL", raising=False)
    monkeypatch.setattr("agent_dispatch.netinfo.is_wsl", lambda: False)
    assert client_url() == load_config().url


def test_client_url_degrades_on_resolution_error(monkeypatch):
    monkeypatch.delenv("AGENT_DISPATCH_URL", raising=False)
    monkeypatch.setattr("agent_dispatch.netinfo.is_wsl", lambda: True)

    def _boom(_port):
        raise RuntimeError("probe blew up")

    monkeypatch.setattr("agent_dispatch.netinfo.resolve_wsl_client_url", _boom)
    # A detection/probe failure must never break the CLI.
    assert client_url() == load_config().url


def test_config_module_importable():
    assert config_mod.DEFAULT_PORT == 9847


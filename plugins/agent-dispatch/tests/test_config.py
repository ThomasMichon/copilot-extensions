"""Tests for coordinator configuration resolution."""

from __future__ import annotations

import pytest

from agent_dispatch import config as config_mod
from agent_dispatch import rendezvous
from agent_dispatch.config import DEFAULT_SWEEP_INTERVAL, client_url, load_config


@pytest.fixture(autouse=True)
def _isolate_discovery(monkeypatch, tmp_path):
    """Isolate endpoint discovery from ambient state so client_url is deterministic.

    Points the rendezvous run dir at an empty tmp dir and clears the endpoint /
    Windows-mount overrides, so tests never read a live coordinator's rendezvous
    file (which would resolve to a 'discovered' URL).
    """
    monkeypatch.setenv("AGENT_DISPATCH_RUN_DIR", str(tmp_path / "run"))
    for var in (
        "AGENT_DISPATCH_ENDPOINT",
        "AGENT_DISPATCH_WINDOWS_RUN_DIR",
        "AGENT_DISPATCH_WINDOWS_MOUNT",
    ):
        monkeypatch.delenv(var, raising=False)


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


# -- endpoint discovery (Phase 3 Stage A/B) ---------------------------------


def test_client_url_discovers_local_endpoint(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENT_DISPATCH_URL", raising=False)
    monkeypatch.setattr("agent_dispatch.netinfo.is_wsl", lambda: False)
    # Treat the advertised endpoint as live (skip the connect probe).
    monkeypatch.setattr(rendezvous, "connect_probe", lambda ep, **k: True)
    run = tmp_path / "run"
    monkeypatch.setenv("AGENT_DISPATCH_RUN_DIR", str(run))
    rendezvous.write_endpoint(run, "tcp", "127.0.0.1:55123")
    assert client_url() == "http://127.0.0.1:55123"


def test_client_url_endpoint_env_override(monkeypatch):
    monkeypatch.delenv("AGENT_DISPATCH_URL", raising=False)
    monkeypatch.setattr("agent_dispatch.netinfo.is_wsl", lambda: False)
    monkeypatch.setenv("AGENT_DISPATCH_ENDPOINT", "tcp:127.0.0.1:23456")
    assert client_url() == "http://127.0.0.1:23456"


def test_client_url_no_file_falls_back_to_fixed(monkeypatch):
    monkeypatch.delenv("AGENT_DISPATCH_URL", raising=False)
    monkeypatch.setattr("agent_dispatch.netinfo.is_wsl", lambda: False)
    # Empty run dir (from the autouse fixture) -> no discovery -> fixed default.
    assert client_url() == load_config().url


def test_client_url_wsl_uses_discovered_port(monkeypatch):
    monkeypatch.delenv("AGENT_DISPATCH_URL", raising=False)
    monkeypatch.setattr("agent_dispatch.netinfo.is_wsl", lambda: True)
    monkeypatch.setattr(
        "agent_dispatch.netinfo.resolve_wsl_client_url",
        lambda port: f"http://172.19.240.1:{port}",
    )
    # A discovered port (here via the endpoint override) flows into the WSL URL.
    monkeypatch.setenv("AGENT_DISPATCH_ENDPOINT", "tcp:127.0.0.1:51000")
    assert client_url() == "http://172.19.240.1:51000"



# -- shared/elected coordinator resolution (cross-machine dispatch) ----------


def test_shared_url_unset_is_none(monkeypatch):
    monkeypatch.delenv("AGENT_DISPATCH_SHARED_URL", raising=False)
    assert config_mod.shared_url() is None


def test_shared_url_from_env(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_SHARED_URL", "https://gateway.example/dispatch")
    assert config_mod.shared_url() == "https://gateway.example/dispatch"


def test_shared_token_is_independent_of_local_token(monkeypatch):
    # The shared bearer does NOT fall back to AGENT_DISPATCH_TOKEN -- the two
    # coordinators authenticate separately.
    monkeypatch.setenv("AGENT_DISPATCH_TOKEN", "local-secret")
    monkeypatch.delenv("AGENT_DISPATCH_SHARED_TOKEN", raising=False)
    assert config_mod.shared_token() is None
    monkeypatch.setenv("AGENT_DISPATCH_SHARED_TOKEN", "shared-secret")
    assert config_mod.shared_token() == "shared-secret"

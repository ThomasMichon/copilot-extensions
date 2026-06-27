"""Tests for BridgeClient.from_config() routing-table integration."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent_bridge import routing
from agent_bridge.client import BridgeClient


@pytest.fixture
def cfg_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A config dir with a valid auth token and a config.yaml port."""
    (tmp_path / "auth.yaml").write_text(yaml.dump({"token": "tok-123"}))
    (tmp_path / "config.yaml").write_text(yaml.dump({"port": 9281, "bind": "127.0.0.1"}))
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("AGENT_BRIDGE_BASE_URL", raising=False)
    monkeypatch.delenv("AGENT_BRIDGE_NO_ROUTING_TABLE", raising=False)
    return tmp_path


def test_falls_back_to_config_port_when_no_table(cfg_dir: Path):
    client = BridgeClient.from_config()
    assert client._base == "http://127.0.0.1:9281"


def test_prefers_routing_table_over_config(cfg_dir: Path, monkeypatch):
    # Pretend the active endpoint moved to a new port (skip listener probe so
    # the test needs no real socket).
    monkeypatch.setattr(routing, "_listening", lambda *a, **k: True)
    routing.publish_active(cfg_dir, bind="127.0.0.1", port=9290, version="v")
    client = BridgeClient.from_config()
    assert client._base == "http://127.0.0.1:9290"


def test_no_routing_table_env_forces_config_port(cfg_dir: Path, monkeypatch):
    monkeypatch.setattr(routing, "_listening", lambda *a, **k: True)
    routing.publish_active(cfg_dir, bind="127.0.0.1", port=9290, version="v")
    monkeypatch.setenv("AGENT_BRIDGE_NO_ROUTING_TABLE", "1")
    client = BridgeClient.from_config()
    assert client._base == "http://127.0.0.1:9281"


def test_explicit_base_url_env_wins(cfg_dir: Path, monkeypatch):
    monkeypatch.setattr(routing, "_listening", lambda *a, **k: True)
    routing.publish_active(cfg_dir, bind="127.0.0.1", port=9290, version="v")
    monkeypatch.setenv("AGENT_BRIDGE_BASE_URL", "http://127.0.0.1:9299/")
    client = BridgeClient.from_config()
    assert client._base == "http://127.0.0.1:9299"


def test_stale_table_falls_back_to_config(cfg_dir: Path, monkeypatch):
    # Active points at a dead port (no listener, no pid) -> resolver returns
    # None -> client uses config fallback.
    monkeypatch.setattr(routing, "_listening", lambda *a, **k: False)
    routing.publish_active(cfg_dir, bind="127.0.0.1", port=9290)
    client = BridgeClient.from_config()
    assert client._base == "http://127.0.0.1:9281"

"""Tests for agent-bridge daemon URL resolution (dotfiles #826).

The daemon binds an OS-assigned ephemeral port (post-#694) and advertises it in
``~/.agent-bridge/active.json``. ``_resolve_bridge_url`` must read that table so
container agents register against the LIVE daemon, not the retired hardcoded
:9280 default -- otherwise ``bridge register`` fails after any daemon restart.
"""

from __future__ import annotations

import json

from agent_containers import bridge_provider


def test_resolves_port_from_active_json(tmp_path, monkeypatch):
    (tmp_path / "active.json").write_text(
        json.dumps({"active": {"bind": "127.0.0.1", "port": 54411}})
    )
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("AGENT_BRIDGE_BASE_URL", raising=False)
    assert bridge_provider._resolve_bridge_url() == "http://127.0.0.1:54411"


def test_wildcard_bind_maps_to_loopback(tmp_path, monkeypatch):
    (tmp_path / "active.json").write_text(
        json.dumps({"active": {"bind": "0.0.0.0", "port": 60123}})  # noqa: S104 -- test input, never bound
    )
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("AGENT_BRIDGE_BASE_URL", raising=False)
    assert bridge_provider._resolve_bridge_url() == "http://127.0.0.1:60123"


def test_falls_back_to_default_when_no_table(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("AGENT_BRIDGE_BASE_URL", raising=False)
    assert bridge_provider._resolve_bridge_url() == bridge_provider.DEFAULT_BRIDGE_URL


def test_falls_back_to_default_on_malformed_table(tmp_path, monkeypatch):
    (tmp_path / "active.json").write_text("{ not json")
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("AGENT_BRIDGE_BASE_URL", raising=False)
    assert bridge_provider._resolve_bridge_url() == bridge_provider.DEFAULT_BRIDGE_URL


def test_falls_back_to_default_on_zero_port(tmp_path, monkeypatch):
    (tmp_path / "active.json").write_text(
        json.dumps({"active": {"bind": "127.0.0.1", "port": 0}})
    )
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("AGENT_BRIDGE_BASE_URL", raising=False)
    assert bridge_provider._resolve_bridge_url() == bridge_provider.DEFAULT_BRIDGE_URL


def test_explicit_base_url_env_wins(tmp_path, monkeypatch):
    (tmp_path / "active.json").write_text(
        json.dumps({"active": {"bind": "127.0.0.1", "port": 54411}})
    )
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_BRIDGE_BASE_URL", "http://127.0.0.1:9999/")
    assert bridge_provider._resolve_bridge_url() == "http://127.0.0.1:9999"

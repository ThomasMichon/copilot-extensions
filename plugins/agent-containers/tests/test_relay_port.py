"""Tests for the credential-relay port resolution in the container exec wrapper.

Covers dotfiles #1631: the container wrapper must inject the agent-bridge daemon's
*live* relay port (published via ``relay_state``), not a stale static default, so
in-container ADO/git + build-cache auth keeps working after the relay binds an
ephemeral port. Falls back to the configured default when no live port is known.
"""

from __future__ import annotations

import sys
import types

from agent_containers.__main__ import _resolve_relay_port


def _install_fake_relay_state(monkeypatch, live):
    """Install a fake ``agent_bridge.relay_state`` exposing ``get_live_relay_port``."""
    pkg = types.ModuleType("agent_bridge")
    mod = types.ModuleType("agent_bridge.relay_state")
    mod.get_live_relay_port = lambda: live  # type: ignore[attr-defined]
    pkg.relay_state = mod  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "agent_bridge", pkg)
    monkeypatch.setitem(sys.modules, "agent_bridge.relay_state", mod)


def test_prefers_live_relay_port(monkeypatch):
    _install_fake_relay_state(monkeypatch, 62839)
    assert _resolve_relay_port(9857) == 62839


def test_falls_back_to_default_when_no_live_port(monkeypatch):
    _install_fake_relay_state(monkeypatch, None)
    assert _resolve_relay_port(9857) == 9857


def test_falls_back_to_default_when_agent_bridge_missing(monkeypatch):
    # Simulate agent-bridge not importable from the wrapper process.
    monkeypatch.setitem(sys.modules, "agent_bridge", None)
    monkeypatch.setitem(sys.modules, "agent_bridge.relay_state", None)
    assert _resolve_relay_port(9857) == 9857


def test_live_port_is_coerced_to_int(monkeypatch):
    _install_fake_relay_state(monkeypatch, "62839")
    result = _resolve_relay_port(9857)
    assert result == 62839
    assert isinstance(result, int)

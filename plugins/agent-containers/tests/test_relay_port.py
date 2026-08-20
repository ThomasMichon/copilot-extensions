"""Tests for credential-relay port resolution in the container exec wrapper.

Covers dotfiles #1631: the container wrapper must inject the agent-bridge daemon's
*live* relay port (published to ``<config_dir>/relay-port``), not a stale static
default, so in-container ADO/git + build-cache auth keeps working after the relay
binds an ephemeral port. Reads the published file **directly** (the wrapper runs
in agent-containers' own venv, where ``agent_bridge`` is NOT importable), and
falls back to the configured default when the file is absent/empty/unreadable.
"""

from __future__ import annotations

from pathlib import Path

from agent_containers.__main__ import _live_relay_port_file, _resolve_relay_port


def test_prefers_live_relay_port_from_file(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    (tmp_path / "relay-port").write_text("62839", encoding="utf-8")
    assert _resolve_relay_port(9857) == 62839


def test_falls_back_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))  # no relay-port file
    assert _resolve_relay_port(9857) == 9857


def test_falls_back_when_file_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    (tmp_path / "relay-port").write_text("   ", encoding="utf-8")
    assert _resolve_relay_port(9857) == 9857


def test_falls_back_when_file_garbage(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    (tmp_path / "relay-port").write_text("not-a-port", encoding="utf-8")
    assert _resolve_relay_port(9857) == 9857


def test_result_is_int(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    (tmp_path / "relay-port").write_text("62839\n", encoding="utf-8")
    result = _resolve_relay_port(9857)
    assert result == 62839 and isinstance(result, int)


def test_elevated_subdir_resolves_to_primary(tmp_path, monkeypatch):
    # An elevated sub-daemon config dir (<primary>/elevated) must read the
    # primary's published port, not a (nonexistent) elevated one.
    primary = tmp_path
    (primary / "relay-port").write_text("55123", encoding="utf-8")
    elevated = primary / "elevated"
    elevated.mkdir()
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(elevated))
    assert _live_relay_port_file() == primary / "relay-port"
    assert _resolve_relay_port(9857) == 55123


def test_default_config_dir_when_env_unset(tmp_path, monkeypatch):
    # With no env override, the file path is under ~/.agent-bridge (expanduser
    # honors USERPROFILE on Windows and HOME on POSIX).
    monkeypatch.delenv("AGENT_BRIDGE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    assert _live_relay_port_file() == tmp_path / ".agent-bridge" / "relay-port"

"""Smoke tests for the elevated sub-daemon launcher helpers (no UAC/Task Sched)."""

from __future__ import annotations

import socket

from agent_bridge import elevated


def test_is_up_false_on_closed_port():
    # Pick an almost-certainly-free high port and confirm is_up reports down.
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    free_port = s.getsockname()[1]
    s.close()
    assert elevated.is_up(free_port, timeout=0.5) is False


def test_elevated_dir_under_config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    ed = elevated.elevated_dir()
    assert ed == tmp_path / "elevated"
    assert ed.is_dir()


def test_read_token_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    assert elevated.read_token() is None


def test_constants():
    assert elevated.ELEVATED_PORT == 9281
    assert elevated.TASK_NAME == "agent-bridge-elevated"

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


def test_relay_spawn_command_shape():
    cmd = elevated.relay_spawn_command("SPO.Core", token="tok123", port=9281)
    assert cmd[1:] == [
        "-m", "agent_bridge", "acp-connect",
        "ws://127.0.0.1:9281/acp/SPO.Core", "--token", "tok123", "--stdio",
    ]
    # First element is the interpreter that hosts agent_bridge.
    assert cmd[0].lower().endswith(("python", "python.exe", "python3"))


def test_relay_applicable_false_when_not_requires_admin():
    assert elevated.relay_applicable(False) is False


def test_relay_applicable_off_windows(monkeypatch):
    import sys as _sys

    monkeypatch.setattr(_sys, "platform", "linux")
    assert elevated.relay_applicable(True) is False


def test_relay_applicable_true_on_windows_non_elevated(monkeypatch):
    import sys as _sys

    monkeypatch.setattr(_sys, "platform", "win32")
    monkeypatch.setattr(elevated, "is_process_elevated", lambda: False)
    assert elevated.relay_applicable(True) is True


def test_relay_not_applicable_when_already_elevated(monkeypatch):
    import sys as _sys

    monkeypatch.setattr(_sys, "platform", "win32")
    monkeypatch.setattr(elevated, "is_process_elevated", lambda: True)
    assert elevated.relay_applicable(True) is False

"""Tests for ``auto_unlock`` prompt resolution.

The unlock command must reach the operator wherever they are. A controlling
terminal wins over the (blocking) service-side GUI dialog -- if the operator
typed ``unlock`` at a terminal, we prompt inline there directly and never risk
the GUI stall. Only without a controlling terminal does it fall to the service
GUI (WSL) / client GUI (Linux); with neither, it returns ``False`` rather than
stalling.
"""

from __future__ import annotations

import pytest

from agent_vault import cli


@pytest.fixture(autouse=True)
def _no_terminal_env(monkeypatch):
    monkeypatch.delenv("VAULT_UNLOCK_TERMINAL", raising=False)


def _boom(*_a, **_k):
    raise AssertionError("must not be called")


# ---------------------------------------------------------------------------
# Controlling terminal wins -- inline directly, never the blocking GUI
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("is_wsl", [True, False])
def test_tty_prompts_inline_directly_without_gui(monkeypatch, is_wsl):
    monkeypatch.setattr(cli, "IS_WSL", is_wsl)
    monkeypatch.setattr(cli, "_has_controlling_tty", lambda: True)
    monkeypatch.setattr(cli, "_terminal_unlock_local", lambda: True)
    # The blocking GUI paths must NOT be consulted when a terminal is available.
    monkeypatch.setattr(cli, "_server_prompted_unlock", _boom)
    monkeypatch.setattr(cli, "prompt_password", _boom)
    assert cli.auto_unlock() is True


# ---------------------------------------------------------------------------
# No controlling terminal: WSL -> service GUI; Linux -> client GUI
# ---------------------------------------------------------------------------

def test_wsl_no_tty_uses_service_gui(monkeypatch):
    monkeypatch.setattr(cli, "IS_WSL", True)
    monkeypatch.setattr(cli, "_has_controlling_tty", lambda: False)
    monkeypatch.setattr(cli, "_server_prompted_unlock", lambda: True)
    monkeypatch.setattr(cli, "_terminal_unlock_local", _boom)
    assert cli.auto_unlock() is True


def test_wsl_no_tty_service_gui_fails_returns_false(monkeypatch):
    monkeypatch.setattr(cli, "IS_WSL", True)
    monkeypatch.setattr(cli, "_has_controlling_tty", lambda: False)
    monkeypatch.setattr(cli, "_server_prompted_unlock", lambda: False)
    monkeypatch.setattr(cli, "_terminal_unlock_local", _boom)
    assert cli.auto_unlock() is False


def test_non_wsl_no_tty_uses_client_gui(monkeypatch):
    monkeypatch.setattr(cli, "IS_WSL", False)
    monkeypatch.setattr(cli, "_has_controlling_tty", lambda: False)
    monkeypatch.setattr(cli, "prompt_password", lambda: "hunter2")
    monkeypatch.setattr(cli, "send_command", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(cli, "_terminal_unlock_local", _boom)
    assert cli.auto_unlock() is True


def test_non_wsl_no_tty_no_gui_returns_false(monkeypatch):
    monkeypatch.setattr(cli, "IS_WSL", False)
    monkeypatch.setattr(cli, "_has_controlling_tty", lambda: False)
    monkeypatch.setattr(cli, "prompt_password", lambda: None)  # no GUI available
    monkeypatch.setattr(cli, "_terminal_unlock_local", _boom)
    assert cli.auto_unlock() is False


# ---------------------------------------------------------------------------
# Explicit terminal override still wins
# ---------------------------------------------------------------------------

def test_env_override_forces_terminal(monkeypatch):
    monkeypatch.setenv("VAULT_UNLOCK_TERMINAL", "1")
    monkeypatch.setattr(cli, "_terminal_unlock_local", lambda: True)
    monkeypatch.setattr(cli, "_server_prompted_unlock", _boom)
    monkeypatch.setattr(cli, "prompt_password", _boom)
    assert cli.auto_unlock() is True

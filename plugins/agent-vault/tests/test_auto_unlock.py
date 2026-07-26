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

# The real implementation, captured before the autouse stub replaces the module
# binding -- the unit test below exercises it directly.
_REAL_PROVIDER_ONLY_UNLOCK = cli._provider_only_unlock


@pytest.fixture(autouse=True)
def _no_terminal_env(monkeypatch):
    monkeypatch.delenv("VAULT_UNLOCK_TERMINAL", raising=False)


def _boom(*_a, **_k):
    raise AssertionError("must not be called")


@pytest.fixture(autouse=True)
def _no_provider_unlock(monkeypatch):
    """Default the broker-first provider unlock to a miss so the prompt-fallback
    paths are exercised; the broker-first test overrides this explicitly."""
    monkeypatch.setattr(cli, "_provider_only_unlock", lambda: False)


# ---------------------------------------------------------------------------
# Broker-first: a held provider password unlocks silently, before any prompt
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("is_wsl", [True, False])
def test_provider_unlock_wins_before_any_prompt(monkeypatch, is_wsl):
    """When an unlock-source provider (e.g. the operator-held broker deposit)
    resolves, `auto_unlock` returns True without touching any prompt path."""
    monkeypatch.setattr(cli, "IS_WSL", is_wsl)
    monkeypatch.setattr(cli, "_provider_only_unlock", lambda: True)
    monkeypatch.setattr(cli, "_has_controlling_tty", _boom)
    monkeypatch.setattr(cli, "_terminal_unlock_local", _boom)
    monkeypatch.setattr(cli, "_server_prompted_unlock", _boom)
    monkeypatch.setattr(cli, "prompt_password", _boom)
    assert cli.auto_unlock() is True


def test_provider_only_unlock_sends_passwordless_unlock(monkeypatch):
    """`_provider_only_unlock` sends a passwordless unlock (no prompt) and maps the
    daemon's ok verdict to a bool -- the daemon runs its providers server-side."""
    seen = {}

    def _send(request, timeout=None):
        seen["request"] = request
        seen["timeout"] = timeout
        return {"ok": True}

    monkeypatch.setattr(cli, "_provider_only_unlock", _REAL_PROVIDER_ONLY_UNLOCK)
    monkeypatch.setattr(cli, "send_command", _send)
    assert cli._provider_only_unlock() is True
    assert seen["request"] == {"action": "unlock"}
    assert "password" not in seen["request"]
    assert seen["request"].get("prompt") is None
    # A needs_unlock / unreachable answer is a miss.
    monkeypatch.setattr(cli, "send_command", lambda *a, **k: {"ok": False, "needs_unlock": True})
    assert cli._provider_only_unlock() is False
    monkeypatch.setattr(cli, "send_command", lambda *a, **k: None)
    assert cli._provider_only_unlock() is False


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

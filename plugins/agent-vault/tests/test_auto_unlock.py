"""Tests for ``auto_unlock`` prompt resolution and the inline-TTY fallback.

The unlock command must reach the operator wherever they are: after the
service/GUI prompt path is unavailable or fails, ``auto_unlock`` falls back to an
inline terminal prompt when a controlling terminal is present -- so a bare
``unlock`` works on a headless/SSH host with no GUI. When there is no GUI *and*
no controlling terminal, it returns ``False`` rather than stalling.
"""

from __future__ import annotations

import pytest

from agent_vault import cli


@pytest.fixture(autouse=True)
def _no_terminal_env(monkeypatch):
    monkeypatch.delenv("VAULT_UNLOCK_TERMINAL", raising=False)


# ---------------------------------------------------------------------------
# WSL branch: server/GUI prompt then inline-TTY fallback
# ---------------------------------------------------------------------------

def test_wsl_falls_back_to_inline_tty_when_server_prompt_fails(monkeypatch):
    monkeypatch.setattr(cli, "IS_WSL", True)
    monkeypatch.setattr(cli, "_server_prompted_unlock", lambda: False)
    monkeypatch.setattr(cli, "_has_controlling_tty", lambda: True)
    called = {}
    monkeypatch.setattr(
        cli, "_terminal_unlock_local", lambda: called.setdefault("inline", True) or True
    )
    assert cli.auto_unlock() is True
    assert called.get("inline") is True


def test_wsl_no_tty_fails_fast_without_inline(monkeypatch):
    monkeypatch.setattr(cli, "IS_WSL", True)
    monkeypatch.setattr(cli, "_server_prompted_unlock", lambda: False)
    monkeypatch.setattr(cli, "_has_controlling_tty", lambda: False)
    monkeypatch.setattr(
        cli, "_terminal_unlock_local",
        lambda: (_ for _ in ()).throw(AssertionError("inline must not run without a TTY")),
    )
    assert cli.auto_unlock() is False


def test_wsl_server_prompt_success_skips_inline(monkeypatch):
    monkeypatch.setattr(cli, "IS_WSL", True)
    monkeypatch.setattr(cli, "_server_prompted_unlock", lambda: True)
    monkeypatch.setattr(
        cli, "_terminal_unlock_local",
        lambda: (_ for _ in ()).throw(AssertionError("inline must not run on success")),
    )
    assert cli.auto_unlock() is True


# ---------------------------------------------------------------------------
# Non-WSL branch: GUI prompt then inline-TTY fallback
# ---------------------------------------------------------------------------

def test_non_wsl_no_gui_falls_back_to_inline(monkeypatch):
    monkeypatch.setattr(cli, "IS_WSL", False)
    monkeypatch.setattr(cli, "prompt_password", lambda: None)  # no GUI available
    monkeypatch.setattr(cli, "_has_controlling_tty", lambda: True)
    monkeypatch.setattr(cli, "_terminal_unlock_local", lambda: True)
    assert cli.auto_unlock() is True


def test_non_wsl_gui_success_no_inline(monkeypatch):
    monkeypatch.setattr(cli, "IS_WSL", False)
    monkeypatch.setattr(cli, "prompt_password", lambda: "hunter2")
    monkeypatch.setattr(cli, "send_command", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(
        cli, "_terminal_unlock_local",
        lambda: (_ for _ in ()).throw(AssertionError("inline must not run on GUI success")),
    )
    assert cli.auto_unlock() is True


def test_non_wsl_no_gui_no_tty_fails(monkeypatch):
    monkeypatch.setattr(cli, "IS_WSL", False)
    monkeypatch.setattr(cli, "prompt_password", lambda: None)
    monkeypatch.setattr(cli, "_has_controlling_tty", lambda: False)
    assert cli.auto_unlock() is False


# ---------------------------------------------------------------------------
# Explicit terminal override still wins
# ---------------------------------------------------------------------------

def test_env_override_forces_terminal(monkeypatch):
    monkeypatch.setenv("VAULT_UNLOCK_TERMINAL", "1")
    monkeypatch.setattr(cli, "_terminal_unlock_local", lambda: True)
    monkeypatch.setattr(
        cli, "_server_prompted_unlock",
        lambda: (_ for _ in ()).throw(AssertionError("must not consult the service")),
    )
    assert cli.auto_unlock() is True

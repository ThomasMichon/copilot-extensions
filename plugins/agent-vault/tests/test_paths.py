"""Tests for env-overridable runtime endpoint paths (socket / pid / log)."""

from __future__ import annotations

import importlib
from pathlib import Path

import agent_vault.config as config


def _reload_clean(monkeypatch):
    for var in (config.SOCKET_ENV, config.PID_ENV, config.LOG_ENV):
        monkeypatch.delenv(var, raising=False)
    importlib.reload(config)


def test_paths_env_override(monkeypatch):
    monkeypatch.setenv("AGENT_VAULT_SOCKET", "/tmp/custom-vault.sock")
    monkeypatch.setenv("AGENT_VAULT_PID", "/tmp/custom-vault.pid")
    monkeypatch.setenv("AGENT_VAULT_LOG", "/tmp/custom-vault.log")
    importlib.reload(config)
    try:
        assert config.SOCKET_PATH == "/tmp/custom-vault.sock"
        assert config.PID_FILE == "/tmp/custom-vault.pid"
        assert config.LOG_FILE == Path("/tmp/custom-vault.log")
        # The resolved config surfaces the overridden socket too.
        assert config.VaultConfig().socket_path == "/tmp/custom-vault.sock"
    finally:
        _reload_clean(monkeypatch)


def test_paths_default_without_env(monkeypatch):
    _reload_clean(monkeypatch)
    try:
        assert config.SOCKET_PATH == config.DEFAULT_SOCKET_PATH
        assert config.PID_FILE.endswith("agent-vault-service.pid")
        assert str(config.LOG_FILE).endswith("agent-vault-service.log")
    finally:
        _reload_clean(monkeypatch)

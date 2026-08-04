"""Tests for ACP model flag propagation."""

from __future__ import annotations

import json
from pathlib import Path

from agent_codespaces.model_launch import build_model_flags, resolve_model_config


def _settings(home: Path, text: str) -> None:
    path = home / ".copilot" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding="utf-8")


def _isolated_home(monkeypatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for name in (
        "AGENT_CODESPACES_MODEL_PROPAGATE",
        "AGENT_CODESPACES_ACP_MODEL",
        "AGENT_CODESPACES_ACP_EFFORT",
        "AGENT_CODESPACES_ACP_CONTEXT",
    ):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def test_resolve_uses_host_settings(monkeypatch, tmp_path: Path) -> None:
    home = _isolated_home(monkeypatch, tmp_path)
    _settings(
        home,
        """
        {
          // comments in settings.json are tolerated
          "model": "claude-opus-4.8",
          "effortLevel": "high",
          "contextTier": "long_context"
        }
        """,
    )

    assert resolve_model_config() == {
        "model": "claude-opus-4.8",
        "effort": "high",
        "context": "long_context",
    }


def test_resolve_override_and_env_win_over_host(monkeypatch, tmp_path: Path) -> None:
    home = _isolated_home(monkeypatch, tmp_path)
    _settings(
        home,
        json.dumps({
            "model": "host-model",
            "effortLevel": "low",
            "contextTier": "default",
        }),
    )
    monkeypatch.setenv("AGENT_CODESPACES_ACP_MODEL", "env-model")
    monkeypatch.setenv("AGENT_CODESPACES_ACP_EFFORT", "medium")

    assert resolve_model_config({"effort": "high", "context": "long_context"}) == {
        "model": "env-model",
        "effort": "high",
        "context": "long_context",
    }


def test_resolve_opt_out_returns_empty(monkeypatch, tmp_path: Path) -> None:
    home = _isolated_home(monkeypatch, tmp_path)
    _settings(home, json.dumps({"model": "claude-opus-4.8"}))
    monkeypatch.setenv("AGENT_CODESPACES_MODEL_PROPAGATE", "false")
    monkeypatch.setenv("AGENT_CODESPACES_ACP_MODEL", "env-model")

    assert resolve_model_config({"model": "override-model"}) == {}


def test_resolve_missing_or_malformed_settings_is_empty(monkeypatch, tmp_path: Path) -> None:
    home = _isolated_home(monkeypatch, tmp_path)
    assert resolve_model_config() == {}

    _settings(home, "not json {{{")
    assert resolve_model_config() == {}


def test_build_model_flags_emits_present_keys_quoted_and_prefixed() -> None:
    flags = build_model_flags({
        "model": "claude opus",
        "effort": "high",
        "context": "long_context",
    })

    assert flags == " --model 'claude opus' --reasoning-effort high --context long_context"


def test_build_model_flags_emits_only_present_keys() -> None:
    assert build_model_flags({"effort": "high"}) == " --reasoning-effort high"


def test_build_model_flags_empty_when_none(monkeypatch, tmp_path: Path) -> None:
    _isolated_home(monkeypatch, tmp_path)
    assert build_model_flags() == ""

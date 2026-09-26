"""venue_copilot.models: a venue session mirrors the caller's model settings."""

from __future__ import annotations

from pathlib import Path

from venue_copilot.models import model_copilot_args, resolve_model_config


def _home(monkeypatch, tmp_path: Path, settings: str | None = None) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for name in (
        "AGENT_CODESPACES_MODEL_PROPAGATE",
        "AGENT_CODESPACES_ACP_MODEL",
        "AGENT_CODESPACES_ACP_EFFORT",
        "AGENT_CODESPACES_ACP_CONTEXT",
    ):
        monkeypatch.delenv(name, raising=False)
    if settings is not None:
        path = tmp_path / ".copilot" / "settings.json"
        path.parent.mkdir(parents=True)
        path.write_text(settings, encoding="utf-8")


def test_resolves_host_settings_with_comments(monkeypatch, tmp_path: Path) -> None:
    _home(monkeypatch, tmp_path, '{\n // note\n "model": "m1", "effortLevel": "high", "contextTier": "long"\n}')
    assert resolve_model_config() == {"model": "m1", "effort": "high", "context": "long"}


def test_env_overrides_host_settings(monkeypatch, tmp_path: Path) -> None:
    _home(monkeypatch, tmp_path, '{"model": "m1"}')
    monkeypatch.setenv("AGENT_CODESPACES_ACP_MODEL", "m2")
    assert resolve_model_config()["model"] == "m2"


def test_copilot_args_mirror_settings_and_respect_explicit_flags(monkeypatch, tmp_path: Path) -> None:
    _home(monkeypatch, tmp_path, '{"model": "m1", "effortLevel": "high", "contextTier": "long"}')
    assert model_copilot_args(["--no-ask-user"]) == [
        "--model=m1", "--reasoning-effort=high", "--context=long"]
    assert model_copilot_args(["--model=m9"]) == ["--reasoning-effort=high", "--context=long"]


def test_opt_out_and_missing_settings_yield_nothing(monkeypatch, tmp_path: Path) -> None:
    _home(monkeypatch, tmp_path)
    assert model_copilot_args([]) == []
    _home(monkeypatch, tmp_path, '{"model": "m1"}')
    monkeypatch.setenv("AGENT_CODESPACES_MODEL_PROPAGATE", "no")
    assert model_copilot_args([]) == []
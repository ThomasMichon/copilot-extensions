"""Detached target-mode launch must not inject caller defaults."""

from venue_copilot import models
from agent_containers.copilot_detach import _with_caller_model


def test_target_launch_preferences(monkeypatch):
    monkeypatch.setenv("AGENT_BRIDGE_PREFERENCE_SOURCE", "target-settings")
    monkeypatch.delenv("AGENT_CODESPACES_MODEL_PROPAGATE", raising=False)
    for key in ("AGENT_CODESPACES_ACP_MODEL", "AGENT_CODESPACES_ACP_EFFORT",
                "AGENT_CODESPACES_ACP_CONTEXT", "COPILOT_PROVIDER_BASE_URL", "COPILOT_OFFLINE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(models, "_host_settings_config",
                        lambda: (_ for _ in ()).throw(AssertionError("caller settings read")))
    assert _with_caller_model(["--model=explicit"]) == ["--model=explicit"]
    monkeypatch.setenv("AGENT_CODESPACES_ACP_MODEL", "environment-choice")
    assert _with_caller_model([]) == ["--model=environment-choice"]
    monkeypatch.setenv("COPILOT_PROVIDER_BASE_URL", "http://localhost:1")
    assert _with_caller_model([]) == []


def test_caller_mode_remains_default(monkeypatch):
    monkeypatch.delenv("AGENT_BRIDGE_PREFERENCE_SOURCE", raising=False)
    monkeypatch.delenv("AGENT_CODESPACES_MODEL_PROPAGATE", raising=False)
    for key in ("AGENT_CODESPACES_ACP_MODEL", "AGENT_CODESPACES_ACP_EFFORT",
                "AGENT_CODESPACES_ACP_CONTEXT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(models, "_host_settings_config", lambda: {"model": "caller"})
    assert _with_caller_model([]) == ["--model=caller"]

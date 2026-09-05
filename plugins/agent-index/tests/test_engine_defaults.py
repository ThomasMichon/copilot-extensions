"""Tests for the torch-free-service defaults (effort agent-index-engine-daemon).

The versioned service runtime is torch-free and routes ALL embedding through the
durable engine daemon, so the standing defaults are engine_mode="external" and
search_in_process=off. Env vars still override for a single-venv install.
"""

from __future__ import annotations

import pytest

from agent_index import __version__
from agent_index.engine.generation import current_engine_generation
from agent_index.index_config import IndexConfig, ModelProfile


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("AGENT_INDEX_ENGINE_MODE", raising=False)
    monkeypatch.delenv("AGENT_INDEX_ENGINE_GENERATION", raising=False)
    monkeypatch.delenv("AGENT_INDEX_SEARCH_IN_PROCESS", raising=False)


def test_engine_mode_defaults_to_external():
    assert ModelProfile(model_id="code", model_name="m").engine_mode == "external"


def test_engine_generation_defaults_independently_from_service_version():
    assert current_engine_generation() == "engine-v1"
    assert current_engine_generation() != __version__


def test_default_model_profile_is_external():
    # The registry-built profile inherits the env default too (no explicit mode).
    profiles = IndexConfig().model_profiles
    assert profiles["code"].engine_mode == "external"


def test_search_in_process_defaults_off():
    assert IndexConfig().search_in_process is False


def test_engine_mode_env_override(monkeypatch):
    monkeypatch.setenv("AGENT_INDEX_ENGINE_MODE", "subprocess")
    assert ModelProfile(model_id="code", model_name="m").engine_mode == "subprocess"


def test_engine_generation_env_override(monkeypatch):
    monkeypatch.setenv("AGENT_INDEX_ENGINE_GENERATION", "engine-v2")
    assert current_engine_generation() == "engine-v2"


def test_search_in_process_env_override(monkeypatch):
    monkeypatch.setenv("AGENT_INDEX_SEARCH_IN_PROCESS", "1")
    assert IndexConfig().search_in_process is True

"""Tests for config-driven role resolution (host vs client).

Role is pure configuration -- the plugin encodes no machine names (effort
agent-index-engine-daemon). Precedence: AGENT_INDEX_ROLE env, then the
machine-local config file's role:/engine: scalar, else client.
"""

from __future__ import annotations

import pytest

from agent_index import config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    # Isolate every test from the real environment / home config.
    monkeypatch.delenv("AGENT_INDEX_ROLE", raising=False)
    monkeypatch.delenv("AGENT_INDEX_CONFIG", raising=False)
    monkeypatch.setenv("AGENT_INDEX_HOME", str(tmp_path))
    return tmp_path


def test_default_role_is_client(_clean_env):
    assert config.resolve_role() == "client"


def test_env_overrides_config(monkeypatch, _clean_env):
    (_clean_env / "config.yaml").write_text("role: host\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_INDEX_ROLE", "client")
    assert config.resolve_role() == "client"


@pytest.mark.parametrize("value", ["host", "HOST", " Host ", "client", "Client"])
def test_env_role_normalized(monkeypatch, _clean_env, value):
    monkeypatch.setenv("AGENT_INDEX_ROLE", value)
    assert config.resolve_role() == value.strip().lower()


def test_invalid_env_falls_through_to_default(monkeypatch, _clean_env):
    monkeypatch.setenv("AGENT_INDEX_ROLE", "banana")
    assert config.resolve_role() == "client"


def test_config_role_host(_clean_env):
    (_clean_env / "config.yaml").write_text("# machine role\nrole: host\n", encoding="utf-8")
    assert config.resolve_role() == "host"


def test_config_role_quoted_with_comment(_clean_env):
    (_clean_env / "config.yaml").write_text('role: "host"  # this box hosts the engine\n', encoding="utf-8")
    assert config.resolve_role() == "host"


def test_config_engine_alias(_clean_env):
    (_clean_env / "config.yaml").write_text("engine: host\n", encoding="utf-8")
    assert config.resolve_role() == "host"


def test_config_synonyms(_clean_env):
    (_clean_env / "config.yaml").write_text("role: indexer\n", encoding="utf-8")
    assert config.resolve_role() == "host"
    (_clean_env / "config.yaml").write_text("role: consumer\n", encoding="utf-8")
    assert config.resolve_role() == "client"


def test_explicit_config_path_override(monkeypatch, tmp_path):
    cfg = tmp_path / "custom.yaml"
    cfg.write_text("role: host\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_INDEX_CONFIG", str(cfg))
    assert config.config_path() == cfg
    assert config.resolve_role() == "host"


def test_missing_config_file_is_client(_clean_env):
    assert not (_clean_env / "config.yaml").exists()
    assert config.resolve_role() == "client"

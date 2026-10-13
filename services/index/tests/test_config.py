from __future__ import annotations

import copy
import os

import pytest

from agent_index_service import __version__
from agent_index_service.composition import core_environment
from agent_index_service.config import ConfigurationError, load_config, parse_config


def test_defaults_and_environment(config_data, tmp_path):
    config = parse_config(config_data, tmp_path / "host.yaml")
    env = config.core_environment()
    assert config.data == config.home / "data"
    assert config.routing == config.home
    assert env["AGENT_INDEX_RUNTIME_VERSION"] == __version__
    assert "AGENT_INDEX_SERVER_VENV_PYTHON" not in env
    assert env["AGENT_INDEX_ENGINE_MODE"] == "external"
    assert env["AGENT_INDEX_SEARCH_IN_PROCESS"] == "0"
    assert env["AGENT_INDEX_SOURCE_MODE"] == "explicit"
    assert not config.home.exists()


@pytest.mark.parametrize(("key", "value"), [
    ("schema_version", True), ("schema_version", 2), ("schema_version", "1"),
    ("home", "relative"), ("home", None), ("sources", []), ("sources", {}),
    ("listener", {"host": "0.0.0.0"}), ("listener", {"port": True}),
    ("listener", {"port": 65536}), ("listener", {"port": -1}),
    ("engine", {"host": "remote.example"}), ("engine", {"mode": "subprocess"}),
    ("engine", {"mode": "auto"}), ("engine", {"port": 0}),
    ("engine", {"device": "arbitrary"}), ("limits", {"batch_size": 0}),
    ("limits", {"stream_batch_size": "64"}), ("limits", {"indexer_nice": 20}),
    ("data", "relative"), ("routing", "relative"),
    ("unknown", 1), ("limits", {"unknown": 1}),
    ("listener", ["127.0.0.1"]),
])
def test_invalid_config(config_data, tmp_path, key, value):
    data = copy.deepcopy(config_data)
    data[key] = value
    with pytest.raises(ConfigurationError):
        parse_config(data, tmp_path / "host.yaml")


@pytest.mark.parametrize("source", [
    {"name": "git:fixture"},
    {"name": "git:fixture", "path": "relative"},
    {"name": "git:fixture", "type": []},
    {"name": "other:fixture"},
    {"name": "github:owner"},
    {"name": "github:owner/repo:issues"},
    {"name": "github:owner/repo", "path": "irrelevant"},
    {"name": "github:owner/repo", "auth": {"token": "not-accepted"}},
    {"name": "git:fixture", "repo": "registry-resolution-is-not-accepted"},
    {"name": " git:fixture"},
])
def test_invalid_sources(config_data, tmp_path, source):
    config_data["sources"] = [source]
    with pytest.raises(ConfigurationError):
        parse_config(config_data, tmp_path / "host.yaml")


def test_duplicate_sources(config_data, tmp_path):
    config_data["sources"].append({
        "name": "git:FIXTURE", "path": str(tmp_path / "other"),
    })
    with pytest.raises(ConfigurationError, match="unique"):
        parse_config(config_data, tmp_path / "host.yaml")


@pytest.mark.parametrize("text", [
    "schema_version: 1\nschema_version: 1\n",
    "home: {nested: 1, nested: 2}",
    "- a\n- b\n",
    "{invalid",
])
def test_invalid_yaml(tmp_path, text):
    path = tmp_path / "host.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_config(path)


def test_missing_file(tmp_path):
    with pytest.raises(ConfigurationError, match="cannot read"):
        load_config(tmp_path / "absent.yaml")


def test_invalid_encoding(tmp_path):
    path = tmp_path / "host.yaml"
    path.write_bytes(b"\xff\xfe")
    with pytest.raises(ConfigurationError, match="cannot read"):
        load_config(path)


def test_environment_overrides_are_scoped(monkeypatch, config_data, tmp_path):
    config = parse_config(config_data, tmp_path / "host.yaml")
    monkeypatch.setenv("AGENT_INDEX_ENGINE_MODE", "subprocess")
    monkeypatch.setenv("AGENT_INDEX_SOURCE_MODE", "ambient")
    monkeypatch.setenv("AGENT_INDEX_REPO", str(tmp_path / "unselected"))
    monkeypatch.setenv("AGENT_INDEX_RUNTIME_VERSION", "plugin-version")
    monkeypatch.setenv("AGENT_INDEX_SERVER_VENV_PYTHON", str(tmp_path / "other-python"))
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "unselected-cell")
    before = dict(os.environ)
    with core_environment(config):
        assert "AGENT_INDEX_REPO" not in os.environ
        assert "COPILOT_EXTENSIONS_CONTEXT" not in os.environ
        assert os.environ["AGENT_INDEX_ENGINE_MODE"] == "external"
        assert os.environ["AGENT_INDEX_RUNTIME_VERSION"] == __version__
        assert "AGENT_INDEX_SERVER_VENV_PYTHON" not in os.environ
    assert dict(os.environ) == before


def test_explicit_legacy_selection_not_implicit(config_data, tmp_path):
    # A simulated legacy home, never the operator's actual home.
    config_data["home"] = str(tmp_path / ".agent-index")
    config = parse_config(config_data, tmp_path / "host.yaml")
    assert config.home == tmp_path / ".agent-index"
    assert config.data == config.home / "data"


def test_github_and_source_metadata(config_data, tmp_path):
    config_data["sources"] = [{
        "name": "github:owner/repo", "auth": {"account": "selected"},
        "trust_domain": "work",
    }]
    config = parse_config(config_data, tmp_path / "host.yaml")
    assert config.sources[0]["auth"] == {"account": "selected"}
    assert config.sources[0]["trust_domain"] == "work"

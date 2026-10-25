from __future__ import annotations

import copy
import os

import pytest

from agent_index_service import __version__
from agent_index_service.composition import core_environment
from agent_index_service.config import ConfigurationError, load_config, parse_config


def test_source_layout_opt_in_is_explicit_and_scoped(config_data, tmp_path, monkeypatch):
    assert "AGENT_INDEX_STORE_LAYOUT" not in parse_config(
        config_data, tmp_path / "host.yaml",
    ).core_environment()
    config_data["store"] = {"layout": "source-aware-v1"}
    selected = parse_config(config_data, tmp_path / "host.yaml")
    monkeypatch.setenv("AGENT_INDEX_STORE_LAYOUT", "unrelated-ambient")
    with core_environment(selected):
        assert os.environ["AGENT_INDEX_STORE_LAYOUT"] == "source-aware-v1"
    assert os.environ["AGENT_INDEX_STORE_LAYOUT"] == "unrelated-ambient"


@pytest.mark.parametrize("store", [{}, {"layout": "legacy"}, {"layout": "unknown"},
                                  {"layout": "source-aware-v1", "adopt_legacy": True}])
def test_unknown_or_adoptive_layout_not_accepted(config_data, tmp_path, store):
    config_data["store"] = store
    with pytest.raises(ConfigurationError):
        parse_config(config_data, tmp_path / "host.yaml")


def test_optional_fleet_policy_is_explicit_and_scoped(config_data, tmp_path, monkeypatch):
    assert "AGENT_INDEX_FLEET_POLICY" not in parse_config(
        config_data, tmp_path / "host.yaml",
    ).core_environment()
    policy = tmp_path / "policy.json"
    config_data["fleet"] = {"policy": str(policy)}
    config_data["store"] = {"layout": "source-aware-v1"}
    selected = parse_config(config_data, tmp_path / "host.yaml")
    monkeypatch.setenv("AGENT_INDEX_FLEET_POLICY", "ambient-not-selected")
    with core_environment(selected):
        assert os.environ["AGENT_INDEX_FLEET_POLICY"] == str(policy.resolve())
    assert os.environ["AGENT_INDEX_FLEET_POLICY"] == "ambient-not-selected"


@pytest.mark.parametrize("fleet", [
    {"policy": "relative"}, {"policy": None}, {"policy": "x", "token": "not-accepted"},
])
def test_invalid_fleet_policy_reference(config_data, tmp_path, fleet):
    config_data["fleet"] = fleet
    config_data["store"] = {"layout": "source-aware-v1"}
    with pytest.raises(ConfigurationError):
        parse_config(config_data, tmp_path / "host.yaml")


def test_fleet_policy_cannot_implicitly_select_or_adopt_store(config_data, tmp_path):
    config_data["fleet"] = {"policy": str(tmp_path / "policy.json")}
    with pytest.raises(ConfigurationError, match="explicit store.layout"):
        parse_config(config_data, tmp_path / "host.yaml")


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


def test_explicit_installation_identity_is_projected_without_ambient_spoof(config_data, tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_INDEX_INSTALLATION_ID", "ambient-spoof")
    unspecified = parse_config(config_data, tmp_path / "host.yaml")
    with core_environment(unspecified):
        assert "AGENT_INDEX_INSTALLATION_ID" not in os.environ
    config_data["installation_id"] = "persistent-native-instance"
    selected = parse_config(config_data, tmp_path / "host.yaml")
    with core_environment(selected):
        assert os.environ["AGENT_INDEX_NATIVE_INSTALLATION_ID"] == "persistent-native-instance"
        assert "AGENT_INDEX_INSTALLATION_ID" not in os.environ
    assert os.environ["AGENT_INDEX_INSTALLATION_ID"] == "ambient-spoof"


@pytest.mark.parametrize("identity", [None, "", " leading", "line\nbreak", "not ascii \u2603", "x" * 257])
def test_invalid_installation_identity(config_data, tmp_path, identity):
    config_data["installation_id"] = identity
    with pytest.raises(ConfigurationError):
        parse_config(config_data, tmp_path / "host.yaml")


def test_account_relay_git_remote_is_explicit_and_preserved(config_data, tmp_path):
    policy = tmp_path / "worker-policy.json"
    config_data["sources"] = [{
        "name": "git:fixture", "path": str(tmp_path / "repository"),
        "remote": "https://github.com/example/project.git",
        "auth": {"account": "account-a", "relay": str(policy)},
    }]
    result = parse_config(config_data, tmp_path / "host.yaml")
    assert result.sources[0]["auth"] == {"account": "account-a", "relay": str(policy.resolve())}
    assert result.sources[0]["remote"] == "https://github.com/example/project.git"
    assert result.core_environment()["AGENT_INDEX_HOST"] == "127.0.0.1"
    config_data["sources"][0]["auth"]["gateway"] = str(tmp_path / "other.json")
    with pytest.raises(ConfigurationError, match="mutually exclusive"):
        parse_config(config_data, tmp_path / "host.yaml")


@pytest.mark.parametrize("auth", [
    {"relay": "relative", "account": "account-a"},
    {"relay": "relative"},
])
def test_invalid_account_relay_references(config_data, tmp_path, auth):
    config_data["sources"] = [{"name": "github:example/project", "auth": auth}]
    with pytest.raises(ConfigurationError):
        parse_config(config_data, tmp_path / "host.yaml")


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


def test_gateway_ingestion_selection_survives_detached_worker_environment(config_data, tmp_path):
    policy = tmp_path / "worker.json"
    checkout = tmp_path / "checkouts" / "repo"
    config_data["sources"] = [
        {"name": "git:repo", "path": str(checkout), "ref": "origin/dev",
         "remote": "https://example.test/owner/repo.git",
         "auth": {"account": "selected", "gateway": str(policy)}},
        {"name": "github:owner/repo",
         "auth": {"account": "selected", "gateway": str(policy)}},
    ]
    config = parse_config(config_data, tmp_path / "host.yaml")
    import base64
    import json

    encoded = config.core_environment()["AGENT_INDEX_CONFIG_DATA_B64"]
    sources = json.loads(base64.urlsafe_b64decode(encoded))["corpus"]["sources"]
    assert sources[0]["_repo_path"] == str(checkout.resolve())
    assert sources[0]["remote"] == "https://example.test/owner/repo.git"
    assert sources[0]["ref"] == "origin/dev"
    assert all(s["auth"] == {"account": "selected", "gateway": str(policy)} for s in sources)
    assert not checkout.exists()


@pytest.mark.parametrize("change", [
    {"remote": "https://user:secret@example.test/repo.git"},
    {"remote": "http://example.test/repo.git"},
    {"remote": None},
    {"auth": {"account": "selected", "gateway": "relative"}},
    {"auth": {"account": "selected", "gateway": "x", "token": "forbidden"}},
])
def test_invalid_gateway_source_selection(config_data, tmp_path, change):
    source = {
        "name": "git:repo", "path": str(tmp_path / "checkout"),
        "remote": "https://example.test/repo.git",
        "auth": {"account": "selected", "gateway": str(tmp_path / "worker.json")},
    }
    source.update(change)
    config_data["sources"] = [source]
    with pytest.raises(ConfigurationError):
        parse_config(config_data, tmp_path / "host.yaml")

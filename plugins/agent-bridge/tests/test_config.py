"""Tests for config management commands (adopt, remove, validate)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from agent_bridge.config import (
    adopt_topology,
    load_config,
    remove_topology,
    save_config,
    validate_config,
)
from agent_bridge.models import ServiceConfig, TopologyProfile


@pytest.fixture()
def config_home(tmp_path, monkeypatch):
    """Point agent-bridge config dir to a temp directory."""
    config_dir = tmp_path / ".agent-bridge"
    config_dir.mkdir()
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(config_dir))
    return config_dir


@pytest.fixture()
def fake_repo(tmp_path):
    """Create a fake repo with machines.yaml and acp-agents.json."""
    repo = tmp_path / "my-repo"
    repo.mkdir()
    (repo / "machines.yaml").write_text(yaml.dump({"machines": {"test": {}}}))
    agents_dir = repo / "tools" / "mcp"
    agents_dir.mkdir(parents=True)
    (agents_dir / "acp-agents.json").write_text(json.dumps({"test-agent": {}}))
    return repo


class TestSaveConfig:
    def test_roundtrip(self, config_home):
        cfg = ServiceConfig(port=9999, bind="0.0.0.0")
        save_config(cfg)
        loaded = load_config()
        assert loaded.port == 9999
        assert loaded.bind == "0.0.0.0"


class TestAdoptTopology:
    def test_auto_discovers_files(self, config_home, fake_repo):
        cfg = adopt_topology("test-profile", str(fake_repo))
        assert "test-profile" in cfg.topologies
        profile = cfg.topologies["test-profile"]
        assert profile.machines_yaml is not None
        assert "machines.yaml" in profile.machines_yaml
        assert profile.agents_config is not None
        assert "acp-agents.json" in profile.agents_config

    def test_persists_to_disk(self, config_home, fake_repo):
        adopt_topology("saved", str(fake_repo))
        loaded = load_config()
        assert "saved" in loaded.topologies

    def test_updates_existing_profile(self, config_home, fake_repo):
        adopt_topology("same", str(fake_repo))
        # Create a second repo with different files
        repo2 = fake_repo.parent / "repo2"
        repo2.mkdir()
        (repo2 / "machines.yaml").write_text(yaml.dump({"machines": {}}))
        adopt_topology("same", str(repo2))
        loaded = load_config()
        assert "repo2" in loaded.topologies["same"].machines_yaml

    def test_explicit_paths(self, config_home, fake_repo):
        machines = str(fake_repo / "machines.yaml")
        agents = str(fake_repo / "tools" / "mcp" / "acp-agents.json")
        cfg = adopt_topology("explicit", str(fake_repo),
                             machines_yaml=machines, agents_config=agents)
        assert cfg.topologies["explicit"].machines_yaml is not None
        assert cfg.topologies["explicit"].agents_config is not None

    def test_no_files_raises(self, config_home, tmp_path):
        empty = tmp_path / "empty-repo"
        empty.mkdir()
        with pytest.raises(FileNotFoundError, match="No machines.yaml"):
            adopt_topology("fail", str(empty))

    def test_missing_repo_raises(self, config_home, tmp_path):
        with pytest.raises(FileNotFoundError, match="does not exist"):
            adopt_topology("fail", str(tmp_path / "nope"))

    def test_forward_slash_normalization(self, config_home, fake_repo):
        cfg = adopt_topology("slashes", str(fake_repo))
        profile = cfg.topologies["slashes"]
        if profile.machines_yaml:
            assert "\\" not in profile.machines_yaml


class TestRemoveTopology:
    def test_removes_profile(self, config_home, fake_repo):
        adopt_topology("to-remove", str(fake_repo))
        remove_topology("to-remove")
        loaded = load_config()
        assert "to-remove" not in loaded.topologies

    def test_missing_profile_raises(self, config_home):
        with pytest.raises(KeyError, match="not found"):
            remove_topology("nonexistent")


class TestValidateConfig:
    def test_valid_config(self, config_home, fake_repo):
        adopt_topology("valid", str(fake_repo))
        issues = validate_config()
        assert issues == []

    def test_no_topologies(self, config_home):
        save_config(ServiceConfig())
        issues = validate_config()
        assert any("No topology" in i for i in issues)

    def test_missing_file(self, config_home):
        cfg = ServiceConfig(topologies={
            "bad": TopologyProfile(machines_yaml="/nonexistent/machines.yaml")
        })
        save_config(cfg)
        issues = validate_config()
        assert any("not found" in i for i in issues)

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

    def test_credential_relay_default_and_roundtrip(self, config_home):
        # Defaults on (primary daemon owns the relay); the elevated sub-daemon
        # seeds it off so it never re-binds/evicts the primary's relay.
        assert ServiceConfig().enable_credential_relay is True
        save_config(ServiceConfig(enable_credential_relay=False))
        assert load_config().enable_credential_relay is False

    def test_session_host_enabled_default_on(self, config_home):
        # Session Hosts are the durable-dispatch default now (#145/#177).
        assert ServiceConfig().session_host_enabled is True


class TestMigrateConfig:
    def test_flips_stale_off_to_on_once(self, config_home):
        from agent_bridge.config import migrate_config

        # A machine still on the OLD explicit default (off)...
        save_config(ServiceConfig(session_host_enabled=False))
        migrated = migrate_config(load_config())
        assert migrated.session_host_enabled is True
        # ...persisted to disk...
        assert load_config().session_host_enabled is True
        # ...and the marker is written.
        assert (config_home / ".migrations" / "session_host_default_on").exists()

    def test_respects_opt_out_after_marker(self, config_home):
        from agent_bridge.config import migrate_config

        # Marker already present (migration ran) -> a deliberate opt-out sticks.
        (config_home / ".migrations").mkdir(parents=True)
        (config_home / ".migrations" / "session_host_default_on").write_text("applied\n")
        save_config(ServiceConfig(session_host_enabled=False))
        migrated = migrate_config(load_config())
        assert migrated.session_host_enabled is False
        assert load_config().session_host_enabled is False

    def test_idempotent_leaves_on_untouched(self, config_home):
        from agent_bridge.config import migrate_config

        save_config(ServiceConfig(session_host_enabled=True))
        migrated = migrate_config(load_config())
        assert migrated.session_host_enabled is True

    def test_idle_reap_default_armed(self, config_home):
        # The idle-session reaper is armed by default now (#1826 complement to
        # Session Hosts being default-on).
        assert ServiceConfig().idle_reap_ttl_seconds == 600
        assert ServiceConfig().idle_reap_sweep_seconds == 120

    def test_idle_reap_flips_stale_zero_to_600_once(self, config_home):
        from agent_bridge.config import migrate_config

        # A machine still carrying the OLD explicit disabled value...
        save_config(ServiceConfig(idle_reap_ttl_seconds=0))
        migrated = migrate_config(load_config())
        assert migrated.idle_reap_ttl_seconds == 600
        # ...persisted...
        assert load_config().idle_reap_ttl_seconds == 600
        # ...and its own marker is written.
        assert (config_home / ".migrations" / "idle_reap_default_on").exists()

    def test_idle_reap_respects_opt_out_after_marker(self, config_home):
        from agent_bridge.config import migrate_config

        # Marker already present -> a deliberate 0 (opt-out) sticks.
        (config_home / ".migrations").mkdir(parents=True)
        (config_home / ".migrations" / "idle_reap_default_on").write_text("applied\n")
        save_config(ServiceConfig(idle_reap_ttl_seconds=0))
        migrated = migrate_config(load_config())
        assert migrated.idle_reap_ttl_seconds == 0
        assert load_config().idle_reap_ttl_seconds == 0

    def test_idle_reap_idempotent_leaves_custom_untouched(self, config_home):
        from agent_bridge.config import migrate_config

        # A non-zero value (default or custom) is never touched by the migration.
        save_config(ServiceConfig(idle_reap_ttl_seconds=300))
        migrated = migrate_config(load_config())
        assert migrated.idle_reap_ttl_seconds == 300


class TestAdoptTopology:
    def test_auto_discovers_machines_not_agents(self, config_home, fake_repo):
        # machines.yaml is auto-discovered; acp-agents.json is NOT (retired --
        # the roster is derived from topology). An acp-agents.json present in the
        # repo is ignored unless passed explicitly as agents_config.
        cfg = adopt_topology("test-profile", str(fake_repo))
        assert "test-profile" in cfg.topologies
        profile = cfg.topologies["test-profile"]
        assert profile.machines_yaml is not None
        assert "machines.yaml" in profile.machines_yaml
        assert profile.agents_config is None

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

    def test_no_files_raises(self, config_home, tmp_path, monkeypatch):
        empty = tmp_path / "empty-repo"
        empty.mkdir()
        # No state-root overlay either.
        monkeypatch.setattr(
            "agent_bridge.config._state_root_machines_yaml", lambda repo: None)
        with pytest.raises(FileNotFoundError, match="No machines.yaml"):
            adopt_topology("fail", str(empty))

    def test_discovers_agent_worktrees_canonical_location(self, config_home, tmp_path):
        # The canonical .agent-worktrees/machines.yaml location (#950) is found.
        repo = tmp_path / "aw-repo"
        (repo / ".agent-worktrees").mkdir(parents=True)
        (repo / ".agent-worktrees" / "machines.yaml").write_text(
            yaml.dump({"machines": {"m": {}}}))
        cfg = adopt_topology("aw", str(repo))
        assert ".agent-worktrees" in cfg.topologies["aw"].machines_yaml

    def test_state_root_overlay_fallback(self, config_home, tmp_path, monkeypatch):
        # A stateless harness with no machines.yaml redirects to the knowledge
        # repo's machines.yaml resolved via the state-root overlay (E1e, #947).
        harness = tmp_path / "citadel-harness"
        harness.mkdir()
        knowledge = tmp_path / "citadel-knowledge"
        (knowledge / ".agent-worktrees").mkdir(parents=True)
        kmach = knowledge / ".agent-worktrees" / "machines.yaml"
        kmach.write_text(yaml.dump({"machines": {"dev6": {}}}))
        monkeypatch.setattr(
            "agent_bridge.config._state_root_machines_yaml",
            lambda repo: str(kmach) if Path(repo) == harness else None)
        cfg = adopt_topology("stateless", str(harness))
        assert cfg.topologies["stateless"].machines_yaml is not None
        assert "citadel-knowledge" in cfg.topologies["stateless"].machines_yaml

    def test_missing_repo_raises(self, config_home, tmp_path):
        with pytest.raises(FileNotFoundError, match="does not exist"):
            adopt_topology("fail", str(tmp_path / "nope"))

    def test_forward_slash_normalization(self, config_home, fake_repo):
        cfg = adopt_topology("slashes", str(fake_repo))
        profile = cfg.topologies["slashes"]
        if profile.machines_yaml:
            assert "\\" not in profile.machines_yaml


class TestStateRootMachinesYaml:
    """_state_root_machines_yaml -- the E1e state-root overlay resolver (#947)."""

    def _fake_which(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: "agent-worktrees")

    def _fake_state_root(self, monkeypatch, payload, *, rc=0):
        import types
        self._fake_which(monkeypatch)
        proc = types.SimpleNamespace(returncode=rc, stdout=json.dumps(payload),
                                     stderr="")
        monkeypatch.setattr("subprocess.run", lambda *a, **k: proc)

    def test_resolves_knowledge_machines_yaml(self, tmp_path, monkeypatch):
        from agent_bridge.config import _state_root_machines_yaml
        knowledge = tmp_path / "knowledge"
        (knowledge / ".agent-worktrees").mkdir(parents=True)
        (knowledge / ".agent-worktrees" / "machines.yaml").write_text("machines: {}")
        self._fake_state_root(monkeypatch, {
            "state_root": str(knowledge), "requires_external": True, "bound": True,
        })
        got = _state_root_machines_yaml(tmp_path / "harness")
        assert got is not None and "knowledge" in got

    def test_self_hosted_not_redirected(self, tmp_path, monkeypatch):
        from agent_bridge.config import _state_root_machines_yaml
        # A self-hosted repo (requires_external False) must NOT be grafted.
        self._fake_state_root(monkeypatch, {
            "state_root": str(tmp_path), "requires_external": False, "bound": True,
        })
        assert _state_root_machines_yaml(tmp_path / "harness") is None

    def test_no_binstub_returns_none(self, tmp_path, monkeypatch):
        from agent_bridge.config import _state_root_machines_yaml
        monkeypatch.setattr("shutil.which", lambda name: None)
        assert _state_root_machines_yaml(tmp_path) is None

    def test_nonzero_exit_returns_none(self, tmp_path, monkeypatch):
        from agent_bridge.config import _state_root_machines_yaml
        self._fake_state_root(monkeypatch, {"error": "unbound"}, rc=3)
        assert _state_root_machines_yaml(tmp_path) is None


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

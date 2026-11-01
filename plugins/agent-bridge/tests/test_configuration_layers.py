from pathlib import Path

import pytest
import yaml
from configuration_layers import ConfigurationError

from agent_bridge import configuration
from agent_bridge.agent_registry_topology import _effective_spawn_defaults
from agent_bridge.config import load_repo_bridge_config
from agent_bridge.models import TopologyProfile


def write(root, values):
    path = root / ".copilot-extensions" / "agent-bridge" / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(values), encoding="utf-8")
    return path


def fixture_report(tmp_path, binding="bound"):
    base, knowledge, local = (tmp_path / part for part in ("base", "knowledge", "local"))
    for root in (base, knowledge, local):
        root.mkdir()
    context = {
        "repository_id": "repo-qualified-alpha",
        "execution_machine": "worker-logical",
        "installation_cell": "cell-alpha",
        "binding": binding,
        "base_root": str(base),
    }
    if binding == "bound":
        context["knowledge_root"] = str(knowledge)
    return {
        "schema": "agent-worktrees.configuration", "version": 1, "context": context,
        "repository_machine_root": str(local),
    }, base, knowledge, local


def test_native_spawn_consumer_composes_all_tiers(tmp_path, monkeypatch):
    report, base, knowledge, local = fixture_report(tmp_path)
    write(base, {"default_copilot_args": ["base"], "default_env": {"base": "yes", "winner": "base"}})
    write(knowledge, {"default_copilot_args": ["knowledge"], "default_env": {"winner": "knowledge"}})
    write(local, {"default_env": {"winner": "local"}})
    monkeypatch.setattr(configuration, "owner_report", lambda root: report)
    cfg = load_repo_bridge_config(base)
    assert cfg.default_copilot_args == ["knowledge"]
    args, env = _effective_spawn_defaults(
        TopologyProfile(default_copilot_args=["machine"], default_env={"machine": "yes", "winner": "machine"}),
        cfg,
    )
    assert args == ["machine"]
    assert env == {"base": "yes", "winner": "local", "machine": "yes"}
    owner = cfg._configuration_provenance["/default_env/winner"]
    assert owner["repository_id"] == "repo-qualified-alpha"
    assert owner["execution_machine"] == "worker-logical"
    assert owner["installation_cell"] == "cell-alpha"
    assert Path(owner["source"]["root"]) == local


def test_native_unbound_and_standalone_defaults(tmp_path, monkeypatch):
    report, base, knowledge, local = fixture_report(tmp_path, "unbound")
    write(knowledge, {"default_copilot_args": ["excluded"]})
    monkeypatch.setattr(configuration, "owner_report", lambda root: report)
    cfg = load_repo_bridge_config(base)
    assert _effective_spawn_defaults(TopologyProfile(), cfg) == ([], {})
    monkeypatch.setattr(configuration, "owner_report", lambda root: None)
    assert load_repo_bridge_config(base) is None


def test_unresolved_owner_never_falls_back(tmp_path, monkeypatch):
    report, base, knowledge, local = fixture_report(tmp_path, "unresolved")
    write(base, {"default_env": {"winner": "base"}})
    monkeypatch.setattr(configuration, "owner_report", lambda root: report)
    with pytest.raises(ConfigurationError, match="unresolved"):
        load_repo_bridge_config(base)


@pytest.mark.parametrize("field", ["remote_acp_bindings", "session_modes", "topologies", "db_path"])
def test_portable_source_cannot_overwrite_protected_fields(tmp_path, monkeypatch, field):
    report, base, knowledge, local = fixture_report(tmp_path)
    write(knowledge, {field: {}})
    monkeypatch.setattr(configuration, "owner_report", lambda root: report)
    with pytest.raises(ValueError, match="ordinary spawn"):
        load_repo_bridge_config(base)


def test_native_legacy_selection_and_malformed_canonical_refusal(tmp_path, monkeypatch):
    report, base, knowledge, local = fixture_report(tmp_path)
    monkeypatch.setattr(configuration, "owner_report", lambda root: report)
    legacy = base / ".agent-bridge" / "config.yaml"
    legacy.parent.mkdir()
    legacy.write_text("default_copilot_args: [legacy]\n", encoding="utf-8")
    assert load_repo_bridge_config(base).default_copilot_args == ["legacy"]
    canonical = write(base, {"default_copilot_args": ["canonical"]})
    assert load_repo_bridge_config(base).default_copilot_args == ["canonical"]
    canonical.write_text("[malformed, shape]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping|object"):
        load_repo_bridge_config(base)


def test_native_default_producer_uses_qualified_owner_prefix(tmp_path, monkeypatch):
    from agent_bridge import session_lifecycle_cli as native

    observed = {}
    monkeypatch.setattr(native, "_agent_worktrees_launch_prefix", lambda **kw: ["qualified-owner"])
    monkeypatch.setattr(native, "_agent_worktrees_environment", lambda: {"safe": "env"})

    def read(prefix, root, **kwargs):
        observed.update(prefix=prefix, root=root, **kwargs)
        return None

    monkeypatch.setattr(configuration, "read_owner_context", read)
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    assert configuration.owner_report(tmp_path) is None
    assert observed == {
        "prefix": ["qualified-owner"], "root": tmp_path,
        "required": False, "environment": {"safe": "env"},
    }


def test_native_preference_source_authority_is_not_portably_merged(tmp_path, monkeypatch):
    report, base, knowledge, local = fixture_report(tmp_path)
    write(base, {"default_env": {"AGENT_BRIDGE_PREFERENCE_SOURCE": "target-settings", "base": "yes"}})
    monkeypatch.setattr(configuration, "owner_report", lambda root: report)
    cfg = load_repo_bridge_config(base)
    assert _effective_spawn_defaults(TopologyProfile(), cfg)[1]["AGENT_BRIDGE_PREFERENCE_SOURCE"] == "target-settings"
    profile = TopologyProfile(default_env={"AGENT_BRIDGE_PREFERENCE_SOURCE": "caller-settings"})
    assert _effective_spawn_defaults(profile, cfg)[1]["AGENT_BRIDGE_PREFERENCE_SOURCE"] == "caller-settings"
    write(knowledge, {"default_env": {"AGENT_BRIDGE_PREFERENCE_SOURCE": "caller-settings"}})
    with pytest.raises(ValueError, match="authority"):
        load_repo_bridge_config(base)


def test_actual_absent_owner_keeps_unbound_native_consumer_without_bootstrap(tmp_path, monkeypatch):
    import configuration_context

    home = tmp_path / "home"
    home.mkdir()
    repo = tmp_path / "unbound-repository"
    repo.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    for name in ("AGENT_BRIDGE_INSTALL_DIR", "AGENT_BRIDGE_CONFIG_DIR",
                 "COPILOT_EXTENSIONS_CONTEXT", "AGENT_CONFIGURATION_CONTEXT"):
        monkeypatch.delenv(name, raising=False)

    def no_process(*args, **kwargs):
        pytest.fail("absent optional owner must not launch a front door or installer")

    monkeypatch.setattr(configuration_context.subprocess, "run", no_process)
    assert load_repo_bridge_config(repo) is None
    assert _effective_spawn_defaults(TopologyProfile(), None) == ([], {})
    assert list(home.iterdir()) == []
    assert list(repo.iterdir()) == []


def test_same_named_repositories_do_not_share_qualified_source_defaults(tmp_path, monkeypatch):
    roots, reports = [], {}
    for identity in ("qualified-alpha", "qualified-beta"):
        base = tmp_path / identity / "same-name"
        knowledge = tmp_path / identity / "knowledge"
        local = tmp_path / identity / "local"
        for root in (base, knowledge, local):
            root.mkdir(parents=True)
        write(knowledge, {"default_copilot_args": [identity]})
        reports[base] = {
            "schema": "agent-worktrees.configuration", "version": 1,
            "context": {
                "repository_id": identity, "execution_machine": "logical",
                "installation_cell": identity + "-cell", "binding": "bound",
                "base_root": str(base), "knowledge_root": str(knowledge),
            },
            "repository_machine_root": str(local),
        }
        roots.append(base)
    monkeypatch.setattr(configuration, "owner_report", lambda root: reports[root])
    first, second = (load_repo_bridge_config(root) for root in roots)
    assert _effective_spawn_defaults(TopologyProfile(), first)[0] == ["qualified-alpha"]
    assert _effective_spawn_defaults(TopologyProfile(), second)[0] == ["qualified-beta"]
    assert first._configuration_provenance["/default_copilot_args"]["repository_id"] == "qualified-alpha"


@pytest.mark.parametrize("text", ["", "null\n"])
def test_native_empty_yaml_source_remains_valid(tmp_path, monkeypatch, text):
    report, base, knowledge, local = fixture_report(tmp_path)
    monkeypatch.setattr(configuration, "owner_report", lambda root: report)
    source = write(base, {})
    source.write_text(text, encoding="utf-8")
    assert _effective_spawn_defaults(TopologyProfile(), load_repo_bridge_config(base)) == ([], {})


@pytest.mark.parametrize("implicit", [False, True])
@pytest.mark.parametrize("matching_cell", [False, True])
def test_real_loader_checks_delivered_cell_against_actual_owner(tmp_path, monkeypatch, implicit, matching_cell):
    import json
    from agent_bridge import _peer_launch, session_lifecycle_cli as native

    report, base, knowledge, local = fixture_report(tmp_path)
    write(knowledge, {"default_copilot_args": ["knowledge"]})
    cell = tmp_path / "marketplaces" / "current-cell"
    own_root = cell / "plugins" / "agent-bridge"
    own_root.mkdir(parents=True)
    report["context"]["installation_cell"] = str(cell if matching_cell else tmp_path / "foreign-cell")
    calls = []

    def validate(owner, root, raw):
        calls.append((owner, root, raw))
        assert root == own_root
        assert raw == str(own_root / "install.json")
        return {"cellRoot": str(cell), "pluginRoot": str(own_root)}

    monkeypatch.setattr(_peer_launch, "validate_owner", validate)
    monkeypatch.setattr(native, "_agent_bridge_owner_root", lambda: own_root)
    monkeypatch.setattr(native, "_agent_worktrees_launch_prefix", lambda **kwargs: None)
    if implicit:
        monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    else:
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(own_root / "install.json"))
    monkeypatch.setenv("AGENT_CONFIGURATION_CONTEXT", json.dumps(report))
    if matching_cell:
        assert load_repo_bridge_config(base).default_copilot_args == ["knowledge"]
    else:
        with pytest.raises(ConfigurationError, match="another installation cell"):
            load_repo_bridge_config(base)
    assert calls

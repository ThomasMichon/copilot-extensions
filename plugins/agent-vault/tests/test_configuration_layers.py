import json

import pytest
from configuration_layers import ConfigurationError

from agent_vault import config, configuration


def setup(tmp_path, monkeypatch, binding="bound"):
    base, knowledge, local = (tmp_path / part for part in ("base", "knowledge", "local"))
    for root in (base, knowledge, local):
        root.mkdir()
    global_path = tmp_path / "global.json"
    global_path.write_text(json.dumps({
        "vaults": {
            name: {"kpdb": str(tmp_path / f"{name}.kdbx")}
            for name in ("base", "knowledge", "machine", "local", "explicit")
        },
    }), encoding="utf-8")
    monkeypatch.setenv("AGENT_VAULT_CONFIG", str(global_path))
    for env in ("AGENT_VAULT", "KPDB", "VAULT_GROUP", "AGENT_VAULT_PORT", "AGENT_VAULT_PINNED_CONTEXT"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(config, "_ext_config", lambda cwd: {})
    context = {
        "repository_id": "repo-qualified", "execution_machine": "logical",
        "installation_cell": "cell", "binding": binding, "base_root": str(base),
    }
    if binding == "bound":
        context["knowledge_root"] = str(knowledge)
    report = {
        "schema": "agent-worktrees.configuration", "version": 1, "context": context,
        "repository_machine_root": str(local),
    }
    monkeypatch.setattr(configuration, "owner_report", lambda root: report)
    return base, knowledge, local, global_path, report


def write(root, values):
    (root / ".agent-vault.json").write_text(json.dumps(values), encoding="utf-8")


def test_native_vault_selector_all_tiers_without_opening_database(tmp_path, monkeypatch):
    base, knowledge, local, global_path, report = setup(tmp_path, monkeypatch)
    write(base, {"vault": "base", "group": "base-group"})
    write(knowledge, {"vault": "knowledge", "group": "knowledge-group"})
    assert config.resolve_context(str(base), strict=True).vault_name == "knowledge"
    global_data = json.loads(global_path.read_text())
    global_data["default_vault"] = "machine"
    global_data["group"] = "machine-group"
    global_path.write_text(json.dumps(global_data))
    assert config.resolve_context(str(base), strict=True).vault_name == "machine"
    write(local, {"vault": "local", "group": "local-group"})
    result = config.resolve_context(str(base), strict=True)
    assert result.vault_name == "local"
    assert result.group == "local-group"
    assert result.kpdb == str(tmp_path / "local.kdbx")
    assert not (tmp_path / "local.kdbx").exists()
    assert result.configuration_provenance["/vault"]["source"]["root"] == str(local)
    monkeypatch.setenv("AGENT_VAULT", "explicit")
    monkeypatch.setenv("VAULT_GROUP", "explicit-group")
    result = config.resolve_context(str(base), strict=True)
    assert result.vault_name == "explicit"
    assert result.group == "explicit-group"
    assert "/vault" not in result.configuration_provenance
    assert "/group" not in result.configuration_provenance


def test_native_unbound_and_unresolved_are_distinct(tmp_path, monkeypatch):
    base, knowledge, local, global_path, report = setup(tmp_path, monkeypatch, "unbound")
    write(base, {"vault": "base"})
    write(knowledge, {"vault": "knowledge"})
    assert config.resolve_context(str(base), strict=True).vault_name == "base"
    report["context"]["binding"] = "unresolved"
    with pytest.raises(ConfigurationError, match="unresolved"):
        config.resolve_context(str(base), strict=True)


@pytest.mark.parametrize("protected", ["kpdb", "port", "vaults", "socket_path", "cache", "kek"])
def test_portable_vault_selection_refuses_protected_state(tmp_path, monkeypatch, protected):
    base, knowledge, local, global_path, report = setup(tmp_path, monkeypatch)
    write(knowledge, {protected: {}})
    with pytest.raises(ValueError, match="custody"):
        config.resolve_context(str(base), strict=True)


def test_native_repository_database_path_retains_declaring_owner(tmp_path, monkeypatch):
    base, knowledge, local, global_path, report = setup(tmp_path, monkeypatch)
    write(base, {"vault": "base", "kpdb": "native.kdbx"})
    write(knowledge, {"group": "portable-group"})
    result = config.resolve_context(str(base), strict=True)
    assert result.kpdb == str(base / "native.kdbx")
    assert result.sources["kpdb"] == "repo"


def test_actual_absent_owner_keeps_unbound_vault_defaults_without_open_or_bootstrap(tmp_path, monkeypatch):
    import configuration_context

    for name in ("COPILOT_EXTENSIONS_CONTEXT", "AGENT_CONFIGURATION_CONTEXT",
                 "AGENT_VAULT", "KPDB", "VAULT_GROUP", "AGENT_VAULT_PORT",
                 "AGENT_VAULT_PINNED_CONTEXT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENT_VAULT_CONFIG", str(tmp_path / "absent-global.json"))
    monkeypatch.setattr(config, "_ext_config", lambda cwd: {})

    def no_process(*args, **kwargs):
        pytest.fail("vault selection must not install an owner or open a vault")

    monkeypatch.setattr(configuration_context.subprocess, "run", no_process)
    result = config.resolve_context(str(tmp_path), strict=True)
    assert result.vault_name is None
    assert result.kpdb is None
    assert result.port == config.DEFAULT_TCP_PORT
    assert list(tmp_path.iterdir()) == []


def test_native_unready_legacy_owner_preserves_vault_defaults(tmp_path, monkeypatch):
    import shutil
    from pathlib import Path
    import configuration_context

    home = tmp_path / "home"
    scripts = home / ".agent-vault" / "scripts"
    scripts.mkdir(parents=True)
    for source in (Path(configuration.__file__).parents[2] / "scripts").glob("versioned_runtime*.py"):
        shutil.copy2(source, scripts / source.name)
    peer = home / ".agent-worktrees"
    fake_python = peer / "versions" / "1.0.0" / "Scripts" / "python.exe"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text("not a completed runtime", encoding="utf-8")
    (peer / "current-version").write_text("1.0.0", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: home)
    for name in ("AGENT_VAULT_HOME", "AGENT_RT_ROOT", "COPILOT_EXTENSIONS_CONTEXT",
                 "AGENT_CONFIGURATION_CONTEXT", "AGENT_VAULT", "KPDB", "VAULT_GROUP",
                 "AGENT_VAULT_PORT", "AGENT_VAULT_PINNED_CONTEXT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENT_VAULT_CONFIG", str(tmp_path / "absent-global.json"))
    monkeypatch.setattr(config, "_ext_config", lambda cwd: {})

    def no_process(*args, **kwargs):
        pytest.fail("an unready optional owner must not bootstrap or open a vault")

    monkeypatch.setattr(configuration_context.subprocess, "run", no_process)
    assert config.resolve_context(str(tmp_path), strict=True).kpdb is None
    assert (scripts / "versioned_runtime.py").is_file()
    assert configuration._legacy_report(tmp_path) is None


@pytest.mark.parametrize("implicit", [False, True])
@pytest.mark.parametrize("matching_cell", [False, True])
def test_real_loader_checks_delivered_cell_against_actual_owner(tmp_path, monkeypatch, implicit, matching_cell):
    real_report = configuration.owner_report
    base, knowledge, local, global_path, report = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(configuration, "owner_report", real_report)
    write(base, {"vault": "base"})
    write(knowledge, {"vault": "knowledge"})
    cell = tmp_path / "marketplaces" / "current-cell"
    own_root = cell / "plugins" / "agent-vault"
    own_root.mkdir(parents=True)
    report["context"]["installation_cell"] = str(cell if matching_cell else tmp_path / "foreign-cell")
    calls = []

    def validate(owner, root, raw):
        calls.append((owner, root, raw))
        assert root == own_root
        assert raw == str(own_root / "install.json")
        return {"cellRoot": str(cell), "pluginRoot": str(own_root)}

    monkeypatch.setattr(configuration._peer_launch, "validate_owner", validate)
    monkeypatch.setenv("AGENT_RT_ROOT", str(own_root))
    if implicit:
        monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    else:
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(own_root / "install.json"))
    monkeypatch.setenv("AGENT_CONFIGURATION_CONTEXT", json.dumps(report))
    if matching_cell:
        assert config.resolve_context(str(base), strict=True).vault_name == "knowledge"
    else:
        with pytest.raises(ConfigurationError, match="another installation cell"):
            config.resolve_context(str(base), strict=True)
    assert calls


@pytest.mark.parametrize("tier", ["knowledge", "repository_machine"])
def test_tier_null_keeps_native_fallback_and_does_not_claim_false_provenance(tmp_path, monkeypatch, tier):
    base, knowledge, local, global_path, report = setup(tmp_path, monkeypatch)
    write(base, {"vault": "base", "group": "base-group"})
    if tier == "knowledge":
        write(knowledge, {"vault": None, "group": None})
        monkeypatch.setattr(config, "_ext_config", lambda cwd: {
            "vault": "explicit", "group": "extension-group",
        })
        expected_vault, expected_group = "explicit", "extension-group"
        vault_source, group_source = "ext", "ext"
    else:
        write(knowledge, {"vault": "knowledge", "group": "knowledge-group"})
        data = json.loads(global_path.read_text())
        data.update(default_vault="machine", group="machine-group")
        data["vaults"]["machine"]["group"] = "registered-group"
        global_path.write_text(json.dumps(data), encoding="utf-8")
        write(local, {"vault": None, "group": None})
        expected_vault, expected_group = "machine", "registered-group"
        vault_source, group_source = "global", "vault:machine"
    result = config.resolve_context(str(base), strict=True)
    assert (result.vault_name, result.group) == (expected_vault, expected_group)
    assert result.sources["vault"] == vault_source
    assert result.sources["group"] == group_source
    assert "/vault" not in result.configuration_provenance
    assert "/group" not in result.configuration_provenance
    assert not (tmp_path / f"{expected_vault}.kdbx").exists()

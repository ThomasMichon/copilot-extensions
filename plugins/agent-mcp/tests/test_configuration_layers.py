import json

import pytest
import yaml
from configuration_layers import ConfigurationError

from agent_mcp import config, configuration


def setup(tmp_path, monkeypatch, binding="bound"):
    base, knowledge, local, machine = (tmp_path / part for part in ("base", "knowledge", "local", "machine"))
    for root in (base, knowledge, local, machine):
        root.mkdir()
    selected = tmp_path / "selected.mcp.yaml"
    selected.write_text("server:\n  type: http\n  url: https://example.invalid/mcp\ntimeout: 10\n", encoding="utf-8")
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
    monkeypatch.setattr(config, "OVERRIDES_DIR", machine)
    return selected, base, knowledge, local, machine, report


def write(root, values):
    path = root / ".copilot-extensions" / "agent-mcp" / "bridges" / "selected.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(values), encoding="utf-8")


def test_native_mcp_loader_all_tiers_and_protected_source(tmp_path, monkeypatch):
    selected, base, knowledge, local, machine, report = setup(tmp_path, monkeypatch)
    write(base, {"timeout": 20, "retries": 2})
    write(knowledge, {"timeout": 30, "retries": 3})
    (machine / "selected.yaml").write_text("timeout: 40\nheaders:\n  X-Native: retained\n", encoding="utf-8")
    write(local, {"timeout": 50})
    result = config.load_config(str(selected))
    assert (result.timeout, result.retries) == (50, 3)
    assert result.server.url == "https://example.invalid/mcp"
    assert result.headers == {"X-Native": "retained"}
    assert result.source_path == selected
    assert result.configuration_provenance["/timeout"]["installation_cell"] == "cell"
    assert result.configuration_provenance["/timeout"]["source"]["root"] == str(local)


def test_native_kn_then_machine_precedence(tmp_path, monkeypatch):
    selected, base, knowledge, local, machine, report = setup(tmp_path, monkeypatch)
    write(knowledge, {"timeout": 30})
    assert config.load_config(str(selected)).timeout == 30
    (machine / "selected.json").write_text(json.dumps({"timeout": 40}), encoding="utf-8")
    assert config.load_config(str(selected)).timeout == 40


def test_native_unbound_default_and_explicit_path(tmp_path, monkeypatch):
    selected, base, knowledge, local, machine, report = setup(tmp_path, monkeypatch, "unbound")
    write(knowledge, {"timeout": 999})
    assert config.load_config(str(selected)).timeout == 10
    report["context"]["binding"] = "unresolved"
    with pytest.raises(ConfigurationError, match="unresolved"):
        config.load_config(str(selected))


@pytest.mark.parametrize("protected", ["auth", "server", "headers", "routing", "decorators", "tools"])
def test_portable_timing_cannot_change_native_protected_schema(tmp_path, monkeypatch, protected):
    selected, base, knowledge, local, machine, report = setup(tmp_path, monkeypatch)
    write(knowledge, {protected: {}})
    with pytest.raises(ValueError, match="cannot change"):
        config.load_config(str(selected))


def test_native_environment_idle_selector_preserved(tmp_path, monkeypatch):
    selected, base, knowledge, local, machine, report = setup(tmp_path, monkeypatch)
    monkeypatch.setenv("AGENT_MCP_BRIDGE_IDLE_TIMEOUT", "17")
    result = config.load_config(str(selected))
    assert result.idle_timeout == 17
    assert "/idle_timeout" not in result.configuration_provenance
    assert result.configuration_selectors == {"idle_timeout": "AGENT_MCP_BRIDGE_IDLE_TIMEOUT"}
    write(knowledge, {"idle_timeout": 18})
    assert config.load_config(str(selected)).idle_timeout == 18


def test_actual_absent_owner_preserves_unbound_explicit_bridge_without_bootstrap(tmp_path, monkeypatch):
    import configuration_context

    for name in ("COPILOT_EXTENSIONS_CONTEXT", "AGENT_CONFIGURATION_CONTEXT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "OVERRIDES_DIR", tmp_path / "absent-overrides")
    selected = tmp_path / "standalone.yaml"
    selected.write_text("server:\n  url: https://example.invalid/mcp\n", encoding="utf-8")

    def no_process(*args, **kwargs):
        pytest.fail("native config loading must not install or launch an optional owner")

    monkeypatch.setattr(configuration_context.subprocess, "run", no_process)
    result = config.load_config(str(selected))
    assert (result.timeout, result.retries) == (30.0, 1)
    assert sorted(item.name for item in tmp_path.iterdir()) == ["standalone.yaml"]


def test_native_unready_legacy_owner_preserves_bridge_defaults(tmp_path, monkeypatch):
    import shutil
    from pathlib import Path
    import configuration_context

    home = tmp_path / "home"
    scripts = home / ".agent-mcp" / "scripts"
    scripts.mkdir(parents=True)
    for source in (Path(configuration.__file__).parents[2] / "scripts").glob("versioned_runtime*.py"):
        shutil.copy2(source, scripts / source.name)
    peer = home / ".agent-worktrees"
    fake_python = peer / "versions" / "1.0.0" / "Scripts" / "python.exe"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text("not a completed runtime", encoding="utf-8")
    (peer / "current-version").write_text("1.0.0", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: home)
    for name in ("AGENT_MCP_HOME", "AGENT_RT_ROOT", "COPILOT_EXTENSIONS_CONTEXT", "AGENT_CONFIGURATION_CONTEXT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "OVERRIDES_DIR", tmp_path / "absent-overrides")
    selected = tmp_path / "bridge.yaml"
    selected.write_text("server:\n  url: https://example.invalid/mcp\n", encoding="utf-8")

    def no_process(*args, **kwargs):
        pytest.fail("an unready optional owner must not bootstrap")

    monkeypatch.setattr(configuration_context.subprocess, "run", no_process)
    assert config.load_config(str(selected)).timeout == 30.0
    assert (scripts / "versioned_runtime.py").is_file()
    assert configuration._legacy_report(tmp_path) is None

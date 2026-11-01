import argparse

import pytest
import yaml
from configuration_layers import ConfigurationError

from agent_ssh import configuration, mesh


def write(root, values, *, local=False):
    path = root / ("config.yaml" if local else ".copilot-extensions/agent-ssh/config.yaml")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(values), encoding="utf-8")


def setup(tmp_path, monkeypatch, binding="bound"):
    base, knowledge, machine, local = (tmp_path / part for part in ("base", "knowledge", "machine", "local"))
    for root in (base, knowledge, machine, local):
        root.mkdir()
    path = base / "machines.yaml"
    path.write_text(yaml.safe_dump({"machines": {"logical": {
        "description": "native", "ssh": {"ready": True, "environments": [{"alias": "native-ssh"}]},
    }}}), encoding="utf-8")
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
    monkeypatch.setattr(configuration, "owner_report", lambda root: (report, machine))
    return path, base, knowledge, machine, local


def test_native_mesh_consumer_all_tiers_preserves_admission(tmp_path, monkeypatch):
    path, base, knowledge, machine, local = setup(tmp_path, monkeypatch)
    write(base, {"machines": {"logical": {"description": "base", "capabilities": ["base"]}}})
    write(knowledge, {"machines": {"logical": {"description": "knowledge", "capabilities": ["knowledge"]}}})
    write(machine, {"machines": {"logical": {"description": "machine"}}}, local=True)
    write(local, {"machines": {"logical": {"description": "local"}}})
    result = mesh.load_mesh(path, strict=True, preferences=True)
    assert result.machines[0].description == "local"
    assert result.machines[0].capabilities == ["knowledge"]
    assert result.machines[0].ssh_ready is True
    assert result.machines[0].environments[0].alias == "native-ssh"
    provenance = result.configuration_provenance["/machines/logical/description"]
    assert provenance["installation_cell"] == "cell"
    assert provenance["source"]["root"] == str(local)


def test_explicit_native_source_does_not_graft(tmp_path, monkeypatch):
    path, base, knowledge, machine, local = setup(tmp_path, monkeypatch)
    write(knowledge, {"machines": {"logical": {"description": "knowledge"}}})
    assert mesh.load_mesh(path, strict=True).machines[0].description == "native"


def test_unbound_and_unresolved_context_are_distinct(tmp_path, monkeypatch):
    path, base, knowledge, machine, local = setup(tmp_path, monkeypatch, "unbound")
    write(knowledge, {"machines": {"logical": {"description": "excluded"}}})
    assert mesh.load_mesh(path, strict=True, preferences=True).machines[0].description == "native"
    report, runtime = configuration.owner_report(base)
    report["context"]["binding"] = "unresolved"
    with pytest.raises(ConfigurationError, match="unresolved"):
        mesh.load_mesh(path, strict=True, preferences=True)


@pytest.mark.parametrize("protected", ["ssh", "hostname", "role", "dtssh", "authority"])
def test_preferences_refuse_protected_native_fields(tmp_path, monkeypatch, protected):
    path, base, knowledge, machine, local = setup(tmp_path, monkeypatch)
    write(knowledge, {"machines": {"logical": {protected: {}}}})
    with pytest.raises(ValueError, match="topology or transport"):
        mesh.load_mesh(path, strict=True, preferences=True)


def test_default_cli_producer_requests_preferences_but_explicit_path_does_not(tmp_path, monkeypatch, capsys):
    from agent_ssh import __main__ as cli

    path, base, knowledge, machine, local = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(cli.fragment_registry.FragmentRegistry, "refresh", lambda self: None)
    monkeypatch.setattr(mesh, "find_machines_file", lambda: path)
    write(knowledge, {"machines": {"logical": {"description": "knowledge"}}})
    args = argparse.Namespace(path=None, config_d=tmp_path / "fragments", json=True, summary=False)
    assert cli._cmd_mesh_status(args) == 0
    assert '"description": "knowledge"' in capsys.readouterr().out
    args.path = path
    assert cli._cmd_mesh_status(args) == 0
    assert '"description": "native"' in capsys.readouterr().out


def test_actual_absent_owner_preserves_unbound_mesh_without_process(tmp_path, monkeypatch):
    import configuration_context

    for name in ("COPILOT_EXTENSIONS_CONTEXT", "AGENT_CONFIGURATION_CONTEXT"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "machines.yaml"
    path.write_text("machines:\n  standalone:\n    description: native\n", encoding="utf-8")

    def no_process(*args, **kwargs):
        pytest.fail("standalone mesh presentation must not bootstrap an optional owner")

    monkeypatch.setattr(configuration_context.subprocess, "run", no_process)
    result = mesh.load_mesh(path, strict=True, preferences=True)
    assert result.machines[0].description == "native"
    assert sorted(item.name for item in tmp_path.iterdir()) == ["machines.yaml"]


def test_native_unready_legacy_owner_preserves_mesh_without_process(tmp_path, monkeypatch):
    import shutil
    from pathlib import Path
    import configuration_context

    home = tmp_path / "home"
    scripts = home / ".agent-ssh" / "scripts"
    scripts.mkdir(parents=True)
    for source in (Path(configuration.__file__).parents[2] / "scripts").glob("versioned_runtime*.py"):
        shutil.copy2(source, scripts / source.name)
    peer = home / ".agent-worktrees"
    fake_python = peer / "versions" / "1.0.0" / "Scripts" / "python.exe"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text("not a completed runtime", encoding="utf-8")
    (peer / "current-version").write_text("1.0.0", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: home)
    for name in ("AGENT_RT_ROOT", "COPILOT_EXTENSIONS_CONTEXT", "AGENT_CONFIGURATION_CONTEXT"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "machines.yaml"
    path.write_text("machines:\n  standalone:\n    description: native\n", encoding="utf-8")

    def no_process(*args, **kwargs):
        pytest.fail("an unready optional owner must not bootstrap")

    monkeypatch.setattr(configuration_context.subprocess, "run", no_process)
    assert mesh.load_mesh(path, strict=True, preferences=True).machines[0].description == "native"
    assert (scripts / "versioned_runtime.py").is_file()
    assert configuration._legacy_report(tmp_path) is None


@pytest.mark.parametrize("implicit", [False, True])
@pytest.mark.parametrize("matching_cell", [False, True])
def test_real_mesh_loader_checks_delivered_cell_against_actual_owner(tmp_path, monkeypatch, implicit, matching_cell):
    import json

    real_report = configuration.owner_report
    path, base, knowledge, machine, local = setup(tmp_path, monkeypatch)
    report, machine_root = configuration.owner_report(base)
    monkeypatch.setattr(configuration, "owner_report", real_report)
    write(knowledge, {"machines": {"logical": {"description": "knowledge"}}})
    cell = tmp_path / "marketplaces" / "current-cell"
    own_root = cell / "plugins" / "agent-ssh"
    own_root.mkdir(parents=True)
    report["context"]["installation_cell"] = str(cell if matching_cell else tmp_path / "foreign-cell")
    calls = []

    def validate(owner, root, raw):
        calls.append((owner, root, raw))
        assert root == own_root
        assert raw == str(own_root / "install.json")
        return {"cellRoot": str(cell), "pluginRoot": str(own_root)}

    monkeypatch.setattr(configuration._worktrees, "validate_owner", validate)
    monkeypatch.setenv("AGENT_RT_ROOT", str(own_root))
    if implicit:
        monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    else:
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(own_root / "install.json"))
    monkeypatch.setenv("AGENT_CONFIGURATION_CONTEXT", json.dumps(report))
    if matching_cell:
        assert mesh.load_mesh(path, strict=True, preferences=True).machines[0].description == "knowledge"
    else:
        with pytest.raises(ConfigurationError, match="another installation cell"):
            mesh.load_mesh(path, strict=True, preferences=True)
    assert calls

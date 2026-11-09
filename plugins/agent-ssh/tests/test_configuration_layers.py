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
    monkeypatch.chdir(base)
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
    monkeypatch.setattr(mesh, "_find_machines_source", lambda start=None: (base, path))
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
    import subprocess

    for name in ("COPILOT_EXTENSIONS_CONTEXT", "AGENT_CONFIGURATION_CONTEXT"):
        monkeypatch.delenv(name, raising=False)
    path = tmp_path / "machines.yaml"
    path.write_text("machines:\n  standalone:\n    description: native\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    def no_process(*args, **kwargs):
        if args[0][0] == "git":
            return subprocess.CompletedProcess(args[0], 0, str(tmp_path), "")
        pytest.fail("standalone mesh presentation must not bootstrap an optional owner")

    monkeypatch.setattr(configuration_context.subprocess, "run", no_process)
    result = mesh.load_mesh(path, strict=True, preferences=True)
    assert result.machines[0].description == "native"
    assert sorted(item.name for item in tmp_path.iterdir()) == ["machines.yaml"]


def test_native_unready_legacy_owner_preserves_mesh_without_process(tmp_path, monkeypatch):
    import os
    import shutil
    from pathlib import Path

    home = tmp_path / "home"
    scripts = home / ".agent-ssh" / "bin"
    scripts.mkdir(parents=True)
    suffix = "ps1" if os.name == "nt" else "sh"
    resolver = Path(configuration.__file__).parents[2] / "scripts" / f"resolve-runtime.{suffix}"
    shutil.copy2(resolver, scripts / resolver.name)
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
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(mesh, "_find_machines_source", lambda start=None: (tmp_path, path))

    assert mesh.load_mesh(path, strict=True, preferences=True).machines[0].description == "native"
    assert not (home / ".agent-ssh" / "scripts").exists()
    assert not (peer / "logs").exists()
    assert configuration._legacy_report(tmp_path) is None


@pytest.mark.parametrize("outcome", ["matching", "repository-mismatch", "cell-mismatch", "refused", "cell-owned"])
def test_deployed_legacy_resolver_requalifies_delivered_presentation(tmp_path, monkeypatch, outcome):
    import copy
    import json
    import os
    import shlex
    import shutil
    import sys
    from pathlib import Path

    if os.name == "nt":
        pytest.skip("real POSIX installed-layout actor; Windows transport has its own contract test")
    home = tmp_path / "home"
    own = home / ".agent-ssh"
    peer = home / ".agent-worktrees"
    (own / "bin").mkdir(parents=True)
    shutil.copyfile(
        Path(configuration.__file__).parents[2] / "scripts/resolve-runtime.sh",
        own / "bin/resolve-runtime.sh",
    )
    python = peer / "versions/1.2.3/bin/python"
    python.parent.mkdir(parents=True)
    (peer / "current-version").write_text("1.2.3")
    (python.parent.parent / ".install-complete.json").write_text(
        '{"version": "1.2.3", "completed_at": "2026-01-01", "pid": 0}',
    )
    report = {"schema": "agent-worktrees.configuration", "version": 1, "context": {
        "repository_id": "selected-repository", "execution_machine": "worker",
        "installation_cell": "selected-cell", "binding": "unbound", "base_root": str(tmp_path),
    }}
    fresh = copy.deepcopy(report)
    if outcome == "repository-mismatch":
        fresh["context"]["repository_id"] = "foreign-repository"
    if outcome == "cell-mismatch":
        fresh["context"]["installation_cell"] = "foreign-cell"
    actor = tmp_path / "owner.py"
    actor.write_text(
        "import json, os, sys\n"
        "assert sys.argv[1:] == ['-I', '-X', 'utf8', '-B', '-m', 'agent_worktrees', "
        "'state-root', '--configuration', '--json']\n"
        "assert not any(key in os.environ for key in "
        "['AGENT_CONFIGURATION_CONTEXT', 'AGENT_RT_ROOT', 'PYTHONPATH', 'GITHUB_TOKEN'])\n"
        f"sys.exit(3) if {outcome == 'refused'!r} else None\n"
        f"print(json.dumps({fresh!r}))\n",
    )
    python.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} -I {shlex.quote(str(actor))} \"$@\"\n")
    python.chmod(0o700)
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setenv("AGENT_RT_ROOT", str(own))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "synthetic-must-not-reach-peer")
    monkeypatch.setenv("AGENT_CONFIGURATION_CONTEXT", json.dumps(report))
    if outcome == "cell-owned":
        (peer / ".installation-ownership.json").write_text("{}")
        with pytest.raises(configuration._peer_launch.ContextRefused, match="installation-cell ownership"):
            configuration.owner_report(tmp_path)
    elif outcome == "matching":
        assert configuration.owner_report(tmp_path) == (fresh, own)
    else:
        with pytest.raises(ConfigurationError, match="refused|matching qualified owner"):
            configuration.owner_report(tmp_path)
    assert not (peer / "logs").exists()
    assert not (own / "scripts").exists()


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


@pytest.mark.parametrize("folder", [".agent-worktrees", "config", ".github"])
@pytest.mark.parametrize("git_root", [False, True])
def test_native_mesh_loader_keeps_repository_root_for_nested_conventional_source(
    tmp_path, monkeypatch, folder, git_root,
):
    import json
    import subprocess

    base, knowledge = tmp_path / "repository", tmp_path / "knowledge"
    base.mkdir()
    knowledge.mkdir()
    path = base / folder / "machines.yaml"
    path.parent.mkdir()
    path.write_text("machines:\n  worker:\n    description: native\n", encoding="utf-8")
    write(knowledge, {"machines": {"worker": {"description": "knowledge"}}})
    report = {
        "schema": "agent-worktrees.configuration", "version": 1,
        "context": {
            "repository_id": "repo-qualified", "execution_machine": "worker",
            "installation_cell": "legacy-cell", "binding": "bound",
            "base_root": str(base), "knowledge_root": str(knowledge),
        },
    }
    for name in ("AGENT_RT_ROOT", "COPILOT_EXTENSIONS_CONTEXT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENT_CONFIGURATION_CONTEXT", json.dumps(report))
    monkeypatch.setattr(configuration._worktrees, "validate_context", lambda: {
        "cellRoot": "legacy-cell", "pluginRoot": str(base),
    })
    monkeypatch.chdir(path.parent if git_root else base)

    def selected_root(argv, **kwargs):
        assert argv[0] == "git"
        return subprocess.CompletedProcess(argv, 0 if git_root else 1, str(base), "")

    monkeypatch.setattr(mesh.subprocess, "run", selected_root)
    result = mesh.load_mesh(path, strict=True, preferences=True)
    assert result.machines[0].description == "knowledge"
    assert result.configuration_provenance["/machines/worker/description"]["source"]["root"] == str(knowledge)

    def no_selection(*args, **kwargs):
        pytest.fail("an explicit source must not trigger repository preference selection")

    monkeypatch.setattr(mesh, "_find_machines_source", no_selection)
    assert mesh.load_mesh(path, strict=True).machines[0].description == "native"


def test_preference_loader_refuses_an_unrelated_selection_root(tmp_path, monkeypatch):
    import subprocess

    selected, unrelated = tmp_path / "selected", tmp_path / "unrelated"
    selected.mkdir()
    unrelated.mkdir()
    path = selected / "config" / "machines.yaml"
    path.parent.mkdir()
    path.write_text("machines: {worker: {description: native}}\n", encoding="utf-8")
    monkeypatch.chdir(unrelated)
    monkeypatch.setattr(mesh.subprocess, "run", lambda argv, **kwargs:
                        subprocess.CompletedProcess(argv, 0, str(unrelated), ""))
    with pytest.raises(ValueError, match="selected topology repository root"):
        mesh.load_mesh(path, strict=True, preferences=True)


@pytest.mark.parametrize("code,diagnostic", [
    (2, "unrecognized arguments: --configuration"),
    (2, "unknown option: --configuration"),
    (1, "binding refused"), (3, "binding unresolved"), (126, "owner refused"),
    (2, "unrecognized arguments: --unrelated"), (0, "malformed"),
])
def test_optional_older_peer_preserves_native_mesh_or_fails_real_refusal(tmp_path, monkeypatch, code, diagnostic):
    import subprocess
    real_report = configuration.owner_report
    path, base, knowledge, machine, local = setup(tmp_path, monkeypatch)
    monkeypatch.setattr(configuration, "owner_report", real_report)
    cell = tmp_path / "marketplaces" / "current-cell"
    own = cell / "plugins" / "agent-ssh"
    (cell / "plugins" / "agent-worktrees").mkdir(parents=True)
    monkeypatch.setenv("AGENT_RT_ROOT", str(own))
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(own / "install.json"))
    monkeypatch.delenv("AGENT_CONFIGURATION_CONTEXT", raising=False)
    monkeypatch.setattr(configuration._worktrees, "validate_owner", lambda *args:
                        {"cellRoot": str(cell), "pluginRoot": str(own)})
    monkeypatch.setattr(configuration._peer_launch, "launch_prefix", lambda *args: ["qualified-peer"])
    calls = []

    def run(argv, **kwargs):
        if argv[0] == "git":
            return subprocess.CompletedProcess(argv, 0, str(base), "")
        assert argv == ["qualified-peer", "state-root", "--configuration", "--json"]
        calls.append(argv)
        return subprocess.CompletedProcess(argv, code, b"{bad-json", diagnostic.encode())

    monkeypatch.setattr(subprocess, "run", run)
    if code == 2 and diagnostic.endswith("--configuration"):
        assert mesh.load_mesh(path, strict=True, preferences=True).machines[0].description == "native"
    else:
        with pytest.raises(ConfigurationError):
            mesh.load_mesh(path, strict=True, preferences=True)
    assert len(calls) == 1


def test_unqualified_delivery_cannot_be_admitted_by_optional_absence(tmp_path, monkeypatch):
    import json
    real_report = configuration.owner_report
    path, base, knowledge, machine, local = setup(tmp_path, monkeypatch)
    report, machine_root = configuration.owner_report(base)
    monkeypatch.setattr(configuration, "owner_report", real_report)
    for name in ("COPILOT_EXTENSIONS_CONTEXT", "AGENT_RT_ROOT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENT_CONFIGURATION_CONTEXT", json.dumps(report))
    monkeypatch.setattr(configuration, "_legacy_report", lambda root: None)
    with pytest.raises(ConfigurationError, match="matching qualified owner"):
        mesh.load_mesh(path, strict=True, preferences=True)
